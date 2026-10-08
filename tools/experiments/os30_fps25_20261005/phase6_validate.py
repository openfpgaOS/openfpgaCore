#!/usr/bin/env python3
"""Validate the F8 per-batch command-transfer diagnostic against frozen F7.

The CPU harness models execution cost and drains the GPU eagerly. Retained-A
RTL replay separately checks pixels/depth under synthetic memory traffic;
neither result is a physical Pocket frame-rate measurement.
"""
import argparse
import concurrent.futures
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT, PRIOR = CAMP / 'phase6', CAMP / 'phase5'
RTL = CAMP / 'phase4/texmem/extended/model'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, obj):
    with path.open('x') as output:
        output.write(json.dumps(obj, indent=2) + '\n')


def rows(path):
    with path.open() as source:
        return list(csv.DictReader(source))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def simulator():
    prior = read(PRIOR / 'qsim-manifest.json')
    exe = PRIOR / 'qsim/qsim'
    assert sha(exe) == prior['simulator_sha256']
    for name, digest in prior['sources'].items():
        assert sha(PRIOR / 'qsim' / name) == digest, name
    assert sha(PRIOR / 'QUALIFIED.json') == read(OUT / 'baseline.json')['parent_qualified_sha256']
    return exe, prior


def prepare_sim():
    exe, prior = simulator()
    write(OUT / 'qsim-manifest.json', dict(
        simulator=str(exe), simulator_sha256=sha(exe),
        parent_manifest_sha256=sha(PRIOR / 'qsim-manifest.json'),
        unchanged_sources=prior['sources'],
        scope='Exact existing F7 executable/source; passive observer already supports39..64 snapshot words.'))


def verify_capture(path, digest, panel=False):
    info = read(path / 'inputs.json')
    exe, _ = simulator()
    assert info['success'] and info['panel'] == panel
    assert info['audio'] and not info['geometry_capability'] and not info['command_dma']
    assert info['hw_features'] == '0x2b136753'
    assert Path(info['command'][0]).resolve() == exe.resolve()
    assert info['command'][info['command'].index('--gpu-sched') + 1] == 'eager'
    assert sha(path / 'sm64.elf') == digest
    for name, expected in info['sha256'].items():
        assert sha(Path(name)) == expected, name
    for name, expected in info['results_sha256'].items():
        assert sha(path / name) == expected, (path, name)
    frame_rows = rows(path / 'frames.csv')
    assert len(frame_rows) == 721
    return frame_rows


