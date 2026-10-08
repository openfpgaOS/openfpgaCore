#!/usr/bin/env python3
"""Seal independent source, numerical, output and bounded performance review."""
import json
from pathlib import Path

from phase9_experiment import CAMP, HERE, INPUTS, OUT, PARENT, ROOT, sha, verify_capture, write

CAND = OUT / 'checks-state-f11'
DEST = OUT / 'independent-review'
EVIDENCE = {}
ALIASES = {}


def bind(path, expected=None):
    path = Path(path)
    digest = sha(path)
    if expected is not None:
        assert digest == expected, str(path)
    actual = path.resolve()
    assert actual.is_relative_to(ROOT), actual
    relative = str(actual.relative_to(ROOT))
    if path != actual:
        ALIASES[str(path)] = str(actual)
    assert relative not in EVIDENCE or EVIDENCE[relative] == digest
    EVIDENCE[relative] = digest
    return digest


def read(path):
    bind(path)
    return json.loads(Path(path).read_text())


def expand(mapping, base=ROOT):
    for name, digest in mapping.items():
        bind(base / name, digest)


def capture(folder, elf_hash):
    assert len(verify_capture(folder, elf_hash)) == 721
    record = read(folder / 'inputs.json')
    expand(record['sha256'])
    expand(record['results_sha256'], folder)


