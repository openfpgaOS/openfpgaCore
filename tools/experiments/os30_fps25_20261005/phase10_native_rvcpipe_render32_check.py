#!/usr/bin/env python3
"""Read-only paired native GPU8/GPU32 review; does not run either model."""
import shutil
from phase10_native_renderer_check import ROOT, P, WINDOW, sha, read, save

OUT = P / 'native-rvcpipe-render32-check-v1'


def main():
    assert not OUT.exists()
    evidence, checked = {}, 0

    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)

    def table(base, values):
        nonlocal checked
        for name, digest in values.items():
            path = base / name
            assert sha(path) == digest, str(path)
            checked += 1
            bind(path)

    def manifest(path):
        data = read(path)
        table(path.parent, data.get('files', {}))
        for key in ['inputs_sha256', 'evidence_sha256']:
            table(ROOT, data.get(key, {}))
        bind(path)
        return data

    for label in ['native-rvcpipe-comparison-v1', 'native-warm-interval-check-v1']:
        manifest(P / label / 'manifest.json')
    labels = ['cache64-rvcpipe-v1', 'cache64-rvcpipe-render32-v1']
    models = [WINDOW / ('model-' + x) for x in labels]
    prepared = [read(x / 'prepared.json') for x in models]
    for model, data in zip(models, prepared):
        table(model, data['source_sha256'])
        bind(model / 'prepared.json'); bind(model / 'build.json')
        assert read(model / 'build.json')['returncode'] == 0
        assert data['cpu_sha256'] == 'aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295'
        assert data['cache_layout'] == 'I64/D64'
    a, b = prepared
    assert set(a['source_sha256']) == set(b['source_sha256'])
    delta = {name: {'baseline': a['source_sha256'][name], 'candidate': digest}
             for name, digest in b['source_sha256'].items() if a['source_sha256'][name] != digest}
    top = 'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/tb_irq.v'
    assert list(delta) == [top]
    old = (models[0] / top).read_text()
    needle = '.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4), .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)'
    assert old.count(needle) == 1
    assert old.replace(needle, needle.replace('.SET_BITS(6)', '.SET_BITS(8)')) == (models[1] / top).read_text()
    assert a['defs'] == b['defs'] and a['selected_qsf_sha256'] == b['selected_qsf_sha256']
    assert a['rvc_pipeline'] == b['rvc_pipeline']
    assert [a['gpu_cache_kib'], b['gpu_cache_kib']] == [8, 32]
    runs, runners, results, times = [], [], [], []
    for label, model in zip(['interval-rvcpipe-v1', 'interval-rvcpipe-render32-v1'], models):
        run, runner = WINDOW / ('run-' + label), WINDOW / ('runner-' + label)
        m = manifest(run / 'manifest.json')
        assert m['model_prepared_sha256'] == sha(model / 'prepared.json')
        result = read(run / 'result.json')
        assert result['status'] == read(run / 'process.json')['returncode'] == 0
        assert all(result[key] == 0 for key in ['write_errors', 'scan_errors', 'cram_errors'])
        assert all(row['errors'] == 0 for row in result['readers'].values())
        assert result['fence'] == 363 and result['upload_words'] == 46036
        rows = [list(map(int, x.split())) for x in (run / 'boundaries.tsv').read_text().splitlines()]
        assert len(rows) == 3 and [x[0] for x in rows] == [1, 2, 3]
        assert [x[3:] for x in rows] == [[0, 361, 0], [23036, 362, 0], [46036, 363, 0]]
        assert rows[0][1] == result['entry_cycle'] and rows[-1][1] == result['complete_cycle']
        assert {k: v['pending'] for k, v in result['readers'].items()} == {'I': 1, 'MEM': 0, 'PER': 0, 'M1': 1, 'AUDIO': 0}
        times.append(dict(first_interval=rows[1][1] - rows[0][1],
                          second_interval=rows[2][1] - rows[1][1], total=rows[2][1] - rows[0][1]))
        runs.append(run); runners.append(runner); results.append(result)
    assert times == [dict(first_interval=3812833, second_interval=4245677, total=8058510),
                     dict(first_interval=3722274, second_interval=4146763, total=7869037)]
    for name in ['main.cpp', 'input.cfg', 'recipe.py']:
        assert (runners[0] / name).read_bytes() == (runners[1] / name).read_bytes(), name
        bind(runners[0] / name); bind(runners[1] / name)
    exact = ['entry-regs.bin', 'entry-2.bin', 'entry-3.bin', 'commands.bin', 'frame.bin']
    exact += [f'frame-{n}{suffix}.bin' for n in [1, 2] for suffix in ['', '-buffer0', '-buffer1', '-buffer2']]
    for name in exact:
        assert (runs[0] / name).read_bytes() == (runs[1] / name).read_bytes(), name
    inherited = read(P / 'native-warm-interval-check-v1/result.json')
    original = inherited['baseline']['total_cycles']
    saved = {k: times[0][k] - times[1][k] for k in times[0]}
    gain = {k: 100 * saved[k] / times[0][k] for k in times[0]}
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    save(OUT / 'result.json', dict(passed=True, checked_input_hashes=checked, source_delta=delta,
        gpu8=times[0], gpu32=times[1], saved_cycles=saved, reduction_percent=gain,
        raw_byte_exact_outputs=exact, readers_gpu8=results[0]['readers'], readers_gpu32=results[1]['readers'],
        original_I32_D128_total=original, versus_original_reduction_percent=100 * (1 - times[1]['total'] / original),
        full_frame_FPS_claimed=False, deployment_ready=False,
        limitations=[
            'One continuous two-renderer interval with original game/audio CPU work and bounded guest services; the second renderer is warm.',
            'The inherited analytic-reference differences are only explicitly classified framebuffer role/address and first-use-clear differences; native A/B is raw byte-exact.',
            'Synthetic fixed-base scan/audio traffic and functional guest voice/time services do not qualify the native OS/IRQ/PCM/display path or sustained FPS.',
            'At the final pre-edge boundary an instruction fetch remains outstanding. GPU fences/busy are complete; this is not global CPU bus quiescence.',
            'Cache size is the only model source delta. Physical resource/timing qualification and the separate proposed cache-storage implementation are not included.',
            'Prior analytic GPU32 evidence improved 1.77% but was too small and unfittable with the original D128 configuration; it was not a slowdown.',
            'No build, simulation, FPGA tool, source change, or deployment performed by this independent checker.'
        ]))
    (OUT / 'REPORT.txt').write_text(
        'Native registered-RVC I64/D64 GPU8 -> GPU32 paired review: PASS\n\n'
        'The sole model delta is render-cache SET_BITS6 -> 8; CPU, all module bytes, guest, ELF, host and runtime config are unchanged. '
        'All three 66-word entry snapshots, 46,036 raw command words, and all buffer dumps are byte-exact between runs. '
        'All observed bus/oracle errors are zero and fences362/363 complete with GPU idle at the two boundaries.\n\n'
        f'First interval: {times[0]["first_interval"]:,} -> {times[1]["first_interval"]:,} cycles ({gain["first_interval"]:.6f}% less).\n'
        f'Second warmed interval: {times[0]["second_interval"]:,} -> {times[1]["second_interval"]:,} cycles ({gain["second_interval"]:.6f}% less).\n'
        f'Total: {times[0]["total"]:,} -> {times[1]["total"]:,}; saved {saved["total"]:,} cycles ({gain["total"]:.6f}%).\n\n'
        'This is a bounded native application-window comparison, not an OS, physical display or FPS claim. '
        'The proposed storage rewrite and hardware fit are separate gates.\n')
    save(OUT / 'manifest.json', dict(passed=True,
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()}, evidence_sha256=evidence))
    print(OUT, sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