def capture(panel=False):
    exe, _ = simulator()
    m = module('f8_capture', ROOT / 'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT = OUT
    sys.argv = [str(Path(__file__)), 'f8-panel' if panel else 'f8',
                '--elf', str(OUT / 'candidate/diagnostic.elf'), '--exe', str(exe),
                '--geometry', '0', '--audio']
    if panel:
        sys.argv += ['--panel', '--vectors', '640']
    m.main()


def vector(data):
    """Compare GPU-visible work while recording relocatable memory separately."""
    pos, hardware, textures, palettes, depths = 0, [], [], [], set()
    uploads, fence = 0, None
    while pos < len(data):
        start, tag = pos, data[pos:pos + 4]
        pos += 4
        if tag in (b'MEM1', b'TEX1'):
            address, count = struct.unpack_from('<II', data, pos)
            pos += 8 + ((count + 3) & ~3)
            if tag == b'TEX1':
                assert address % 4 == count % 4 == 0 and address + count <= 0x200000
                textures.append((address, data[start + 12:pos]))
        elif tag == b'CMDS':
            count, = struct.unpack_from('<I', data, pos)
            pos += 4
            end = pos + count * 4
            while pos < end:
                header, = struct.unpack_from('<I', data, pos)
                op, words = header >> 24, header & 8191
                body = struct.unpack_from(f'<{words}I', data, pos + 4)
                if op == 0x4a:
                    assert words == 17
                    if body[11]:
                        depths.add(body[11])
                elif op == 0x42:
                    fence = body[1]
                elif op == 0x02:
                    fence = body[0]
                pos += 4 * (words + 1)
            assert pos == end
        elif tag == b'MMIO':
            offset, _ = struct.unpack_from('<II', data, pos)
            assert offset in (0x08, 0x38, 0x3c)
            uploads += offset == 0x3c
            pos += 8
        elif tag == b'TLUT':
            pos += 32768
        elif tag == b'PALB':
            palettes.append(struct.unpack_from('<I', data, pos)[0])
            pos += 4
        elif tag == b'DUMP':
            pos += 8
        elif tag == b'RAND':
            pos += 12
        else:
            assert tag in (b'FLSH', b'FENC', b'HASH', b'END.'), tag
            if tag == b'END.':
                assert pos == len(data)
        assert pos <= len(data)
        if tag != b'MEM1':
            hardware.append(tag if tag == b'PALB' else data[start:start + 12]
                            if tag == b'TEX1' else data[start:pos])
    assert len(depths) == len(textures) == len(palettes) == 1 and fence is not None
    return dict(hardware=hardware, texture=textures[0][1], texture_base=textures[0][0],
                palettes=palettes, depth=next(iter(depths)), fence=fence, uploads=uploads)


def elf_data(path):
    data = path.read_bytes()
    assert data[:7] == b'\x7fELF\x01\x01\x01'
    header = struct.unpack_from('<HHIIIIIHHHHHH', data, 16)
    program_offset, section_offset = header[4:6]
    program_size, program_count, section_size, section_count = header[8:12]
    sections = [struct.unpack_from('<10I', data, section_offset + index * section_size)
                for index in range(section_count)]
    symbols = []
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings = sections[section[6]]
        table = data[strings[4]:strings[4] + strings[5]]
        for pos in range(section[4], section[4] + section[5], section[9]):
            name, address, size, _, _, _ = struct.unpack_from('<IIIBBH', data, pos)
            end = table.index(b'\0', name)
            symbols.append((table[name:end].decode(), address, size))

    def memory(address, count):
        for index in range(program_count):
            entry = struct.unpack_from('<8I', data, program_offset + index * program_size)
            kind, offset, virtual, _, size = entry[:5]
            if kind == 1 and virtual <= address and address + count <= virtual + size:
                return data[offset + address - virtual:offset + address - virtual + count]
        raise ValueError((path, hex(address), count))

    return symbols, memory


def star_padding_check(changes, before, current):
    """Explain only the known oversized star-load relocation; reject others."""
    source_rel = 'src/sm64/sm64/actors/star/model.inc.c'
    source = OUT / 'SM64' / source_rel
    context = read(PRIOR / 'star-uv-review.json')
    note = read(PRIOR / 'tex1-padding-note.json')
    assert sha(source) == note['source_sha256'] == sha(PRIOR / 'SM64' / source_rel)
    assert sha(OUT / 'candidate/gfx_pc.c') == sha(PRIOR / 'candidate/gfx_pc.c')
    allowed = {entry['offset'] for entry in context['differences']}
    assert allowed == {140188, 140236, 140260}
    for entry in changes:
        assert {difference['offset'] for difference in entry['texture_changes']} <= allowed, entry
    texture_base, texture_length, asset_size = 138048, 4096, 2048
    details, assets = [], []
    for label, directory in [('f7', before), ('f8', current)]:
        elf = directory / 'sm64.elf'
        symbols, memory = elf_data(elf)
        symbol = [value for value in symbols if value[0] == 'star_seg3_texture_0302A6F0']
        assert len(symbol) == 1 and symbol[0][2] == asset_size
        address = symbol[0][1]
        raw = memory(address, texture_length)
        assets.append(raw[:asset_size])
        converted = bytearray()
        for index in range(0, texture_length, 2):
            word = int.from_bytes(raw[index:index + 2], 'big')
            green = ((((word >> 6) & 31) * 1053) >> 7) >> 2
            color = ((word & 0xf800) | (green << 5) | ((word >> 1) & 31)) or 1
            converted.extend(struct.pack('<H', color if word & 1 else 0))
        for entry in changes:
            if entry['frame'] < 360:
                assert not entry['texture_changes']
                continue
            info = vector((directory / 'gpuvec' / f"frame_{entry['frame']:05d}.vec").read_bytes())
            assert info['texture'][texture_base:texture_base + texture_length] == converted
        covered = []
        for offset in sorted(allowed):
            relative = offset - texture_base
            assert asset_size <= relative < texture_length
            covers = [name for name, start, size in symbols if start <= address + relative < start + size]
            assert covers and all(name.startswith('mushroom_1up_seg3_dl_') for name in covers), covers
            covered.append(dict(texture_offset=offset, raw_address=address + relative, covering_symbols=covers,
                                texel_x=(relative // 2) % 32, texel_y=(relative // 2) // 32))
        details.append(dict(build=label, elf_sha256=sha(elf), asset_address=address,
                            oversized_4096_byte_conversion_matches=True, relocation_sites=covered))
    assert assets[0] == assets[1]
    return dict(passed=True, actual_star_asset_2048_bytes_exact=True, source_sha256=sha(source),
                unchanged_commands_retain_prior_uvs=True, allowed_offsets=sorted(allowed), builds=details,
                prior_uv_context_sha256=sha(PRIOR / 'star-uv-review.json'),
                prior_padding_explanation_sha256=sha(PRIOR / 'tex1-padding-note.json'),
                limit='Changed texels are rows33/34 of a32x64 upload containing a32x32 asset followed by linked display lists. Retained identical command streams have prior captured V<=31.1875; selected RTL color/depth replay is the output gate, not a claim of full texture identity or universal UV proof.')


def snapshot_checks(directory, panel=False):
    data = rows(directory / 'diagnostic.csv')
    assert len(data) == 721
    words = len(data[0]) - 1
    assert words == 55, words  # Final F8 append-only ABI, checked again against source.
    valid, nonzero_transfer = 0, 0
    for row in data:
        s = [int(row[f'stat{i}']) for i in range(words)]
        if not s[0]:
            continue
        valid += 1
        assert s[18] == 8 and s[19:39] == [0] * 20, row
        assert 0 <= s[10] - sum(s[39:43]) <= 3, row
        assert s[43] <= (s[3] + 15) // 16 and s[46] == 16, row
        assert s[44] + s[45] <= s[47], row
        if not s[43]:
            assert s[44] == s[45] == s[47] == 0, row
        assert s[48] + s[49] == s[39], row
        assert s[52] <= s[51] and s[53] in (0, 1, 2, 3), row
        assert s[54] == 0, row
        # Retained A has CPU command stores, without command-DMA capability.
        assert s[51] == s[52] and s[53] in (0, 1), row
        if s[50]:
            assert s[53] == 1, row
        nonzero_transfer += s[48] > 0
    assert valid
    assert nonzero_transfer
    result = dict(valid_rows=valid, snapshot_words=words, partition_and_sample_invariants=True,
                  nonzero_copy_rows=nonzero_transfer, copy_plus_rest_equals_host=True,
                  retained_a_cpu_submit_mode_only=True, invalid_frames=0)
    if panel:
        values = [[row[f'stat{i}'] for i in range(words)] for row in data]
        assert all(value == values[600] for value in values[600:680])
        result['all_words_frozen_frames600_through679'] = True
    return result


def analyze():
    base = read(OUT / 'baseline.json')
    before, current = PRIOR / 'capture-f7', OUT / 'capture-f8'
    a = verify_capture(before, base['diagnostic_elf_sha256'])
    b = verify_capture(current, sha(OUT / 'candidate/diagnostic.elf'))
    exact = {name: sha(before / name) == sha(current / name)
             for name in ('scene.csv', 'geometry.csv', 'culling.csv')}
    assert all(exact.values()), exact
    assert [row['gpu_cmd_words'] for row in a] == [row['gpu_cmd_words'] for row in b]
    totals = {}
    for key in ('insns', 'est_cycles', 'gpu_cmd_words', 'icache_miss_sdram', 'dcache_miss'):
        x, y = (sum(int(row[key]) for row in data[360:]) for data in (a, b))
        totals[key] = dict(f7=x, f8=y, delta=y - x, percent_change=(y / x - 1) * 100)
    changes = []
    paths = sorted((before / 'gpuvec').glob('*.vec'))
    assert len(paths) == 8
    for path in paths:
        other = current / 'gpuvec' / path.name
        old, new = vector(path.read_bytes()), vector(other.read_bytes())
        assert old['hardware'] == new['hardware'], (path.name, 'Commands/MMIO/order/extents')
        assert old['depth'] == new['depth'] and old['fence'] == new['fence']
        assert old['uploads'] == new['uploads']
        assert len(old['texture']) == len(new['texture'])
        differing = [dict(offset=offset,
                          f7=struct.unpack_from('<I', old['texture'], offset)[0],
                          f8=struct.unpack_from('<I', new['texture'], offset)[0])
                     for offset in range(0, len(old['texture']), 4)
                     if old['texture'][offset:offset + 4] != new['texture'][offset:offset + 4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]),
                            f7_vector_sha256=sha(path), f8_vector_sha256=sha(other),
                            texture_changes=differing, f7_palette_base=old['palettes'][0],
                            f8_palette_base=new['palettes'][0], live_upload_words=new['uploads']))
    result = dict(passed=True, baseline_capture_inputs_sha256=sha(before / 'inputs.json'),
                  candidate_capture_inputs_sha256=sha(current / 'inputs.json'),
                  gameplay_geometry_culling_exact=exact, all_721_command_word_counts_exact=True,
                  all_eight_gpu_command_mmio_order_texture_extents_exact=True,
                  gameplay_frames360_through720=totals,
                  snapshot_checks=snapshot_checks(current), vectors=changes,
                  star_oversized_load_check=star_padding_check(changes, before, current),
                  scope='Execution-cost delta is modeled diagnostic overhead, not a device timing bound. Eager GPU cannot measure hardware backpressure.')
    write(OUT / 'analysis.json', result)
    print(json.dumps({key: value for key, value in result.items() if key != 'vectors'}, indent=2))


def replay():
    exe = RTL / 'obj/Vtb_gpu'
    assert sha(exe) == read(RTL / 'build.json')['simulator_sha256']
    current = OUT / 'capture-f8'
    verify_capture(current, sha(OUT / 'candidate/diagnostic.elf'))
    previous = {(row['traffic'], row['frame']): row for row in read(PRIOR / 'replay-fps.json')}
    adapt = module('f8_replay_adapter', Path(__file__).with_name('phase4_combined_validate.py'))

    def run(job):
        traffic, frame = job
        source = current / 'gpuvec' / f'frame_{frame:05d}.vec'
        directory = OUT / 'replays/fps' / str(traffic)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / source.stem
        assert not target.with_suffix('.log').exists(), target
        commands, depth, tags = adapt.with_depth(source, target.with_suffix('.vec'))
        assert depth
        command = [str(exe), str(target.with_suffix('.vec')), str(target.with_suffix('.bin')),
                   f'+memory_traffic={traffic}', '+tagged_tex=1']
        with target.with_suffix('.log').open('x') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        reference = PRIOR / 'replays/fps' / str(traffic) / (source.stem + '.bin')
        prior = previous[traffic, frame]
        assert sha(reference) == prior['output_sha256']
        assert sha(reference.with_suffix('.log')) == prior['log_sha256']
        result, old = target.with_suffix('.bin').read_bytes(), reference.read_bytes()
        assert len(result) == len(old) == 307212
        assert result[:153600] == old[:153600], ('Color', traffic, frame)
        assert result[153612:] == old[153612:], ('Depth', traffic, frame)
        info = vector(source.read_bytes())
        assert struct.unpack_from('<I', result, 153600)[0] == info['fence']
        stats = {key: int(value) for key, value in re.findall(r'(\w+)=(\d+)', target.with_suffix('.log').read_text())}
        assert stats == prior['stats'], (traffic, frame, stats, prior['stats'])
        assert stats['cram_errors'] == 0 and stats['words'] == stats['readback_words'] == info['uploads']
        return dict(traffic=traffic, frame=frame, color_depth_exact=True, fence_exact=True,
                    all_gpu_statistics_exact_to_f7=True, upload_readback_exact=True,
                    stats=stats, commands=commands, tags=tags, command=command,
                    simulator_sha256=sha(exe), vector_sha256=sha(source),
                    adapted_vector_sha256=sha(target.with_suffix('.vec')),
                    output_sha256=sha(target.with_suffix('.bin')), log_sha256=sha(target.with_suffix('.log')),
                    reference_output_sha256=sha(reference))

    jobs = [(traffic, frame) for traffic in (1, 2) for frame in (360, 600, 720)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, jobs))
    write(OUT / 'replay-fps.json', results)
    print(json.dumps(dict(passed=True, comparisons=len(results), all_color_depth_statistics_exact=True)))


def review():
    """Bind independent source review to final builds and validation outputs."""
    base = read(OUT / 'baseline.json')
    sources = {name: sha(OUT / 'candidate' / name) for name in base['inputs']}
    normal, diagnostic = (OUT / 'candidate' / name for name in ('app.elf', 'diagnostic.elf'))
    assert sha(normal) == base['normal_elf_sha256']
    for mode, executable in [('normal', normal), ('diagnostic', diagnostic)]:
        build = read(OUT / 'candidate' / f'build-{mode}.json')
        assert build['returncode'] == 0 and build['elf_sha256'] == sha(executable)
        assert {name: entry['sha256'] for name, entry in build['inputs'].items()} == sources
        for name, entry in build['inputs'].items():
            assert Path(entry['source']) == OUT / 'candidate' / name
            assert sha(Path(entry['source'])) == entry['sha256']
    tests = read(OUT / 'perf-tests.json')
    assert tests['passed'] and tests['snapshot_words'] == 55
    assert tests['script_sha256'] == sha(Path(__file__).with_name('phase6_perf_tests.py'))
    assert all(sources[name] == digest for name, digest in tests['source_sha256'].items())
    transport = read(OUT / 'transport-tests/results.json')
    assert transport['passed'] and transport['command_poll_and_state_order_identical']
    assert transport['rv32_off_text_identical'] and transport['scenarios_per_configuration'] == 209
    assert transport['header_sha256'] == sources['of_gpu.h']
    assert transport['app_sha256'] == sources['gfx_gpu.c']
    assert transport['script_sha256'] == sha(Path(__file__).with_name('phase6_transport_tests.py'))
    for entry in transport['runs']:
        for suffix, key in [('.c', 'source_sha256'), ('', 'binary_sha256'), ('.log', 'log_sha256')]:
            assert sha(OUT / 'transport-tests' / (entry['name'] + suffix)) == entry[key]
    analysis = read(OUT / 'analysis.json')
    assert analysis['passed'] and analysis['star_oversized_load_check']['passed']
    for directory, digest, panel in [(PRIOR / 'capture-f7', base['diagnostic_elf_sha256'], False),
                                     (OUT / 'capture-f8', sha(diagnostic), False),
                                     (OUT / 'capture-f8-panel', sha(diagnostic), True)]:
        verify_capture(directory, digest, panel)
    snapshot_checks(OUT / 'capture-f8')
    snapshot_checks(OUT / 'capture-f8-panel', panel=True)
    replays = read(OUT / 'replay-fps.json')
    assert {(row['traffic'], row['frame']) for row in replays} == {(t, f) for t in (1, 2) for f in (360, 600, 720)}
    for entry in replays:
        stem = f"frame_{entry['frame']:05d}"
        dest = OUT / 'replays/fps' / str(entry['traffic']) / stem
        for suffix, key in [('.vec', 'adapted_vector_sha256'), ('.bin', 'output_sha256'), ('.log', 'log_sha256')]:
            assert sha(dest.with_suffix(suffix)) == entry[key]
        assert sha(OUT / 'capture-f8/gpuvec' / (stem + '.vec')) == entry['vector_sha256']
        assert all(entry[key] for key in ('color_depth_exact', 'fence_exact',
                                          'all_gpu_statistics_exact_to_f7', 'upload_readback_exact'))
    panel = read(OUT / 'panel/result.json')
    for name, key in [('frame.bin', 'output_sha256'), ('panel.png', 'image_sha256'), ('frame_00640.vec', 'vector_sha256')]:
        assert sha(OUT / 'panel' / name) == panel[key]
    scripts = ['phase6_validate.py', 'phase6_panel.py', 'phase6_perf_tests.py', 'phase6_transport_tests.py',
               'phase6_build.py', 'phase6_publish.py', 'phase6_qualify.py', 'phase2_publish.py', 'build.py', 'phase4_combined_validate.py']
    evidence = [OUT / name for name in ['baseline.json', 'F8-DEFINITIONS.txt', 'candidate/build-normal.json',
                'candidate/build-diagnostic.json', 'qsim-manifest.json', 'perf-tests.json', 'transport-tests/results.json',
                'capture-f8/inputs.json', 'capture-f8-panel/inputs.json', 'analysis.json', 'replay-fps.json', 'panel/result.json']]
    evidence += [PRIOR / name for name in ['QUALIFIED.json', 'qsim-manifest.json', 'capture-f7/inputs.json',
                 'replay-fps.json', 'star-uv-review.json', 'tex1-padding-note.json']]
    evidence += [Path(__file__).with_name(name) for name in scripts]
    extra_audit = OUT / 'supplemental-source-review.json'
    if extra_audit.exists():
        extra = read(extra_audit)
        assert extra['passed'] and extra['source_sha256'] == sources
        evidence.append(extra_audit)
    result = dict(passed=True, candidate='candidate', reviewer='/root/f8_validation',
                  source_sha256=sources, normal_elf_sha256=sha(normal), diagnostic_elf_sha256=sha(diagnostic),
                  evidence_sha256={str(path.relative_to(ROOT)): sha(path) for path in evidence},
                  normal_whole_elf_exact_to_f7=True, unchanged_qsim_executable=True,
                  gameplay_frames=721, gpu_command_word_counts_exact=True,
                  all_eight_recorded_command_mmio_order_texture_extents_exact=True,
                  selected_raster_replays=6, selected_color_depth_fence_stats_exact=True,
                  frozen_panel_words=55, panel_layout_visually_inspected=True,
                  findings=dict(
                    scheduling='No command/synchronization/submit scheduling change. Timer hooks wrap one nonempty batch, with counter updates outside the measured interval.',
                    compiler_ordering='Empty compiler memory barriers bound normal staging loads; original volatile MMIO and original hardware fences stay ordered. No added hardware fence.',
                    accounting='Independent uint32 modular frame deltas and uint64 window totals; COPY and REST use HOST denominator. Invalid intervals explicitly flagged; sparse LOAD/TRI include copy and have a separate denominator.',
                    normal='Disabled hooks generate no transport instructions and complete normal ELF is byte-identical.',
                    source_review='Reviewed all candidate deltas, callback call sites, counter lifetimes, ABI, labels, test scenarios and definitions. No blocking correctness finding.',
                    publisher='Frozen F7 qualification and full input inventory, exact8-source/build/evidence binding, retained core/OS guards, preimage checks, backup and atomic readback/rollback; SD touches only SM64 application.'),
                  limits=[
                    'Not a performance optimization or measured physical overhead/FPS result. The eager CPU/GPU harness supplies modeled costs and cannot measure Pocket backpressure.',
                    'COPY contains staged memory loads and command-port latency, not pure stores; REST retains other memory stalls, packing, texture uploads and any unmeasured waits.',
                    'Per-batch hooks add clock/counter/cache-layout overhead in diagnostics; normal game executable remains exact.',
                    'Only selected vectors are raster replayed under synthetic memory traffic; no claim of all-game pixel equality or actual CPU/GPU overlap timing.',
                    'Full TEX1 payloads are not identical: oversized star extra rows contain relocated neighboring display-list bytes. Exact assets, reconstructed uploads, identical UV/command stream and selected raster results bound checked scenes.',
                    'Sparse LOAD/TRI cannot be subtracted from all-frame COPY or REST; display truncation reconciles COPY+REST exactly with HOST.',
                    'Counter intervals must remain below a32-bit wrap. A displayed0.0 explicit wait means below0.1ms, not no stalls.'
                  ])
    write(OUT / 'independent-review.json', result)
    print(json.dumps(dict(passed=True, normal_elf_sha256=sha(normal), diagnostic_elf_sha256=sha(diagnostic),
                         independent_review_sha256=sha(OUT / 'independent-review.json'))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare-sim', 'capture', 'panel', 'analyze', 'replay', 'review'])
    action = parser.parse_args().action
    if action == 'prepare-sim':
        prepare_sim()
    elif action in ('capture', 'panel'):
        capture(action == 'panel')
    elif action == 'analyze':
        analyze()
    elif action == 'replay':
        replay()
    else:
        review()
