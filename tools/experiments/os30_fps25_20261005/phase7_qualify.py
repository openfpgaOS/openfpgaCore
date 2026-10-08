#!/usr/bin/env python3
"""Freeze the separately tested F9 software triangle optimization."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
OUT, PRIOR = CAMP / 'phase7', CAMP / 'phase6'
CAND, PREP = OUT / 'combined-v1', OUT / 'prepared-v3'
PARENT_SHA = '7ba3033b008dcc117867d6854cf767f7dd4c3b417c3ded2b1c89d63d82de0b69'
FRAMES = (40, 120, 200, 360, 400, 500, 600, 720)
evidence = {}


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


guards = module('f9_qualification_guards', HERE / 'phase2_publish.py')
validation = module('f9_qualification_vectors', HERE / 'phase7_validate.py')
adapter = module('f9_qualification_adapter', HERE / 'phase4_combined_validate.py')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def bind(path, expected=None):
    path = Path(path)
    guards.regular(path, ROOT)
    digest = sha(path)
    assert expected is None or digest == expected, ('Hash mismatch', path)
    relative = str(path.relative_to(ROOT))
    assert relative not in evidence or evidence[relative] == digest
    evidence[relative] = digest
    return digest


def read(path):
    bind(path)
    return json.loads(Path(path).read_text())


def expand(mapping, base=ROOT):
    for relative, digest in mapping.items():
        path = Path(relative)
        assert not path.is_absolute() and '..' not in path.parts
        bind(base / path, digest)


def bind_folder(directory):
    for path in sorted(directory.rglob('*')):
        assert not path.is_symlink(), path
        if path.is_file():
            bind(path)


def capture(directory, digest, simulator, panel=False):
    info = read(directory / 'inputs.json')
    assert info['success'] and info['panel'] == panel
    assert info['audio'] and not info['geometry_capability'] and not info['command_dma']
    assert info['hw_features'] == '0x2b136753'
    assert Path(info['command'][0]) == Path(simulator['simulator'])
    assert info['command'][info['command'].index('--gpu-sched') + 1] == 'eager'
    bind(directory / 'sm64.elf', digest)
    for name, expected in info['sha256'].items():
        bind(Path(name), expected)
    expand(info['results_sha256'], directory)
    rows = list(csv.DictReader((directory / 'frames.csv').open()))
    assert len(rows) == 721
    return rows


def analysis(mode, before, current, a, b, sources):
    result = read(CAND / f'analysis-{mode}.json')
    assert result['candidate'] == CAND.name and result['mode'] == mode
    assert result['source_sha256'] == sources
    bind(before / 'inputs.json', result['baseline_capture_sha256'])
    bind(current / 'inputs.json', result['candidate_capture_sha256'])
    exact = {name: sha(before / name) == sha(current / name)
             for name in ('scene.csv', 'geometry.csv', 'culling.csv')}
    assert all(exact.values()) and exact == result['scene_geometry_culling_exact']
    assert [r['gpu_cmd_words'] for r in a] == [r['gpu_cmd_words'] for r in b]
    computed = {}
    for key in ('insns', 'est_cycles', 'gpu_cmd_words', 'icache_miss_sdram', 'dcache_miss', 'mispredict'):
        x, y = (sum(int(r[key]) for r in rows[360:]) for rows in (a, b))
        computed[key] = dict(baseline=x, candidate=y, delta=y-x, saving_percent=100*(1-y/x))
    assert computed == result['gameplay_frames360_through720']
    assert computed['est_cycles']['saving_percent'] > 0
    assert computed['insns']['saving_percent'] > 0
    windows = []
    for lo, hi in ((360, 480), (480, 600), (600, 721)):
        x, y = (sum(int(r['est_cycles']) for r in rows[lo:hi]) for rows in (a, b))
        assert y < x
        windows.append(dict(first=lo, last=hi-1, baseline=x, candidate=y, saving_percent=100*(1-y/x)))
    assert windows == result['windows']
    return computed


def vectors(mode, before, current):
    result = read(CAND / f'vectors-{mode}.json')
    assert result['passed'] and result['all_eight_command_mmio_order_exact']
    assert result['all_721_command_word_counts_exact']
    assert {r['frame'] for r in result['vectors']} == set(FRAMES) and len(result['vectors']) == 8
    for row in result['vectors']:
        name = f"gpuvec/frame_{row['frame']:05}.vec"
        bind(before / name, row['baseline_sha256'])
        bind(current / name, row['candidate_sha256'])
        old, new = (validation.v.vector((d / name).read_bytes()) for d in (before, current))
        assert all(old[k] == new[k] for k in ('hardware', 'depth', 'fence', 'uploads'))
        assert len(old['texture']) == len(new['texture'])
        changed = [dict(offset=i, before=old['texture'][i:i+4].hex(), after=new['texture'][i:i+4].hex())
                   for i in range(0, len(old['texture']), 4) if old['texture'][i:i+4] != new['texture'][i:i+4]]
        assert changed == row['texture_changes']
    padding = validation.padding(result['vectors'], before, current, CAND / 'SM64')
    assert padding == result['padding_check'] and padding['original_asset_exact']
    bind(CAMP / 'phase5/star-uv-review.json', padding['prior_uv_context_sha256'])
    bind(CAMP / 'phase5/tex1-padding-note.json', padding['prior_note_sha256'])
    if mode == 'diagnostic':
        assert validation.v.snapshot_checks(current) == result['snapshot_checks']
        assert result['snapshot_checks']['invalid_frames'] == 0


class BytesDestination:
    """Run the exact depth-vector adapter without creating an artifact."""
    def write_bytes(self, data):
        self.data = data
        return len(data)


def replay(mode, rtl_digest):
    rows = read(CAND / f'replay-{mode}.json')
    frames = FRAMES if mode == 'normal' else (360, 600, 720)
    assert len(rows) == 2*len(frames)
    assert {(r['traffic'], r['frame']) for r in rows} == {(t, f) for t in (1, 2) for f in frames}
    prior_dir = OUT / 'control' if mode == 'normal' else PRIOR
    prior_mode = 'normal' if mode == 'normal' else 'fps'
    prior = {(r['traffic'], r['frame']): r for r in read(prior_dir / f'replay-{prior_mode}.json')}
    for row in rows:
        assert row['color_exact'] and row['depth_exact'] and row['fence_exact']
        assert row['simulator_sha256'] == rtl_digest
        stem = CAND / 'replays' / mode / str(row['traffic']) / f"frame_{row['frame']:05}"
        for suffix, key in (('.vec', 'adapted_vector_sha256'), ('.bin', 'output_sha256'), ('.log', 'log_sha256')):
            bind(stem.with_suffix(suffix), row[key])
        source = CAND / f'capture-{mode}/gpuvec/{stem.name}.vec'
        adapted = BytesDestination()
        counts, depth, tags = adapter.with_depth(source, adapted)
        assert depth and adapted.data == stem.with_suffix('.vec').read_bytes()
        assert counts == row['commands'] and tags == row['tags']
        assert Path(row['command'][0]) == validation.v.RTL / 'obj/Vtb_gpu'
        assert f"+memory_traffic={row['traffic']}" in row['command'] and '+tagged_tex=1' in row['command']
        old = prior[row['traffic'], row['frame']]
        baseline_stem = prior_dir / 'replays' / prior_mode / str(row['traffic']) / stem.name
        bind(baseline_stem.with_suffix('.bin'), old['output_sha256'])
        bind(baseline_stem.with_suffix('.log'), old['log_sha256'])
        assert row['reference_output_sha256'] == old['output_sha256']
        raw, reference = stem.with_suffix('.bin').read_bytes(), baseline_stem.with_suffix('.bin').read_bytes()
        assert len(raw) == len(reference) == 307212
        assert raw[:153604] == reference[:153604] and raw[153612:] == reference[153612:]
        stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', stem.with_suffix('.log').read_text())}
        prior_stats = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', baseline_stem.with_suffix('.log').read_text())}
        assert stats == row['stats'] == prior_stats == old['stats']
        assert stats['cram_errors'] == 0 and stats['words'] == stats['readback_words']
        assert row['gpu_cycle_change_percent'] == 0.0


def component_tests(sources):
    # The selected backend and quantization body are exactly those qualified
    # on retained A RTL; the rejection-only frontend changes have a separate
    # full indexed-run differential test, including fallback side effects.
    prepared = {n: bind(PREP / n) for n in ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h')}
    assert all(prepared[n] == sources[n] for n in ('gfx_gpu.c', 'gfx_gpu.h'))
    extractor = module('f9_prepared_extract', HERE / 'phase7_prepared_cpu.py')
    assert extractor.function((PREP/'gfx_pc.c').read_text(), 'gfx_cpu_prepare_clip') == \
           extractor.function((CAND/'gfx_pc.c').read_text(), 'gfx_cpu_prepare_clip')
    focused = read(PREP / 'focused/result.json')
    assert focused['pass'] and focused['cases'] == 33280 and focused['rounding_modes'] == 4
    assert focused['negative_control_detected'] and focused['source_sha256'] == prepared
    expand(focused['files_sha256'], PREP / 'focused')
    cpu = read(PREP / 'cpu-prepared/result.json')
    assert cpu['pass'] and cpu['rounding_modes'] == 5 and cpu['comparisons'] == 2560
    expand(cpu['evidence_sha256'], PREP / 'cpu-prepared')
    manifest = read(PREP / 'cpu-prepared/manifest.json')
    bind(Path(manifest['simulator_path']), manifest['simulator_sha256'])
    expand(manifest['unchanged_rtl_object_sha256'])
    expand(manifest['source_sha256'])
    expand(manifest['firmware_sha256'], PREP / 'cpu-prepared/firmware')
    bind(PREP / 'cpu-prepared/kernel.asm', manifest['assembly_sha256'])
    assert '-ffast-math' in manifest['compiler_command'] and '-mtune=sifive-5-series' in manifest['compiler_command']
    for directory in (PREP, CAND):
        test = read(directory / 'tests/RESULT.json')
        assert test['passed'] and test['baseline']['cases'] == test['candidate']['cases'] == 2654208
        assert test['baseline']['semantic_hash'] == test['candidate']['semantic_hash']
        bind(PRIOR / 'candidate/gfx_pc.c', test['sources']['baseline'])
        bind(directory / 'gfx_pc.c', test['sources']['candidate'])
        bind(HERE / 'phase7_eligibility_tests.py', test['recipe_sha256'])
        if directory == CAND:
            assert test['negative_fallback_before_prefix_detected']
        bind_folder(directory / 'tests')


def main():
    assert not (OUT / 'QUALIFIED.json').exists(), 'Keep immutable qualification'
    bind(PRIOR / 'QUALIFIED.json', PARENT_SHA)
    prior = read(PRIOR / 'QUALIFIED.json')
    baseline = read(OUT / 'baseline.json')
    assert baseline['parent_qualified_sha256'] == PARENT_SHA
    assert baseline['source_tree_sha256'] == prior['source_tree_sha256']
    assert baseline['normal_elf_sha256'] == prior['normal_elf_sha256']
    assert baseline['diagnostic_elf_sha256'] == prior['diagnostic_elf_sha256']
    sources = {name: bind(CAND / name) for name in baseline['inputs']}
    assert len(sources) == 8
    expected = dict(baseline['source_tree_sha256'])
    for name, relative in baseline['inputs'].items():
        expected[relative] = sources[name]
    assert len(expected) == 6747 and guards.inventory(CAND / 'SM64') == set(expected)
    expand(expected, CAND / 'SM64')
    bind(ROOT / 'src/firmware/api/of_gpu.h', sources['of_gpu.h'])
    assert sources['of_gpu.h'] == prior['source_sha256']['of_gpu.h']
    # Private compiler stages contain source inputs, not release runtime
    # files. Check the retained sibling release through the publication guard.
    assert prior['core_sha256'] == guards.CORE and prior['os_sha256'] == guards.OS
    guards.local_retained()
    composition = read(CAND / 'combined-source.json')
    assert composition['candidate_sha256'] == sources and composition['parent'] == 'prepared-v3'
    for name, digest in composition['parent_sha256'].items():
        bind(PREP / name, digest)
    bind(HERE / 'phase7_combined_prepare.py', composition['recipe_sha256'])
    bind(HERE / 'phase7_rejection_prepare.py', composition['rejection_transform_sha256'])
    assert composition['deferred_preparation_precedes_fallback']

    elves = {}
    for mode, name in (('normal', 'app.elf'), ('diagnostic', 'diagnostic.elf')):
        elves[mode] = bind(CAND / name)
        build = read(CAND / f'build-{mode}.json')
        assert build['returncode'] == 0 and build['diagnostic'] == (mode == 'diagnostic')
        assert build['elf_sha256'] == elves[mode] and Path(build['elf']) == CAND / name
        assert {n: item['sha256'] for n, item in build['inputs'].items()} == sources
        for n, item in build['inputs'].items():
            assert Path(item['source']) == CAND / n
            bind(Path(item['source']), item['sha256'])
        bind(CAND / f'build-{mode}.log')
    assert elves['normal'] != prior['normal_elf_sha256'], 'This is an executable optimization'

    sim = read(PRIOR / 'qsim-manifest.json')
    bind(Path(sim['simulator']), sim['simulator_sha256'])
    bind(CAMP / 'phase5/qsim-manifest.json', sim['parent_manifest_sha256'])
    expand(sim['unchanged_sources'], Path(sim['simulator']).parent)
    modeled = {}
    for mode in ('normal', 'diagnostic'):
        before = OUT / 'control/capture-normal' if mode == 'normal' else PRIOR / 'capture-f8'
        current = CAND / f'capture-{mode}'
        a = capture(before, baseline[mode+'_elf_sha256'], sim)
        b = capture(current, elves[mode], sim)
        modeled[mode] = analysis(mode, before, current, a, b, sources)
        vectors(mode, before, current)
    capture(CAND / 'capture-diagnostic-panel', elves['diagnostic'], sim, panel=True)

    gpu = read(validation.v.RTL / 'build.json')
    bind(validation.v.RTL.parent / 'inputs.json', gpu['inputs_sha256'])
    bind(validation.v.RTL / 'obj/Vtb_gpu', gpu['simulator_sha256'])
    for mode in ('normal', 'diagnostic'):
        replay(mode, gpu['simulator_sha256'])
    perf = read(CAND / 'perf-tests.json')
    assert perf['passed'] and perf['snapshot_words'] == 55 and not perf['normal_macro_undefined_symbols']
    assert all(sources[n] == digest for n, digest in perf['source_sha256'].items())
    bind(HERE / 'phase6_perf_tests.py', perf['script_sha256'])
    panel = read(CAND / 'panel/result.json')
    bind(CAND / 'capture-diagnostic-panel/inputs.json', panel['capture_inputs_sha256'])
    assert panel['snapshot_checks'] == validation.v.snapshot_checks(CAND/'capture-diagnostic-panel', panel=True)
    assert panel['snapshot_checks']['all_words_frozen_frames600_through679']
    assert panel['snapshot_checks']['invalid_frames'] == 0
    assert panel['simulator_sha256'] == gpu['simulator_sha256']
    bind(CAND / 'panel/frame_00640.vec', panel['vector_sha256'])
    bind(CAND / 'panel/frame.bin', panel['output_sha256'])
    bind(CAND / 'panel/panel.png', panel['image_sha256'])
    bind(CAND / 'panel/replay.log')
    visual = read(CAND / 'panel/visual-review.json')
    assert visual['passed'] and visual['image_sha256'] == panel['image_sha256']
    component_tests(sources)
    review = read(OUT / 'independent-review.json')
    assert review['passed'] and review['source_sha256'] == sources
    assert review['normal_elf_sha256'] == elves['normal'] and review['diagnostic_elf_sha256'] == elves['diagnostic']
    expand(review['evidence_sha256'])
    bind(OUT / 'REPORT.txt')

    for name in ('phase7_qualify.py', 'phase7_publish.py', 'phase7_experiment.py', 'phase7_validate.py',
                 'phase7_replay.py', 'phase7_prepared_tests.py', 'phase7_prepared_cpu.py',
                 'phase7_prepared_prepare.py', 'phase7_prepared_split.py', 'phase7_eligibility_tests.py',
                 'phase2_publish.py', 'build.py', 'phase6_validate.py', 'phase4_combined_validate.py'):
        bind(HERE / name)
    result = dict(candidate=CAND.name, parent='phase6/candidate/F8',
        purpose='GPU-ready vertex records and reuse of completed rejection checks; no hardware changes.',
        qualified_for_device_trial=True, independent_review_passed=True, target_verified=False,
        normal_elf_sha256=elves['normal'], diagnostic_elf_sha256=elves['diagnostic'],
        normal_executable_byte_identical_to_f6=False, fps_header='PERF F9 TRI',
        source_sha256=sources, source_tree_sha256=expected,
        core_sha256=prior['core_sha256'], os_sha256=prior['os_sha256'],
        modeled_gameplay_cost=modeled, evidence_sha256=evidence,
        limits=['Modeled CPU cycle improvements do not establish physical Pocket FPS.',
                'LOAD/TRI use sparse samples; COPY and REST use all measured render frames.',
                'GPU replay covers 16 normal and six diagnostic cases under synthetic traffic.',
                'The pre-existing oversized star upload has three separately bounded relocation differences outside sampled UV rows.',
                'Core, OS, clocks and FPGA resources unchanged; no fit or seed sweep.'])
    guards.write_record(OUT / 'QUALIFIED.json', result)
    print(json.dumps(dict(qualified_for_device_trial=True, candidate=CAND.name,
                         bound_evidence_files=len(evidence), device_performance_measured=False,
                         normal_sha256=elves['normal'], diagnostic_sha256=elves['diagnostic']), indent=2))


if __name__ == '__main__':
    main()
