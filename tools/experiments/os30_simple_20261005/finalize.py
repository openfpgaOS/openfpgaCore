#!/usr/bin/env python3
"""Verify final simple-v4 evidence; --write freezes reports and selection, never deploys."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'
V = OUT / 'validation'
sys.path.insert(0, str(Path(__file__).parent / 'validation'))
from common import capture_path, check_capture

CANDIDATE = 'simple-v4'
CORE_A = 'd3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f'
CPU_A = '33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
APPS = {
    'normal': ('0350acc15976134120b3ba4cb48de4ee5f9077f62732143251840d68293ebc6f',
               'e11905966c8d5c3c8a8d086e3269e5c97a64042eb77c21c1e15d9964b68770a4', 'app.elf'),
    'diagnostic': ('990ba23281d6793a479df4c9094cb0cd600894ca3ab5548df360a62c016f2984',
                   '94d8a7e30f5ce9615722f54f71018eb2f520e5214cc06c550477b44c7c255168',
                   'diagnostic.elf'),
}
SOURCES = {
    'gfx_pc.c': '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0',
    'gfx_gpu.c': 'c6655bac3072cef63cfcfee8656fa4e302f9c1f07778225c241c81b7daf27e0d',
}
LEAVES = ['clip-exact', 'vc-material-memo-v1', 'slot-index-v1', 'normal-key-v1', 'cpu-only-v2']
EXPECTED_REPLAYS = {(mode, frame) for mode in (1, 2)
                    for frame in (40, 120, 200, 360, 400, 500, 600, 720)}
evidence = {}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path, expected=None):
    path = path.resolve()
    require(path.is_relative_to(ROOT), f'Evidence outside repository: {path}')
    actual = sha(path)
    require(expected is None or actual == expected, f'Evidence changed: {path}')
    evidence[str(path.relative_to(ROOT))] = actual
    return actual


def read(path):
    bind(path)
    return json.loads(path.read_text())


def bound_references(record, key):
    for path, expected in record.get(key, {}).items():
        bind(ROOT / path, expected)


def check_sources_and_review():
    baseline = read(OUT / 'source-baseline.json')
    manifest = read(OUT / CANDIDATE / 'manifest.json')
    require(manifest['components'] == ['simple-v1', 'normal-key-v1', 'cpu-only-v2'],
            'Unexpected final composition')
    require(set(manifest['files']) == set(SOURCES), 'Unexpected modified source files')
    for name, expected in SOURCES.items():
        bind(OUT / CANDIDATE / name, expected)
        require(manifest['files'][name]['candidate_sha256'] == expected, 'Composition hash mismatch')
    review = read(OUT / 'independent-review/simple-v4-review.json')
    require(review['source_review_passed'] is True, 'Final independent review is not passed')
    require(set(review['selected_leaf_components']) == set(LEAVES), 'Review includes wrong components')
    for name, expected in SOURCES.items():
        require(review['files'][name]['sha256'] == expected and
                review['files'][name]['leaf_composition_exact'] is True,
                f'Final review does not bind exact source: {name}')
    for label, (_, expected, filename) in APPS.items():
        build = read(OUT / CANDIDATE / f'build-{label}.json')
        require(build['returncode'] == 0 and build['diagnostic'] is (label == 'diagnostic'),
                f'Invalid {label} build')
        require(build['elf_sha256'] == expected, f'Wrong {label} app in build record')
        bind(OUT / CANDIDATE / filename, expected)
        require(set(build['inputs']) == {'gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h'},
                'Unexpected build inputs')
        for name, info in build['inputs'].items():
            wanted = SOURCES.get(name, baseline['files']['src/sm64/sm64/src/pc/gfx/' + name])
            require(info['sha256'] == wanted, f'Wrong {label} input: {name}')
            bind(OUT / (CANDIDATE if name in SOURCES else 'baseline') / name, wanted)
    return review


def check_proofs():
    reports = {}
    for name, rel, leaf in (
        ('clip', 'clip-exact/function-checks/manifest.json', 'clip-exact'),
        ('material', 'vc-material-memo-v1/focused-proof.json', 'vc-material-memo-v1'),
        ('normal_key', 'validation/normal-key-v1-focused/results.json', 'normal-key-v1'),
    ):
        report = read(OUT / rel)
        require(report['passed'] is True, f'{name} proof failed')
        bind(OUT / leaf / 'gfx_pc.c', report['candidate_sha256'])
        reports[name] = report
    optin = read(OUT / 'cpu-only-v2/preprocess-audit-v2/results.json')
    require(optin['passed'] is True and optin['macro'] == 1 and
            all(optin['source_token_equivalence'].values()), 'Worker opt-in restoration failed')
    for name, expected in optin['candidate_sources'].items():
        bind(OUT / 'cpu-only-v2' / name, expected)
    bound_references(optin, 'preprocessed_sha256')
    clip = read(V / 'cpu-kernel/results.json')
    slot = read(V / 'cpu-slot-kernel/results.json')
    for name, report in (('clip', clip), ('slot', slot)):
        require(report['passed'] is True, f'{name} actual CPU component test failed')
        bound_references(report, 'evidence_sha256')
    return {
        'clip_warm_quiet_cycle_saving_percent': clip['summary']['quiet']['warm']['1']['cycle_saving_percent'],
        'clip_warm_scanout_cycle_saving_percent': clip['summary']['scanout']['warm']['1']['cycle_saving_percent'],
        'slot_warm_cycle_saving_percent': slot['summary']['quiet']['warm']['1']['cycle_saving_percent'],
        'scope': 'Actual A CPU/fabric RTL component kernels with common harness overhead; '
                 'these percentages are not whole-game or device FPS gains.',
    }


def check_hardware():
    provenance = read(ROOT / 'docs/measurements/os30-sm64-ab-20261005/provenance/manifest.json')
    core = provenance['configurations']['A_single_geo0']
    require(core['pocket_sha256'] == CORE_A and core['cpu_sha256'] == CPU_A, 'Wrong retained core')
    bind(ROOT / core['pocket_bitstream'], CORE_A)
    for path, expected in core['frozen_sources'].items():
        bind(ROOT / path, expected)
    config = read(V / 'final-config-audit.json')
    require(config['passed'] is True and config['A_core_sha256'] == CORE_A and
            config['A_CPU_sha256'] == CPU_A and config['configured_clock_hz'] == 100000000,
            'Final configuration audit failed')
    require(config['geometry_capability_enabled'] is False and
            config['command_DMA_enabled'] is False and config['worker_defines_present'] is False,
            'Wrong optional hardware capabilities')
    bound_references(config, 'source_hashes')
    bind(V / 'replay-single-A/obj/Vtb_gpu', config['gpu_replay_sha256'])
    bind(V / 'qsim-observed/qsim', config['qsim_sha256'])
    for label, (old, new, _) in APPS.items():
        for key, expected in ((f'baseline-{label}', old), (f'{CANDIDATE}-{label}', new)):
            row = config['captures_checked'][key]
            require(row['elf_sha256'] == expected and row['geometry_disabled_in_console'] is True
                    and row['gpu_caps_clock_verified'] is True, f'Capture not config-audited: {key}')
            bind(capture_path(key) / 'inputs.json', row['inputs_sha256'])
    before = read(OUT / 'publication-before.json')
    for path, expected in before['files'].items():
        require(sha(ROOT.parent / 'SM64' / path) == expected, f'Local SM64 changed: {path}')
    return core


def replay_index():
    labels = {f'{prefix}-{kind}' for prefix in ('baseline', CANDIDATE) for kind in APPS}
    index = {}
    for path in V.glob('replay-results-*.json'):
        rows = json.loads(path.read_text())
        relevant = [row for row in rows if row['label'] in labels]
        if not relevant:
            continue
        bind(path)
        for row in relevant:
            key = row['label'], row['mode'], row['frame']
            require(key not in index, f'Duplicate replay: {key}')
            index[key] = row
    return index


def input_files(record):
    items = [(Path(path).name, value) for path, value in record['sha256'].items()
             if Path(path).name != 'sm64.elf']
    require(len(dict(items)) == len(items), 'Ambiguous shared input basenames')
    return dict(items)


def check_pair(kind, index):
    old_hash, new_hash, _ = APPS[kind]
    old_label, new_label = f'baseline-{kind}', f'{CANDIDATE}-{kind}'
    pair = read(V / f'results-{new_label}.json')
    require(pair['candidate'] == new_label and pair['baseline'] == old_label and
            pair['baseline_elf_sha256'] == old_hash and pair['candidate_elf_sha256'] == new_hash,
            f'Wrong {kind} paired result')
    require(pair['exact_inputs_except_app'] is True and pair['scene_identical'] is True and
            pair['all_replayed_pixels_depth_exact'] is True, f'Paired correctness failure: {kind}')
    bound_references(pair, 'evidence_sha256')
    paths = [capture_path(old_label), capture_path(new_label)]
    captures = [check_capture(path) for path in paths]
    require(input_files(captures[0]) == input_files(captures[1]), f'Shared inputs differ: {kind}')
    require((paths[0] / 'scene.csv').read_bytes() == (paths[1] / 'scene.csv').read_bytes(),
            f'Scene differs: {kind}')
    frames = []
    for path, capture, wanted in zip(paths, captures, (old_hash, new_hash)):
        bind(path / 'sm64.elf', wanted)
        for relative, expected in capture['results_sha256'].items():
            bind(path / relative, expected)
        for shared, expected in capture['sha256'].items():
            bind(Path(shared), expected)
        with (path / 'frames.csv').open() as f:
            rows = list(csv.DictReader(f))
        require(len(rows) == 721, 'Incomplete frame capture')
        frames.append(rows)
    require((pair['gameplay']['first_frame'], pair['gameplay']['last_frame'],
             pair['gameplay']['frames']) == (360, 720, 361), 'Unexpected gameplay window')
    for key, metric in pair['gameplay']['metrics'].items():
        a, b = [sum(int(row[key]) for row in rows[360:721]) for rows in frames]
        require(metric['baseline'] == a and metric['candidate'] == b, f'Wrong {kind} total: {key}')
        require(abs(metric['saving_percent'] - 100 * (1 - b / a)) < 1e-9,
                f'Wrong {kind} percentage: {key}')
    for key in ('insns', 'est_cycles'):
        require(pair['gameplay']['metrics'][key]['saving_percent'] > 0, f'{kind} {key} regresses')
    require(len(pair['windows']) == 7 and all(
        row['metrics']['est_cycles']['saving_percent'] > 0 for row in pair['windows']),
        f'{kind} modeled window regression')
    comparisons = {(row['mode'], row['frame']): row for row in pair['gpu_comparisons']}
    require(len(pair['gpu_comparisons']) == 16 and set(comparisons) == EXPECTED_REPLAYS,
            f'{kind} replay coverage incomplete')
    variations = []
    for mode, frame in sorted(EXPECTED_REPLAYS):
        c = comparisons[mode, frame]
        require(c['color']['different_pixels'] == c['depth']['different_pixels'] == 0,
                f'Rendering mismatch: {kind}/{mode}/{frame}')
        a, b = index[old_label, mode, frame], index[new_label, mode, frame]
        require(a['hardware_sha256'] == b['hardware_sha256'], 'GPU replay hardware differs')
        raw = []
        for row in (a, b):
            binary = ROOT / row['binary']
            bind(binary)
            bind(binary.with_suffix('.log'), row['log_sha256'])
            data = binary.read_bytes()
            require(len(data) > 153612, 'Incomplete replay binary')
            require(hashlib.sha256(data[:153600]).hexdigest() == row['framebuffer_sha256']
                    and hashlib.sha256(data[153612:]).hexdigest() == row['depth_sha256'],
                    'Replay output checksum differs')
            raw.append(data)
        require(raw[0][:153600] == raw[1][:153600] and raw[0][153612:] == raw[1][153612:],
                'Replay color/depth bytes differ')
        require(a['commands'] == b['commands'] == c['baseline_commands'] == c['candidate_commands'],
                'Replay command counts differ')
        ca, cb = a['stats']['cycles'], b['stats']['cycles']
        require(c['baseline_gpu_cycles'] == ca and c['candidate_gpu_cycles'] == cb,
                'Replay cycle records disagree')
        delta = cb - ca
        if delta:
            percent = 100 * delta / ca
            require(frame < 360 and abs(percent) < 0.1,
                    f'Unqualified replay cycle change: {kind}/{mode}/{frame}: {percent}%')
            variations.append(dict(mode=mode, frame=frame, baseline=ca, candidate=cb,
                                   extra_cycles=delta, change_percent=percent))
    return dict(baseline_elf_sha256=old_hash, candidate_elf_sha256=new_hash,
                actual_instruction_saving_percent=pair['gameplay']['metrics']['insns']['saving_percent'],
                modeled_cycle_saving_percent=pair['gameplay']['metrics']['est_cycles']['saving_percent'],
                metrics=pair['gameplay']['metrics'],
                window_modeled_savings_percent=[r['metrics']['est_cycles']['saving_percent']
                                                for r in pair['windows']],
                gameplay_frames=361, exact_gpu_color_depth_comparisons=16,
                gameplay_gpu_cycles_identical=True, intro_gpu_cycle_variations=variations)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='freeze reports/selection after every check passes')
    args = parser.parse_args()
    check_sources_and_review()
    component = check_proofs()
    core = check_hardware()
    index = replay_index()
    paired = {kind: check_pair(kind, index) for kind in APPS}
    final_validation = read(V / 'final-simple-v4-validation.json')
    require(final_validation['passed'] is True and final_validation['candidate'] == CANDIDATE
            and final_validation['normal_and_diagnostic_independently_paired'] is True,
            'Final independent runtime validation failed')
    bound_references(final_validation, 'evidence_sha256')
    for kind, (old_hash, new_hash, _) in APPS.items():
        row = final_validation['results'][f'{CANDIDATE}-{kind}']
        require(row['elf_sha256'] == new_hash and row['baseline_elf_sha256'] == old_hash
                and row['exact_color_depth_comparisons'] == 16
                and row['all_gameplay_GPU_cycle_counts_unchanged'] is True,
                f'Final runtime validation does not bind qualified {kind} result')
    read(V / 'baseline-profile-breakdown.json')
    bind(OUT / 'simple-v3/REJECTED.txt')
    bind(OUT / 'simple-v3/app.elf', APPS['normal'][0])
    bind(Path(__file__))
    limits = [
        'Actual guest instructions are counted; whole-game CPU cycles are modeled estimates '
        'with approximate latency, prediction and memory behavior, not full-game RTL timing.',
        'GPU replay runs separately from CPU capture. It does not measure CPU/GPU overlap '
        'or establish an on-device FPS gain.',
        'The CPU rendering bottleneck is reduced, not eliminated. No claim of 30% faster gameplay.',
        'Component improvements are reported separately and are not added to the combined gain.',
        'Retained A is configured at 100 MHz; its previous 92.53 MHz minimum main-clock estimate '
        'and -0.842 ns setup slack do not close timing at 100 MHz. No new FPGA build or sweep.',
        'All selected rendering comparisons preserve color/depth exactly. Any bounded intro-only '
        'GPU cycle variations are listed explicitly; all tested gameplay GPU cycles are identical.',
        'Report generation does not publish or deploy. Actual publication and SD deployment state '
        'are recorded separately; consult those records rather than treating qualification as deployment.',
    ]
    rejected = {
        'vertex-flags': 'Excluded: actual CPU component kernel regressed despite model gain.',
        'packet rewrites and format specializations': 'Excluded: no qualified combined benefit.',
        'guardband': 'Excluded: changed rendered pixels/depth.',
        'position memo and batch position plans': 'Excluded: measured overhead or inconsistent gains.',
        'simple-v3': 'Invalid experiment: composition failed; its ELF is exactly the original baseline '
                     '0350acc1. It supplies no optimization or performance evidence.',
        'dual issue and geometry worker hardware': 'Not selected; the retained A single-issue core is unchanged.',
    }
    report = dict(qualified=True, candidate=CANDIDATE, core='A-single', core_sha256=CORE_A,
                  cpu_sha256=CPU_A, configured_clock_mhz=100, hardware_changes=False,
                  fpga_build=False, fpga_sweep=False,
                  publication_state='Qualification only; local publication and SD deployment have separate records.',
                  local_publication_record='docs/measurements/os30-simple-20261005/published.json',
                  source_sha256={'src/sm64/sm64/src/pc/gfx/' + k: v for k, v in SOURCES.items()},
                  selected_components=LEAVES, paired_results=paired, actual_CPU_component_tests=component,
                  bottleneck='CPU vertex and triangle/display-list preparation dominates the '
                             'captured software profile (roughly 26% vertex, 46% triangle preparation/submission).' ,
                  changes=['Remove unused accelerator/deferred dispatch from the A-only default app.',
                           'Pack lighting memo keys directly from three unsigned normal bytes.',
                           'Reuse stable material eligibility/texture signature within its existing lifetime.',
                           'Replace bounded vertex-slot division with an exact integer quotient.',
                           'Remove redundant clip-plane work and unused scratch initialization.'],
                  rejected=rejected, limits=limits, evidence_sha256=dict(evidence))
    n, d = paired['normal'], paired['diagnostic']
    text = '\n'.join([
        'SM64 simple-v4 qualification on retained single-issue os30 A',
        '===========================================================',
        '', report['bottleneck'],
        'The selected changes reduce redundant software work without adding hardware.',
        '', 'Combined paired gameplay results (361 frames, identical scene and shared inputs):',
        f"Normal: {n['actual_instruction_saving_percent']:.4f}% fewer executed guest instructions; "
        f"{n['modeled_cycle_saving_percent']:.4f}% fewer MODELED CPU cycles.",
        f"P3 diagnostic: {d['actual_instruction_saving_percent']:.4f}% fewer executed guest instructions; "
        f"{d['modeled_cycle_saving_percent']:.4f}% fewer MODELED CPU cycles.",
        'Each app passes 16 exact color/depth GPU replay comparisons. Tested gameplay GPU cycles are identical.',
    ]) + '\n'
    for kind, result in paired.items():
        for row in result['intro_gpu_cycle_variations']:
            text += (f"Intro variation: {kind}, frame {row['frame']}, traffic mode {row['mode']}: "
                     f"{row['extra_cycles']:+d} GPU cycles ({row['change_percent']:+.4f}%), identical pixels/depth.\n")
    text += '\nSelected changes:\n' + ''.join('  - ' + s + '\n' for s in report['changes'])
    text += ('\nSeparate actual A CPU RTL component evidence:\n'
             f"  Clip kernel: {component['clip_warm_scanout_cycle_saving_percent']:.2f}% fewer warm cycles.\n"
             f"  Slot kernel: {component['slot_warm_cycle_saving_percent']:.2f}% fewer warm cycles.\n"
             '  These are component results, not whole-game speedups.\n')
    text += '\nExcluded experiments:\n' + ''.join(f'  {k}: {v}\n' for k, v in rejected.items())
    text += '\nLimits and publication state:\n' + ''.join('  - ' + s + '\n' for s in limits)
    text += f'\nA Pocket bitstream SHA-256: {CORE_A}\n'
    text += 'Local publication is separately guarded by publish.py; its --rollback --apply restores prior local files.\n'
    text += 'Report generation writes only qualification artifacts. Consult separate local-publication and SD-deployment records for installation state.\n'
    selection = dict(qualified=True, candidate=CANDIDATE, core='A-single', core_sha256=CORE_A,
                     source_sha256=report['source_sha256'], normal_app_sha256=APPS['normal'][1],
                     diagnostic_app_sha256=APPS['diagnostic'][1], evidence_sha256=dict(evidence))
    outputs = {'REPORT.json': json.dumps(report, indent=2) + '\n', 'REPORT.txt': text}
    for name, content in outputs.items():
        selection['evidence_sha256'][str((OUT / name).relative_to(ROOT))] = hashlib.sha256(content.encode()).hexdigest()
    outputs['selection.json'] = json.dumps(selection, indent=2) + '\n'
    print(json.dumps(dict(qualified=True, write=args.write, paired_results=paired,
                          evidence_files=len(evidence), outputs=list(outputs)), indent=2))
    if args.write:
        for name in outputs:
            require(not (OUT / name).exists(), f'Preserve existing finalized output: {name}')
        for name, content in outputs.items():
            (OUT / name).write_text(content)


if __name__ == '__main__':
    main()
