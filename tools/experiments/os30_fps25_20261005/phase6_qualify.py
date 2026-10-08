#!/usr/bin/env python3
"""Freeze tested F8 measurement artifacts; device performance remains unmeasured."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase6'
PRIOR = OUT.parent / 'phase5'
CAND = OUT / 'candidate'
evidence = {}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path, expected=None):
    digest = sha(path)
    assert expected is None or digest == expected, path
    evidence[str(path.relative_to(ROOT))] = digest
    return digest


def read(path):
    bind(path)
    return json.loads(path.read_text())


def expand(mapping):
    for relative, digest in mapping.items():
        bind(ROOT / relative, digest)


def main():
    assert not (OUT / 'QUALIFIED.json').exists()
    bind(PRIOR / 'QUALIFIED.json', '3d64fccc5de1894df4a876521cc6180f04abc139a96eb1dc8571ecff724c75b5')
    prior = read(PRIOR / 'QUALIFIED.json')
    baseline = read(OUT / 'baseline.json')
    assert baseline['source_tree_sha256'] == prior['source_tree_sha256']
    sources = {name: bind(CAND / name) for name in baseline['inputs']}
    expected = dict(baseline['source_tree_sha256'])
    for name, relative in baseline['inputs'].items():
        expected[relative] = sources[name]
    spec = importlib.util.spec_from_file_location('f8_inventory', Path(__file__).with_name('phase2_publish.py'))
    guards = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guards)
    assert len(expected) == 6747 and guards.inventory(OUT / 'SM64') == set(expected)
    for relative, digest in expected.items():
        assert sha(OUT / 'SM64' / relative) == digest, relative
    bind(ROOT / 'src/firmware/api/of_gpu.h', sources['of_gpu.h'])

    elves = {}
    for mode, name in [('normal', 'app.elf'), ('diagnostic', 'diagnostic.elf')]:
        elves[mode] = bind(CAND / name)
        build = read(CAND / f'build-{mode}.json')
        assert build['returncode'] == 0 and build['elf_sha256'] == elves[mode]
        assert {name: item['sha256'] for name, item in build['inputs'].items()} == sources
        bind(CAND / f'build-{mode}.log')
    assert elves['normal'] == prior['normal_elf_sha256'] == baseline['normal_elf_sha256']

    transport = read(OUT / 'transport-tests/results.json')
    assert transport['passed'] and transport['rv32_off_text_identical']
    assert transport['command_poll_and_state_order_identical']
    assert transport['empty_batch_timer_reads'] == 0 and transport['timer_reads_per_nonempty_batch'] == 2
    assert transport['header_sha256'] == sources['of_gpu.h']
    assert transport['app_sha256'] == sources['gfx_gpu.c']
    bind(Path(__file__).with_name('phase6_transport_tests.py'), transport['script_sha256'])
    for path in (OUT / 'transport-tests').iterdir():
        if path.is_file():
            bind(path)
    perf = read(OUT / 'perf-tests.json')
    assert perf['passed'] and perf['snapshot_words'] == 55
    assert not perf['normal_macro_undefined_symbols']
    for name, digest in perf['source_sha256'].items():
        assert sources[name] == digest
    bind(Path(__file__).with_name('phase6_perf_tests.py'), perf['script_sha256'])

    simulator = read(OUT / 'qsim-manifest.json')
    bind(Path(simulator['simulator']), simulator['simulator_sha256'])
    bind(PRIOR / 'qsim-manifest.json', simulator['parent_manifest_sha256'])
    for name, digest in simulator['unchanged_sources'].items():
        bind(PRIOR / 'qsim' / name, digest)
    for label in ('f8', 'f8-panel'):
        capture = OUT / ('capture-' + label)
        inputs = read(capture / 'inputs.json')
        assert inputs['success'] and inputs['audio'] and not inputs['geometry_capability']
        assert inputs['panel'] == (label == 'f8-panel')
        for path, digest in inputs['sha256'].items():
            bind(Path(path), digest)
        bind(capture / 'sm64.elf', elves['diagnostic'])
        for relative, digest in inputs['results_sha256'].items():
            bind(capture / relative, digest)
    analysis = read(OUT / 'analysis.json')
    assert analysis['passed'] and all(analysis['gameplay_geometry_culling_exact'].values())
    assert analysis['all_721_command_word_counts_exact']
    assert analysis['all_eight_gpu_command_mmio_order_texture_extents_exact']
    assert analysis['snapshot_checks']['copy_plus_rest_equals_host']
    assert analysis['snapshot_checks']['invalid_frames'] == 0
    replays = read(OUT / 'replay-fps.json')
    assert {(r['traffic'], r['frame']) for r in replays} == {
        (traffic, frame) for traffic in (1, 2) for frame in (360, 600, 720)}
    for row in replays:
        assert row['color_depth_exact'] and row['fence_exact']
        assert row['all_gpu_statistics_exact_to_f7'] and row['upload_readback_exact']
        stem = OUT / 'replays/fps' / str(row['traffic']) / f"frame_{row['frame']:05}"
        for suffix, key in [('.vec', 'adapted_vector_sha256'), ('.bin', 'output_sha256'), ('.log', 'log_sha256')]:
            bind(stem.with_suffix(suffix), row[key])
    panel = read(OUT / 'panel/result.json')
    assert panel['snapshot_checks']['all_words_frozen_frames600_through679']
    bind(OUT / 'panel/panel.png', panel['image_sha256'])
    bind(OUT / 'panel/frame.bin', panel['output_sha256'])
    visual = read(OUT / 'panel/visual-review.json')
    assert visual['passed'] and visual['image_sha256'] == panel['image_sha256']
    review = read(OUT / 'independent-review.json')
    assert review['passed'] and review['source_sha256'] == sources
    assert review['normal_elf_sha256'] == elves['normal']
    assert review['diagnostic_elf_sha256'] == elves['diagnostic']
    expand(review['evidence_sha256'])
    bind(OUT / 'F8-DEFINITIONS.txt')
    for name in ('phase6_build.py', 'phase6_publish.py', 'phase6_qualify.py',
                 'phase6_validate.py', 'phase6_panel.py', 'phase2_publish.py', 'build.py'):
        bind(Path(__file__).with_name(name))
    result = dict(candidate='candidate', parent='phase5/candidate/F7',
                  purpose='Command-transfer attribution; not a speed optimization.',
                  qualified_for_device_trial=True, independent_review_passed=True, target_verified=False,
                  normal_elf_sha256=elves['normal'], diagnostic_elf_sha256=elves['diagnostic'],
                  normal_executable_byte_identical_to_f6=True, fps_header='PERF F8 SEND',
                  source_sha256=sources, source_tree_sha256=expected,
                  core_sha256=prior['core_sha256'], os_sha256=prior['os_sha256'],
                  modeled_diagnostic_cost=analysis['gameplay_frames360_through720'],
                  evidence_sha256=evidence,
                  limits=['Physical command-transfer split and profiler overhead need a Pocket snapshot.',
                          'REST includes other memory/cache stalls and texture work, not only computation.',
                          'LOAD/TRI are sparse-frame averages that include COPY and cannot be subtracted from all-frame COPY.',
                          'GPU replay covers six selected cases; the existing oversized star upload has separately bounded relocation differences.',
                          'Core, OS, clocks and resources unchanged; no FPGA fit or seed sweep.'])
    guards.write_record(OUT / 'QUALIFIED.json', result)
    print(json.dumps({'qualified_for_device_trial': True, 'diagnostic_sha256': elves['diagnostic'],
                      'bound_evidence_files': len(evidence), 'device_performance_measured': False}, indent=2))


if __name__ == '__main__':
    main()
