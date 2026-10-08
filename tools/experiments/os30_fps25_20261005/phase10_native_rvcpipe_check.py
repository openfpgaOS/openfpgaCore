#!/usr/bin/env python3
"""Bind the CPU-only RVC pipeline native cold/continuous comparisons."""
import shutil
from phase10_native_renderer_check import ROOT, P, WINDOW, sha, read, save

OUT = P / 'native-rvcpipe-comparison-v1'


def main():
    assert not OUT.exists()
    evidence, checked = {}, 0

    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)

    def table(base, data):
        nonlocal checked
        for rel, digest in data.items():
            path = base / rel
            assert sha(path) == digest, str(path)
            checked += 1
            bind(path)

    def manifest(path):
        data = read(path)
        table(path.parent, data.get('files', {}))
        for field in ['inputs_sha256', 'evidence_sha256']:
            table(ROOT, data.get(field, {}))
        bind(path)
        return data

    for label in ['native-warm-interval-check-v1', 'native-renderer-aligned-check-v1',
                  'cpu-cache64-rvcpipe-v1', 'cpu-cache64-rvcpipe-source-review-v1']:
        manifest(P / label / 'manifest.json')
    models = [WINDOW / 'model-cache64-timing-v1', WINDOW / 'model-cache64-rvcpipe-v1']
    prepared = [read(m / 'prepared.json') for m in models]
    for model, data in zip(models, prepared):
        table(model, data['source_sha256'])
        bind(model / 'prepared.json'); bind(model / 'build.json')
        assert read(model / 'build.json')['returncode'] == 0
    a, b = prepared
    assert set(a['source_sha256']) == set(b['source_sha256'])
    delta = {k: {'baseline': a['source_sha256'][k], 'candidate': v}
             for k, v in b['source_sha256'].items() if a['source_sha256'][k] != v}
    cpu = 'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    assert delta == {cpu: {
        'baseline': '32cf8619ebd6beb9b7c62e593e300ba85486daf9c70c199ad0f978a23b80b3ea',
        'candidate': 'aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'}}
    assert a['defs'] == b['defs'] and a['selected_qsf_sha256'] == b['selected_qsf_sha256']
    assert a['gpu_cache_kib'] == b['gpu_cache_kib'] == 8
    assert a['cache_layout'] == b['cache_layout']
    assert b['rvc_pipeline']['cycle_equivalence_claim'] is False
    sections = {}
    for kind, control, candidate in [
            ('cold', 'aligned-candidate-v1', 'aligned-rvcpipe-v1'),
            ('continuous', 'interval-cache64-v2', 'interval-rvcpipe-v1')]:
        controls, results = [], []
        for label, model in zip([control, candidate], models):
            runner, run = WINDOW / ('runner-' + label), WINDOW / ('run-' + label)
            m = manifest(run / 'manifest.json')
            assert m['model_prepared_sha256'] == sha(model / 'prepared.json')
            r = read(run / 'result.json')
            assert r['status'] == read(run / 'process.json')['returncode'] == 0
            assert all(r[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
            assert all(v['errors'] == 0 for v in r['readers'].values())
            controls.append((runner, run)); results.append(r)
        for filename in ['main.cpp', 'input.cfg', 'recipe.py']:
            assert (controls[0][0] / filename).read_bytes() == (controls[1][0] / filename).read_bytes()
        identical = ['entry-regs.bin', 'commands.bin', 'frame.bin']
        if kind == 'continuous':
            identical += ['entry-2.bin', 'entry-3.bin']
            identical += [f'frame-{n}{suffix}.bin' for n in [1, 2]
                          for suffix in ['', '-buffer0', '-buffer1', '-buffer2']]
        for filename in identical:
            assert (controls[0][1] / filename).read_bytes() == (controls[1][1] / filename).read_bytes(), (kind, filename)
        if kind == 'cold':
            assert all(r['fence'] == 362 and r['upload_words'] == 23036 for r in results)
            assert all(v['pending'] == 0 for r in results for v in r['readers'].values())
            times = [dict(call=r['finished_cycle'] - r['entry_cycle'],
                          drain=r['complete_cycle'] - r['finished_cycle'],
                          total=r['complete_cycle'] - r['entry_cycle']) for r in results]
            assert times[1] == {'call': 2818930, 'drain': 15760, 'total': 2834690}
        else:
            assert all(r['fence'] == 363 and r['upload_words'] == 46036 for r in results)
            times = []
            for (_, run), r in zip(controls, results):
                rows = [list(map(int, row.split())) for row in (run / 'boundaries.tsv').read_text().splitlines()]
                assert len(rows) == 3 and [x[0] for x in rows] == [1, 2, 3]
                assert [x[3:] for x in rows] == [[0, 361, 0], [23036, 362, 0], [46036, 363, 0]]
                assert rows[0][1] == r['entry_cycle'] and rows[-1][1] == r['complete_cycle']
                assert {k: v['pending'] for k, v in r['readers'].items()} == {'I': 1, 'MEM': 0, 'PER': 0, 'M1': 1, 'AUDIO': 0}
                times.append(dict(first_interval=rows[1][1] - rows[0][1],
                                  second_interval=rows[2][1] - rows[1][1],
                                  total=rows[2][1] - rows[0][1]))
            assert times[1] == {'first_interval': 3812833, 'second_interval': 4245677, 'total': 8058510}
        sections[kind] = dict(control=times[0], candidate=times[1],
                             delta_cycles={k: times[1][k] - times[0][k] for k in times[0]},
                             added_percent={k: 100 * (times[1][k] / times[0][k] - 1) for k in times[0]},
                             byte_exact_outputs=identical, control_readers=results[0]['readers'], candidate_readers=results[1]['readers'])
    original = read(P / 'native-warm-interval-check-v1/result.json')['baseline']['total_cycles']
    total = sections['continuous']['candidate']['total']
    net = dict(original_I32_D128_total=original, candidate_total=total,
               saved_cycles=original - total, reduction_percent=100 * (1 - total / original))
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    save(OUT / 'result.json', dict(passed=True, checked_input_hashes=checked, source_delta=delta,
         cold=sections['cold'], continuous=sections['continuous'], versus_original_cache=net,
         deployment_ready=False, full_frame_FPS_claimed=False,
         limitations=[
             'The CPU is regenerated with a registered decompression boundary. Cycles and instruction polling counts may change; no cycle-equivalence claim.',
             'This adds the measured cost of the pipeline to the previously sealed cold and two-interval native comparisons, retaining all their scope limitations.',
             'Continuous native raw command streams and every buffer/register dump are exact to the cache64 control. The same explicitly classified buffer-role/first-use-clear differences from the analytic reference remain.',
             'Original application game/audio work executes, but private deterministic guest services and synthetic fixed-base scan/audio traffic do not qualify the native OS/IRQ/PCM path or sustained FPS.',
             'Directed CPU redirect/IRQ/trap correctness is a separate gate; these renderer runs do not substitute for it.',
             'No source edits, regeneration, map, fit or simulation were performed by this independent checker.'
         ]))
    (OUT / 'REPORT.txt').write_text(
        'CPU-only registered-decompression native comparison: PASS\n\n'
        'Exact source inventories differ only in the selected CPU Verilog. Guest, host, runtime config, ELF, GPU8, LOD5 and all other RTL are unchanged. '
        'Cold entry/23,036 commands/frame are exact; continuous three entries/46,036 commands/all six buffer dumps are exact to the cache64 control.\n\n'
        'Cold renderer: 2,788,545 -> 2,818,930 cycles; through GPU drain 2,804,267 -> 2,834,690.\n'
        'Continuous first interval: 3,790,161 -> 3,812,833. Second warmed interval: 4,221,468 -> 4,245,677. '
        'Total 8,011,629 -> 8,058,510 (+46,881 cycles, +0.585162%).\n'
        f'Versus original I32/D128 total {original:,}, this retains {original-total:,} cycles / {net["reduction_percent"]:.6f}% improvement.\n\n'
        'This is a bounded native application-window result, not OS/audio-output/physical display/FPS qualification. '
        'The new pipeline changes latency; separate directed redirect/IRQ/trap tests remain required.\n')
    save(OUT / 'manifest.json', dict(passed=True,
         files={path.name: sha(path) for path in OUT.iterdir() if path.is_file()}, evidence_sha256=evidence))
    print(OUT, sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
