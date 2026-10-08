#!/usr/bin/env python3
"""Compare an uninstrumented Quake2 candidate with the deployed lightmap build.

Reuses the immutable pass-one qsim and selected-GPU replay artifacts. This
performs no build, simulation, product edit, or deployment. Rendering parity
and modeled performance are separate result fields; neither proves Pocket FPS.
"""
from pathlib import Path
import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import struct

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / 'docs/measurements/quake2-optimize-20261008'
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008'
SIM = OLD / 'demo-screen-v2'
GPU = OLD / 'rtl-demo-depth-v2'
FRAMES = (20, 80, 180, 300)
OLD_COMPARE = ROOT / 'tools/experiments/quake2_optimize_20261008/compare_demo.py'
PINS = {
    OLD_COMPARE: '10447e556e61774bc98e9332ffba06c13e3b3a58a6844f69ac5d1777ef737236',
    SIM / 'prepared.json': 'e880e8491721be104008e754b9c9def1b54d3e7abb870d0acce06b7e14cd98a9',
    SIM / 'qsim/qsim': '87b3d723050f9abdc2da0b4597dfd772032a3f25ec0c5a691289ba84e5df92bb',
    GPU / 'prepared.json': '592e05fb85ae4af609c15172c5966f0379afe89d8aa3efd7809cc9522d531e8f',
    GPU / 'built.json': '57231c17fd1e12a4bcf00c99ba0ac4120d480c867d78139ab964f3cc64932062',
    GPU / 'obj/Vtb_gpu': 'eedeb2f6bc8e556c7f3c5acc150d3603584e0fec64d516279091664ae16a6fe2',
    SIM / 'lightmap.elf': '6f4ec790371d6a610976fa72d220df6636cfb5fbccf36284f028dc4893bc9959',
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def put(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')


def records(path):
    """Strictly parse the immutable GPU-vector envelope, including its end."""
    data = Path(path).read_bytes()
    pos = 0
    result = []
    ended = False
    while pos < len(data):
        start = pos
        require(pos + 4 <= len(data), f'{path}: truncated tag')
        tag = data[pos:pos + 4]
        pos += 4
        if tag in (b'MEM1', b'TEX1'):
            require(pos + 8 <= len(data), f'{path}: truncated memory envelope')
            _, size = struct.unpack_from('<II', data, pos)
            pos += 8 + ((size + 3) & ~3)
        elif tag == b'CMDS':
            require(pos + 4 <= len(data), f'{path}: truncated command envelope')
            count = struct.unpack_from('<I', data, pos)[0]
            pos += 4 + 4 * count
        elif tag in (b'MMIO', b'DUMP'):
            pos += 8
        elif tag == b'TLUT':
            pos += 32768
        elif tag == b'PALB':
            pos += 4
        elif tag == b'RAND':
            pos += 12
        elif tag in (b'FLSH', b'FENC', b'HASH'):
            pass
        elif tag == b'END.':
            require(pos == len(data), f'{path}: data after end')
            ended = True
        else:
            raise ValueError(f'{path}: unknown tag {tag!r} at {start}')
        require(pos <= len(data), f'{path}: truncated {tag!r}')
        result.append((tag, data[start:pos]))
    require(ended, f'{path}: no end marker')
    return result


def command_bytes(rows):
    return b''.join(raw[8:] for tag, raw in rows if tag == b'CMDS')


def distribution(values):
    values = sorted(values)
    return dict(mean=statistics.mean(values), median=statistics.median(values),
                p90=values[math.ceil(len(values) * .90) - 1],
                p95=values[math.ceil(len(values) * .95) - 1],
                maximum=values[-1], minimum=values[0])


def software(label, evidence):
    elf = SIM / (label + '.elf')
    report_path = SIM / (label + '-result.json')
    report = read(report_path)
    require(report['passed'], f'{label}: software run failed')
    require(report['prepared_sha256'] == PINS[SIM / 'prepared.json'],
            f'{label}: different harness preparation')
    require(report['elf_sha256'] == sha(elf), f'{label}: ELF hash mismatch')
    require(report['steady_frames'] == 340, f'{label}: wrong steady interval')
    evidence.update((elf, report_path))
    full_rows = None
    for suffix, frames in (('-startup', 20), ('', 362)):
        folder = SIM / (label + suffix)
        summary = read(folder / 'summary.json')
        require(summary['elf_sha256'] == sha(elf), f'{label}{suffix}: wrong ELF')
        require(summary['frames'] == frames and summary['stop_reason'] == 'frame limit',
                f'{label}{suffix}: incomplete frame run')
        require(summary['gpu_sched'] == 'eager' and summary['mem_mode'] == 'flat',
                f'{label}{suffix}: workload options differ')
        require(summary['exe_dates_normalized'] == 0, f'{label}{suffix}: ELF patched')
        require(summary['env']['unmapped_mmio'] == 0, f'{label}{suffix}: unmapped MMIO')
        require(summary['gpu_sync']['ring_overflows'] == 0, f'{label}{suffix}: ring overflow')
        require(summary['gpu_sync']['queued_words_at_end'] == 0,
                f'{label}{suffix}: unretired commands')
        require(summary['fpu_subnormal'] == dict(operands=0, results=0),
                f'{label}{suffix}: changed subnormal behavior')
        require('GEOMETRY=0' in (folder / 'console.log').read_text(),
                f'{label}{suffix}: incorrect geometry route')
        rows = list(csv.DictReader((folder / 'frames.csv').open()))
        require(len(rows) == frames, f'{label}{suffix}: incomplete frame records')
        evidence.update(folder / name for name in ('summary.json', 'frames.csv', 'console.log'))
        evidence.add(SIM / (label + suffix + '.log'))
        if not suffix:
            full_rows = rows
    metrics = {key: distribution([int(row[key]) for row in full_rows[20:360]])
               for key in ('insns', 'est_cycles', 'gpu_cmd_words')}
    for metric, field in (('insns', 'instructions_per_frame'),
                          ('est_cycles', 'modeled_cycles_per_frame'),
                          ('gpu_cmd_words', 'command_words_per_frame')):
        require(abs(metrics[metric]['mean'] - report[field]) < 1e-6,
                f'{label}: inconsistent {field}')
    evidence.add(SIM / (label + '-steady-profile.json'))
    return full_rows, metrics


def replay(label, frame, evidence):
    name = f'frame_{frame:05}'
    folder = GPU / label
    source = SIM / label / 'gpuvec' / (name + '.vec')
    filtered = folder / (name + '.vec')
    report = read(folder / (name + '.json'))
    filt = read(folder / (name + '.filter.json'))
    require(report['passed'] and report['frame'] == frame, f'{label}/{name}: failed replay')
    require(filt['raw_sha256'] == sha(source) and filt['filtered_sha256'] == sha(filtered),
            f'{label}/{name}: vector hash mismatch')
    require(filt['command_order_and_payload_unchanged'], f'{label}/{name}: commands edited')
    require(report['command'][0] == str(GPU / 'obj/Vtb_gpu'), f'{label}/{name}: wrong GPU')
    require(report['command'][1:4] == ['+fast_tex=1', '+tagged_tex=1', '+memory_traffic=0'],
            f'{label}/{name}: replay options differ')
    raw_records = records(source)
    filtered_records = records(filtered)
    require(command_bytes(raw_records) == command_bytes(filtered_records),
            f'{label}/{name}: filter changed command words/order')
    preserved = {b'CMDS', b'MMIO', b'TEX1', b'TLUT', b'PALB'}
    payload = [raw for tag, raw in raw_records if tag in preserved]
    for tag in preserved - {b'CMDS'}:
        require([raw for t, raw in raw_records if t == tag] ==
                [raw for t, raw in filtered_records if t == tag],
                f'{label}/{name}: filter changed {tag!r}')
    binary = folder / (name + '.bin')
    require(report['output_sha256'] == sha(binary), f'{label}/{name}: replay output changed')
    data = binary.read_bytes()
    stride, step = filt['zstride'], filt['zstep']
    require(stride >= 320 * step and step >= 2, f'{label}/{name}: invalid depth layout')
    size = stride * 240
    require(len(data) == 153612 + 2 * size, f'{label}/{name}: truncated output')
    depth = b''.join(data[y * stride + x * step:y * stride + x * step + 2]
                     for y in range(240) for x in range(320))
    require(hashlib.sha256(depth).hexdigest() == report['depth_sha256'],
            f'{label}/{name}: depth hash mismatch')
    require(hashlib.sha256(data[size:size + 153600]).hexdigest() == report['color_sha256'],
            f'{label}/{name}: color hash mismatch')
    nonzero = sum(depth[i:i + 2] != b'\0\0' for i in range(0, len(depth), 2))
    require(nonzero > 0 and nonzero == report['rendered_depth_nonzero_pixels'],
            f'{label}/{name}: trivial or invalid rendered depth')
    require(report['final_depth_clear'] and not any(data[-size:]),
            f'{label}/{name}: final depth clear differs')
    stats = {key: int(value) for key, value in
             re.findall(r'(\w+)=(\d+)', (folder / (name + '.log')).read_text())}
    require(stats == report['stats'] and stats['cram_errors'] == 0,
            f'{label}/{name}: stats/error mismatch')
    evidence.add(source)
    evidence.update(folder / (name + suffix) for suffix in
                    ('.vec', '.filter.json', '.bin', '.json', '.log'))
    return report, payload


def compare(candidate, baseline='lightmap', output_name=None):
    for label in (candidate, baseline):
        require(re.fullmatch(r'[A-Za-z0-9_-]+', label), f'invalid label {label!r}')
    require(candidate != baseline, 'candidate and baseline must be different labels')
    output_name = output_name or f'comparison-{candidate}-vs-{baseline}-v1'
    require(re.fullmatch(r'[A-Za-z0-9_-]+', output_name), 'invalid output name')
    output = OUT / output_name
    require(not output.exists(), f'refusing to replace existing evidence {output}')
    evidence = set(PINS)
    evidence.add(Path(__file__).resolve())
    for path, expected in PINS.items():
        require(sha(path) == expected, f'immutable input changed: {path}')
    raw, metrics, proofs = {}, {}, []
    for label in (baseline, candidate):
        raw[label], metrics[label] = software(label, evidence)
        rtl_summary = read(GPU / label / 'result.json')
        require(rtl_summary['passed'] and
                rtl_summary['prepared_sha256'] == PINS[GPU / 'prepared.json'],
                f'{label}: wrong RTL fixture')
        require([row['frame'] for row in rtl_summary['frames']] == list(FRAMES),
                f'{label}: missing/duplicate RTL frames')
        evidence.add(GPU / label / 'result.json')
    for key in ('frame', 'host_framecount', 'buffer', 'gpu_cmd_words',
                'hash_cmd', 'hash_px', 'hash_z', 'hash_pal'):
        require([row[key] for row in raw[baseline]] == [row[key] for row in raw[candidate]],
                f'362-frame mismatch: {key}')
    for frame in FRAMES:
        a, ap = replay(baseline, frame, evidence)
        b, bp = replay(candidate, frame, evidence)
        require(ap == bp, f'frame {frame}: command/texture/MMIO records differ')
        for key in ('color_sha256', 'depth_sha256', 'rendered_depth_nonzero_pixels'):
            require(a[key] == b[key], f'frame {frame}: {key} differs')
        require(a['stats']['cycles'] == b['stats']['cycles'], f'frame {frame}: GPU cycles differ')
        proofs.append(dict(frame=frame, color_sha256=a['color_sha256'],
                           rendered_depth_sha256=a['depth_sha256'],
                           rendered_depth_nonzero_pixels=a['rendered_depth_nonzero_pixels'],
                           final_depth_is_cleared=True, rtl_cycles=a['stats']['cycles'],
                           commands_textures_mmio_exact=True))
    require(len({proof['rendered_depth_sha256'] for proof in proofs}) == len(FRAMES),
            'sampled depth captures are not distinct')
    base_cycles = metrics[baseline]['est_cycles']['mean']
    saved = base_cycles - metrics[candidate]['est_cycles']['mean']
    result = dict(passed=True, rendering_equivalent=True, performance_improved=saved > 0,
                  baseline_label=baseline, candidate_label=candidate,
                  baseline_elf_sha256=sha(SIM / (baseline + '.elf')),
                  candidate_elf_sha256=sha(SIM / (candidate + '.elf')),
                  software_frame_comparisons=362, steady_frame_indices=[20, 359],
                  steady_denominator=340, metrics=metrics,
                  modeled_cycles_saved_per_frame=saved,
                  modeled_cpu_percent_saved=100 * saved / base_cycles,
                  modeled_ms_saved_at_configured_100MHz=saved / 100000,
                  sampled_rtl_pairs=proofs, exact_current_hw_features='0x2b136753',
                  geometry_enabled=False, texture_bytes=16777216,
                  limits=[
                      'Scalar qsim cycles with I64/D64 capacities are not actual selected Vexii cycles or Pocket FPS.',
                      'Fixed30Hz sound-disabled public demo; no real kernel/audio/CPU-GPU memory contention.',
                      'Functional qsim texture uploads are instantaneous; selected GPU replay uses physical16MiB CRAM.',
                      'GPU replay excludes CPU framebuffer snapshots below0x300000; CPU overlays retain exact qsim hashes.',
                      'Pre-clear depth readback drains preceding commands equally on both sides, changing synthetic scheduling.',
                      'Four GPU samples do not cover every scene; full SDRAM hashes can differ because ELF code differs.',
                  ])
    output.mkdir(parents=True, exist_ok=False)
    put(output / 'result.json', result)
    evidence.add(output / 'result.json')
    put(output / 'manifest.json', dict(passed=True, previous_compare_sha256=PINS[OLD_COMPARE],
                                      evidence_sha256={str(path.relative_to(ROOT)): sha(path)
                                                       for path in sorted(evidence)}))
    print(json.dumps(dict(output=str(output), manifest_sha256=sha(output / 'manifest.json'),
                          rendering_equivalent=True, performance_improved=saved > 0,
                          modeled_cpu_percent_saved=result['modeled_cpu_percent_saved']), indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('candidate')
    parser.add_argument('--baseline', default='lightmap')
    parser.add_argument('--output-name')
    args = parser.parse_args()
    compare(args.candidate, args.baseline, args.output_name)
