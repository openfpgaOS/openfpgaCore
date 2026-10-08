#!/usr/bin/env python3
"""Freeze a validated measurement build; it is not a performance optimization."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT = CAMP / 'phase5'
CAND = OUT / 'candidate'
evidence = {}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def bind(p, expected=None):
    h = sha(p)
    if expected is not None:
        assert h == expected, (p, h, expected)
    evidence[str(p.relative_to(ROOT))] = h
    return h


def read(p):
    bind(p)
    return json.loads(p.read_text())


def expand(mapping):
    for rel, digest in mapping.items():
        bind(ROOT / rel, digest)


def main():
    assert not (OUT / 'QUALIFIED.json').exists()
    parent_path = CAMP / 'phase4/release/QUALIFIED.json'
    bind(parent_path, '977cc3c0c1ae91d4e52bd9d585ebc14ec928e6a231d035c4d6bdadde32517a3b')
    parent = read(parent_path)
    baseline = read(OUT / 'baseline.json')
    assert baseline['source_tree_sha256'] == parent['source_tree_sha256']
    sources = {name: bind(CAND / name) for name in baseline['inputs']}
    tree = dict(baseline['source_tree_sha256'])
    assert len(tree) == 6747
    for name, rel in baseline['inputs'].items():
        tree[rel] = sources[name]
    for rel, digest in tree.items():
        bind(OUT / 'SM64' / rel, digest)
    bind(ROOT / 'src/firmware/api/of_gpu.h', sources['of_gpu.h'])
    source_review = read(OUT / 'independent-source-review-v2.json')
    assert source_review['passed'] and source_review['source_sha256'] == sources
    expand(source_review['evidence_sha256'])
    outcomes = read(OUT / 'independent-outcomes-review.json')
    assert outcomes['passed'] and outcomes['source_and_build_closure_passed']
    assert outcomes['source_sha256'] == sources
    expand(outcomes['evidence_sha256'])
    waits = read(OUT / 'wait-tests-v2/results.json')
    assert waits['passed'] and waits['rv32_off_text_identical']
    assert waits['poll_hook_state_order_identical'] and waits['no_wait_timer_reads'] == 0
    assert waits['timer_reads_per_blocking_episode'] == 2
    assert waits['header_sha256'] == sources['of_gpu.h'] and waits['app_sha256'] == sources['gfx_gpu.c']
    bind(Path(__file__).with_name('phase5_wait_tests.py'), waits['script_sha256'])
    perf = read(OUT / 'perf-tests.json')
    assert perf['passed'] and not perf['normal_macro_undefined_symbols']
    for name, digest in perf['source_sha256'].items():
        assert sources[name] == digest
    bind(Path(__file__).with_name('phase5_perf_tests.py'), perf['script_sha256'])
    elves = {}
    for mode, name, record in [('normal', 'app.elf', 'build-normal.json'),
                               ('fps', 'diagnostic.elf', 'build-diagnostic.json')]:
        elves[mode] = bind(CAND / name)
        build = read(CAND / record)
        assert build['returncode'] == 0 and build['elf_sha256'] == elves[mode]
        assert {n: v['sha256'] for n, v in build['inputs'].items()} == sources
        bind(CAND / record.replace('.json', '.log'))
    assert elves['normal'] == parent['normal_elf_sha256'] == outcomes['normal_elf_sha256']
    assert elves['fps'] == outcomes['diagnostic_elf_sha256']
    simulator = read(OUT / 'qsim-manifest.json')
    bind(OUT / 'qsim/qsim', simulator['simulator_sha256'])
    for name, digest in simulator['sources'].items():
        bind(OUT / 'qsim' / name, digest)
    for label in ('f6-control', 'f7', 'f7-panel'):
        capture = OUT / ('capture-' + label)
        inputs = read(capture / 'inputs.json')
        assert inputs['success'] and inputs['audio'] and not inputs['geometry_capability']
        assert inputs['panel'] == (label == 'f7-panel')
        expand(inputs['sha256'])
        expected = parent['diagnostic_elf_sha256'] if label == 'f6-control' else elves['fps']
        bind(capture / 'sm64.elf', expected)
        for rel, digest in inputs['results_sha256'].items():
            bind(capture / rel, digest)
    analysis = read(OUT / 'analysis.json')
    assert all(analysis['control_observer_noninterference'].values())
    assert all(analysis['gameplay_geometry_culling_exact'].values())
    assert analysis['snapshot_partition_and_sample_checks']['passed']
    assert analysis['gameplay_frames360_through720']['gpu_cmd_words']['delta'] == 0
    replay_review = read(OUT / 'independent-replay-review.json')
    assert replay_review['passed'] and replay_review['comparisons'] == 16
    assert replay_review['all_color_depth_exact'] and replay_review['all_active_route']
    assert replay_review['all_candidate_fences_exact'] and replay_review['all_upload_readbacks_exact']
    expand(replay_review['evidence_sha256'])
    rows = read(OUT / 'replay-fps.json')
    expected = {(t, f) for t in (1, 2) for f in (40, 120, 200, 360, 400, 500, 600, 720)}
    assert len(rows) == 16 and {(r['traffic'], r['frame']) for r in rows} == expected
    for row in rows:
        assert row['color_depth_exact']
        stem = OUT / 'replays/fps' / str(row['traffic']) / f"frame_{row['frame']:05}"
        bind(stem.with_suffix('.vec'), row['adapted_vector_sha256'])
        bind(stem.with_suffix('.bin'), row['output_sha256'])
        bind(stem.with_suffix('.log'), row['log_sha256'])
    panel = read(OUT / 'panel/result.json')
    bind(OUT / 'panel/panel.png', panel['image_sha256'])
    bind(OUT / 'panel/frame.bin', panel['output_sha256'])
    assert read(OUT / 'panel/visual-review.json')['passed']
    bind(OUT / 'F7-DEFINITIONS.txt')
    for name in ('phase5_qualify.py', 'phase5_publish.py', 'phase5_validate.py', 'phase5_panel.py',
                 'phase2_publish.py', 'build.py', 'phase4_combined_validate.py'):
        bind(Path(__file__).with_name(name))
    result = dict(candidate='candidate', parent='phase4/combined-cold/aspect-texmem-cold-v1/F6',
                  purpose='Diagnostic wait/renderer attribution; not a speed optimization.',
                  qualified_for_device_trial=True, independent_review_passed=True, target_verified=False,
                  normal_elf_sha256=elves['normal'], diagnostic_elf_sha256=elves['fps'],
                  normal_executable_byte_identical_to_f6=True, fps_header='PERF F7 WAIT',
                  source_sha256=sources, source_tree_sha256=tree,
                  core_sha256=parent['core_sha256'], os_sha256=parent['os_sha256'],
                  modeled_diagnostic_cost=analysis['gameplay_frames360_through720'],
                  evidence_sha256=evidence,
                  limits=['Physical F7 wait split remains to be measured on Pocket.',
                          'HOST includes unmeasured memory/MMIO and texture-upload stalls.',
                          'LOAD/TRI use sparse sampled-frame averages; all-frame HOST has a different denominator.',
                          'Eager qsim cannot exercise physical GPU backpressure; mocked waits test accounting/order.',
                          'Diagnostic instrumentation changes execution and code layout. Modeled overhead is not a hardware bound.',
                          'FPGA bitstream, clock configuration and resources unchanged; no full seed sweep.'])
    (OUT / 'QUALIFIED.json').write_text(json.dumps(result, indent=2) + '\n')
    costs = analysis['gameplay_frames360_through720']['est_cycles']
    report = [
        'F7 wait-attribution diagnostic over F6. No performance optimization selected.', '',
        'Normal executable is byte-for-byte identical to F6.',
        'Diagnostic721-frame run preserves scene, geometry, culling and GPU command counts.',
        '16/16 actual-A GPU replays preserve color/depth; active texture route, fences and uploads verified.',
        'Focused tests cover actual wait functions, zero ready-path timer reads, wrap, nesting and frozen windows.',
        f"Modeled diagnostic CPU overhead: {costs['percent_change']:.3f}%, {costs['delta']/361/100000:.3f} ms/render at100MHz.",
        'This is not a physical overhead bound; eager GPU does not model live wait episodes.',
        'F7 reports RING/DMA/FENC in milliseconds and HOST outside those explicit waits.',
        'LOAD/TRI sample every16 rendered frames; they still include CPU memory and MMIO stalls.',
        'Select+L+R displays a frozen two-second window. Pocket screenshot is needed for the actual split.',
        'Retained F6 hardware observation:27.4FPS in one level9 window. F7 device performance not measured.',
        'No FPGA change, fit, full sweep or commit.'
    ]
    (OUT / 'REPORT.txt').write_text('\n'.join(report) + '\n')
    print('\n'.join(report))
    print('Bound evidence files:', len(evidence))


if __name__ == '__main__':
    main()
