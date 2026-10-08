#!/usr/bin/env python3
"""Read-only CPU-only native forwarding equivalence review; no simulation."""
import shutil
from phase10_native_renderer_check import ROOT, P, WINDOW, sha, read, save

OUT = P / 'native-cpu-load-forward-check-v1'


def main():
    assert not OUT.exists()
    evidence = {}

    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)

    def verify_manifest(path):
        data = read(path)
        for key, base in [('files', path.parent), ('inputs_sha256', ROOT), ('evidence_sha256', ROOT)]:
            for name, digest in data.get(key, {}).items():
                assert sha(base / name) == digest, name
                bind(base / name)
        bind(path)
        return data

    for label in ['cpu-load-forward-v1', 'cpu-load-forward-source-review-v1',
                  'render-cache-storage-v3', 'render-cache-storage-source-review-v3',
                  'native-f11-selected-comparison-v1']:
        verify_manifest(P / label / 'manifest.json')
    model_names = ['model-cache64-rvcpipe-render32-storage-v3',
                   'model-cache64-rvcpipe-render32-storage-v3-forward-v1']
    run_names = ['interval-render32-storage-v3', 'interval-render32-storage-v3-forward-v1']
    models = [WINDOW / n for n in model_names]
    prepared = [read(m / 'prepared.json') for m in models]
    for model, data in zip(models, prepared):
        for name, digest in data['source_sha256'].items():
            assert sha(model / name) == digest, name
            bind(model / name)
        bind(model / 'prepared.json'); bind(model / 'build.json')
        assert read(model / 'build.json')['returncode'] == 0
    a, b = prepared
    assert set(a['source_sha256']) == set(b['source_sha256'])
    delta = {n: [a['source_sha256'][n], h] for n, h in b['source_sha256'].items()
             if h != a['source_sha256'][n]}
    cpu = 'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    assert delta == {cpu: [
        'aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295',
        '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534']}
    for key in ['defs', 'cache_layout', 'gpu_cache_kib', 'rvc_pipeline', 'selected_qsf_sha256']:
        assert a[key] == b[key], key
    for m, d in zip(models, prepared):
        d['normalized_command'] = [v.replace(str(m), '<MODEL>') for v in d['command']]
    assert a['normalized_command'] == b['normalized_command']
    runs = [WINDOW / ('run-' + n) for n in run_names]
    runners = [WINDOW / ('runner-' + n) for n in run_names]
    for run, model in zip(runs, models):
        m = verify_manifest(run / 'manifest.json')
        assert m['model_prepared_sha256'] == sha(model / 'prepared.json')
        r = read(run / 'result.json')
        assert r['status'] == read(run / 'process.json')['returncode'] == 0
        assert all(r[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
        assert all(x['errors'] == 0 for x in r['readers'].values())
        assert r['upload_words'] == 46036 and r['fence'] == 363
    for n in ['main.cpp', 'input.cfg', 'recipe.py']:
        assert (runners[0] / n).read_bytes() == (runners[1] / n).read_bytes(), n
        bind(runners[0] / n); bind(runners[1] / n)
    exact = ['result.json', 'boundaries.tsv', 'pc.csv', 'mmio.tsv', 'commands.bin',
             'entry-regs.bin', 'entry-2.bin', 'entry-3.bin', 'frame.bin']
    exact += [f'frame-{i}{suffix}.bin' for i in [1, 2] for suffix in ['', '-buffer0', '-buffer1', '-buffer2']]
    assert len(exact) == 17
    for name in exact:
        assert (runs[0] / name).read_bytes() == (runs[1] / name).read_bytes(), name
    rows = [list(map(int, line.split())) for line in (runs[0] / 'boundaries.tsv').read_text().splitlines()]
    assert [r[1] for r in rows] == [1380, 3723654, 7870417]
    assert [r[3:] for r in rows] == [[0, 361, 0], [23036, 362, 0], [46036, 363, 0]]
    # Also close the syntax-only v2 -> v3 link to the already reviewed workload.
    control = WINDOW / 'run-interval-render32-storage-v2'
    verify_manifest(control / 'manifest.json')
    for name in exact:
        assert (control / name).read_bytes() == (runs[0] / name).read_bytes(), name
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    save(OUT / 'result.json', dict(passed=True, source_delta=delta,
        source_hashes_checked=len(a['source_sha256']) * 2, exact_artifacts=exact,
        cpu_only_change=True, first_interval_cycles=3722274, second_interval_cycles=4146763,
        total_cycles=7869037, cycle_change=0, storage_v2_v3_also_exact=True,
        no_new_execution=True, deployment_ready=False,
        limitations=[
            'Two continuous renderer-entry intervals, including original caller/game/audio CPU work; second renderer warm.',
            'Bounded functional guest services and synthetic traffic do not qualify native OS/IRQ/PCM/display or sustained FPS.',
            'Inherited model prepared.json still contains one obsolete single-call wording line; the executed host/guest interval contract and evidence above govern this review.',
            'Literal arbitrary-input proof is separate from native workload coverage; mapping and timing improvement remain unproven.'
        ]))
    (OUT / 'REPORT.txt').write_text(
        'CPU load-forward native paired review: PASS\n\n'
        'Only the CPU source changes aecb -> 82b7. Model topology, cache sizes, every other RTL byte, '
        'compilation options, exact PGO ELF/snapshot, guest services, host and runtime input are unchanged. '
        'All 17 result artifacts are byte-identical, including 46,036 commands, all three architectural '
        'entry snapshots, all framebuffer/depth dumps, MMIO history, retired-PC profile and cycle counters.\n\n'
        'Intervals remain 3,722,274 + 4,146,763 = 7,869,037 cycles. The cache v2 -> v3 spelling-only run '
        'is also identical for the same artifacts. No new run was performed by this checker.\n\n'
        'This is native workload and zero-cycle compatibility evidence, not a mapped-area, timing, '
        'native OS/IRQ/PCM or physical FPS qualification.\n')
    save(OUT / 'manifest.json', dict(passed=True,
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()}, evidence_sha256=evidence))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