def main():
    assert not (DEST / 'FINAL.json').exists()
    sources = {name: bind(CAND / name) for name in INPUTS}
    composition = read(CAND / 'composition.json')
    assert composition['source_sha256'] == sources
    backend = read(CAND / 'backend-validation.json')
    assert backend['passed'] and backend['source_sha256'] == sources
    expand(backend['evidence_sha256'])
    publication = read(OUT / 'publication-review.json')
    assert publication['passed'] and all(publication['checks'].values())
    expand(publication['evidence_sha256'])
    retained = read(OUT / 'retained-before.json')
    assert retained['passed'] and retained['source_tree_files_verified'] == 6747
    expand(retained['evidence_sha256'])
    bind(HERE / 'phase9_publish.py', publication['wrapper_sha256'])
    bind(HERE / 'phase2_publish.py', publication['inherited_publisher_sha256'])
    bind(HERE / 'phase9_qualify.py')

    timer_dir = OUT / 'w-guard-v1/cpu-guard-timer'
    timer = read(timer_dir / 'result.json')
    assert timer['passed'] and timer['comparisons'] == 69120 and timer['rounding_modes'] == 5
    assert all(row['saving_percent'] > 0 for row in timer['category_totals'])
    assert timer['standalone_baseline_nan_accepts'] and timer['standalone_candidate_nan_accepts'] == 0
    expand(timer['evidence_sha256'])
    timer_manifest = read(timer_dir / 'manifest.json')
    bind(Path(timer_manifest['simulator_path']), timer_manifest['simulator_sha256'])
    expand(timer_manifest['unchanged_rtl_object_sha256'])
    expand(timer_manifest['source_sha256'])
    expand(timer_manifest['firmware_sha256'], timer_dir / 'firmware')
    assert timer_manifest['source_sha256'][str((OUT / 'w-guard-v1/gfx_pc.c').relative_to(ROOT))] == sources['gfx_pc.c']
    predicate = read(OUT / 'w-guard-v1/tests/predicate/result.json')
    assert predicate['passed'] and predicate['raw_ieee_encodings'] == 2 ** 32
    expand(predicate['source_sha256'])
    expand(predicate['evidence_sha256'], OUT / 'w-guard-v1/tests/predicate')
    indexed = read(OUT / 'w-guard-v1/tests/indexed/RESULT.json')
    assert indexed['passed'] and indexed['baseline'] == indexed['candidate']
    assert indexed['sources']['candidate'] == sources['gfx_pc.c']
    bind(OUT / 'w-guard-v1/codegen-normal/exceptional-input-note.json')
    state = read(OUT / 'direct-state-v1/state-tests/RESULT.json')
    assert state['passed'] and all(row['detected'] for row in state['negative_controls'].values())
    assert all(row['result']['cases'] == 8192 for row in state['runs'].values())
    expand(state['source_sha256'])

    elves, cpu, coupled = {}, {}, []
    for mode, name in [('normal', 'app.elf'), ('diagnostic', 'diagnostic.elf')]:
        elves[mode] = bind(CAND / name)
        build = read(CAND / ('build-' + mode + '.json'))
        assert build['returncode'] == 0 and build['elf_sha256'] == elves[mode]
        assert backend[mode + '_elf_sha256'] == elves[mode]
        for suffix in ('log', 'recipe.py', 'invocation.json'):
            bind(CAND / ('build-' + mode + '-' + suffix) if suffix != 'log' else CAND / ('build-' + mode + '.log'))
        closure = read(CAND / ('independent-composition-' + mode) / 'review.json')
        assert closure['passed'] and closure['source_inputs'] == 6747 and closure['dependency_files'] == 298
        assert closure['source_sha256'] == sources and closure['elf_sha256'] == elves[mode]
        expand(closure['source_tree_sha256'], CAND / 'SM64')
        bind(CAND / ('independent-composition-' + mode) / 'review-recipe.py', closure['helper_sha256'])
        graph_dir = CAND / ('codegen-' + mode)
        graph = read(graph_dir / 'review.json')
        assert graph['passed'] and graph['results']['candidate']['elf_sha256'] == elves[mode]
        assert sum(item['culling_block_count'] for item in graph['comparisons'].values()) == 5
        assert all(item['same_rounding_graph_contract'] for item in graph['comparisons'].values())
        recipe = graph_dir / 'review-recipe.py'
        if not recipe.exists():
            with recipe.open('xb') as stream:
                stream.write((HERE / 'phase9_codegen_review.py').read_bytes())
        bind(recipe, graph['helper_sha256'])
        bind(HERE / 'phase7_cull_codegen_review.py', graph['definitions_source_sha256'])
        for label in ('F9', 'candidate'):
            for function, row in graph['results'][label]['functions'].items():
                bind(graph_dir / (label + '-' + function + '.asm'), row['disassembly_sha256'])
        capture(CAND / ('capture-' + mode), elves[mode])
        analysis = read(CAND / ('analysis-' + mode + '.json'))
        assert analysis['passed'] and all(analysis['scene_geometry_culling_exact'].values())
        assert analysis['all721_command_word_counts_exact'] and analysis['all8_publication_boundaries_exact']
        assert analysis['all8_ordered_command_mmio_streams_exact_except_CMDS_boundaries']
        assert analysis['texture_flush_order_preserved']
        assert all(row['saving_percent'] > 0 for row in analysis['windows'])
        cpu[mode] = analysis['gameplay_frames360_through720']
        assert cpu[mode]['est_cycles']['saving_percent'] > 0
        replay = read(CAND / ('replay-' + mode + '.json'))
        assert len(replay) == 6 and all(all(row[key] for key in ('color_exact', 'depth_exact', 'fence_exact')) for row in replay)
        assert all(row['gpu_cycle_change_percent'] == 0 for row in replay)
        for start in (360, 600):
            comparison = read(CAND / f'coupled-{mode}-{start}.json')
            bind(CAND / f'coupled-{mode}-{start}.recipe.py', comparison['analyzer_sha256'])
            assert comparison['passed'] and comparison['mode'] == mode
            for row in comparison['comparisons']:
                assert row['exact_color_depth_frames'] == 8 and row['exact_command_words_pixels']
                assert row['throughput_change_percent'] > 0
                coupled.append(dict(mode=mode, **row))
                folder = OUT / 'coupled' / f'run-checks-state-f11-{mode}-{start}-t{row["traffic"]}'
                invocation = read(folder / 'phase9-invocation.json')
                assert invocation['source_elf_sha256'] == elves[mode]
                record = read(folder / 'inputs.json')
                assert record['success'] and record['frames'] == 8
                bind(folder / 'inputs.json', row['candidate_input_sha256'])
                expand(record['sha256'])
                expand(record['results_sha256'], folder)
                base = ROOT / 'docs/measurements/os30-overlap-20261006/coupled' if mode == 'normal' else CAMP / 'phase8/diagnostic-coupled'
                prior = base / f'run-baseline{start}-t{row["traffic"]}'
                bind(prior / 'inputs.json', row['baseline_input_sha256'])
                prior_record = read(prior / 'inputs.json')
                expand(prior_record['sha256'])
                expand(prior_record['results_sha256'], prior)
    assert len(coupled) == 8
    assert {(row['mode'], row['start'], row['traffic']) for row in coupled} == {
        (mode, start, traffic) for mode in ('normal', 'diagnostic') for start in (360, 600) for traffic in (1, 2)}
    capture(CAND / 'capture-diagnostic-panel', elves['diagnostic'])
    panel = read(CAND / 'panel/result.json')
    assert panel['passed'] and panel['candidate_elf_sha256'] == elves['diagnostic']
    bind(CAND / 'panel/panel.png', panel['image_sha256'])
    perf = read(CAND / 'perf-tests.json')
    assert perf['passed'] and perf['snapshot_words'] == 55 and not perf['normal_macro_undefined_symbols']
    for name, digest in perf['source_sha256'].items():
        assert sources[name] == digest
    for name in ('phase9_experiment.py', 'phase9_coupled.py', 'phase9_coupled_compare.py',
                 'phase9_codegen_review.py', 'phase9_combined_review.py', 'phase9_final_review.py'):
        bind(HERE / name)
    for name in ('harness-stage.json', 'harness-preparation-recipe.py', 'candidate.patch'):
        bind(CAND / name)
    limits = [
        'This is a small CPU-preparation cleanup, not a substantial performance improvement or a verified25FPS result.',
        'Coupled timings combine modeled CPU/cache timing with retained-A GPU/SDRAM/CRAM1 RTL and synthetic traffic; actual CPU memory arbitration feedback and kernel mixing/interrupt costs are absent.',
        'Fixed game update per rendered frame and two short camera windows do not cover all levels or actual Pocket frame rates.',
        'Finite W values preserve predicate decisions. NaNs now take existing fallback while F9 emitted fast-math code can accept them; universal exceptional-value and fflags equivalence are not claimed.',
        'Component guard cycle savings are not whole-game speedup. Final normal and diagnostic executable results are reported separately and include layout/cache effects.',
        'No FPGA clock, core feature, full seed sweep, sibling application or SD-card write was performed by this independent review. Publication is a later root-owned local-only action.',
        'SD was absent during retained-state review. F10 is only the last historically verified deployment; no fresh SD readback is claimed.'
    ]
    lines = ['Independent final F11 CPU-preparation review', '',
        'PASS as a small cleanup for local publication. This does not deliver a substantial game-speed increase.',
        'The exact composition is the finite positive-W integer eligibility guard, repeated equal-alpha state-check elimination, and the F11 PREP diagnostic marker.', '',
        'Final coupled throughput change against exact F9 executables:',
        'Mode        Start Traffic Gain percent  Saved ms/frame']
    for row in coupled:
        lines.append(f'{row["mode"]:11} {row["start"]:5} {row["traffic"]:7} {row["throughput_change_percent"]:12.4f} {-row["frame_time_delta_ms"]:15.6f}')
    lines += ['', f'Eager CPU modeled cycle saving: normal {cpu["normal"]["est_cycles"]["saving_percent"]:.4f}%; diagnostic {cpu["diagnostic"]["est_cycles"]["saving_percent"]:.4f}%.',
        'All6747 source inputs are closed with exactly3 changes; both actual ELF culling graphs preserve all5 arithmetic blocks.',
        'Each normal/diagnostic721-frame capture preserves scene/culling/geometry and command counts, with8 exact ordered command/MMIO streams including publication boundaries and texture flushes.',
        'All64 coupled paired color/depth frames match. All12 separate retained-A replay comparisons match color/depth/fence and GPU cycles.',
        'The actual-A extracted guard passes69120 finite-boundary decisions over5 rounding modes; its26–30% component saving is not a game-speed claim.',
        'The backend oracle passes8192 variants in each checked/fast mode and detects missing initial-state and missing-alpha updates.',
        'F11 PREP panel visually reviewed;55-word version8 snapshot accounting preserved.', '', 'Limits:', *('- ' + item for item in limits)]
    with (DEST / 'REPORT.txt').open('x') as stream:
        stream.write('\n'.join(lines) + '\n')
    bind(DEST / 'REPORT.txt')
    with (DEST / 'final-review-recipe.py').open('xb') as stream:
        stream.write(Path(__file__).read_bytes())
    bind(DEST / 'final-review-recipe.py')
    result = dict(passed=True, candidate='checks-state-f11', independent_review_passed=True,
        target_verified=False, substantial_performance_improvement=False,
        source_sha256=sources, normal_elf_sha256=elves['normal'], diagnostic_elf_sha256=elves['diagnostic'],
        numerical_contract=composition['numerical_contract'],
        validation=dict(source_inputs=6747, culling_blocks_per_mode=5, capture_frames_per_mode=721,
            coupled_pairs=8, paired_color_depth_frames=64, replay_comparisons=12, panel_checked=True,
            actual_cpu_guard_finite_comparisons=69120, actual_cpu_guard_rounding_modes=5,
            backend_oracle_cases_per_mode=8192, snapshot_words=55),
        coupled_comparisons=coupled, modeled_cpu_cost=cpu, evidence_sha256=EVIDENCE,
        resolved_read_only_model_aliases=ALIASES, limits=limits)
    write(DEST / 'FINAL.json', result)
    print(json.dumps(dict(passed=True, evidence_files=len(EVIDENCE), final_sha256=sha(DEST / 'FINAL.json')), indent=2))


if __name__ == '__main__':
    main()
