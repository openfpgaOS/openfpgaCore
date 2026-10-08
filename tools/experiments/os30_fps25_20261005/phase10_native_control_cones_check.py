#!/usr/bin/env python3
"""Bounded independent source and byte/cycle comparison; does not run models."""
from pathlib import Path
import hashlib
import json
import shutil

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
W = P / 'native-renderer-window-v1'
O = P / 'native-control-cones-check-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())
evidence = {}


def bind(p):
    evidence[str(p.relative_to(R))] = sha(p)


def check_manifest(p, expected=None):
    if expected:
        assert sha(p) == expected, p
    d = read(p)
    for key, base in [('files', p.parent), ('inputs_sha256', R), ('evidence_sha256', R)]:
        for name, digest in d.get(key, {}).items():
            q = base / name
            assert sha(q) == digest, q
            bind(q)
    bind(p)
    return d


def main():
    check_manifest(P / 'cpu-control-compose-source-review-v1/manifest.json',
                   '28fa4eaef99154c359e120b05ebff994049a4e673e07cb0d6d360fecec09683b')
    check_manifest(P / 'gpu-control-owner-source-review-v1/manifest.json',
                   '6347bc0eb02905619d4a31aa03f71bf9f69fa463f300fc56a3836aad83c509b5')
    check_manifest(P / 'native-texture-owned-check-v1/manifest.json',
                   'f31ff8b52ac83c4c8927ac18872ccf91bc2d58c5f56bdc868b84004457dd866a')
    models = [W / 'model-cache64-render32-forward-texture-v1', W / 'model-control-cones-v1']
    runs = [W / 'run-interval-render32-forward-texture-v1', W / 'run-interval-control-cones-v1']
    runners = [W / 'runner-interval-render32-forward-texture-v1', W / 'runner-interval-control-cones-v1']
    prepared = [read(p / 'prepared.json') for p in models]
    for model, data in zip(models, prepared):
        for name, digest in data['source_sha256'].items():
            assert sha(model / name) == digest, name
            bind(model / name)
        assert read(model / 'build.json')['returncode'] == 0
        bind(model / 'prepared.json'); bind(model / 'build.json')
    a, b = prepared
    assert len(a['source_sha256']) == len(b['source_sha256']) == 165
    assert set(a['source_sha256']) == set(b['source_sha256'])
    delta = {n: [a['source_sha256'][n], h] for n, h in b['source_sha256'].items() if h != a['source_sha256'][n]}
    assert delta == {
        'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v': [
            '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534',
            '878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'],
        'frozen/src/fpga/common/gpu_core.v': [
            '238f44273b6bb0188447485d1501e898c8df0c1cb9b758841e8af7ceac414adb',
            'e75ac93db5483ee8525c44ef1aeed4e610f8deecf8fa42765997367873dd4f15'],
    }
    for key in ['defs', 'cache_layout', 'gpu_cache_kib', 'rvc_pipeline', 'selected_qsf_sha256']:
        assert a[key] == b[key], key
    commands = [[s.replace(str(model), '<MODEL>') for s in d['command']] for model, d in zip(models, prepared)]
    assert commands[0] == commands[1]
    assert b['control_components']['parent_prepared_sha256'] == sha(models[0] / 'prepared.json')
    for run, model in zip(runs, models):
        m = check_manifest(run / 'manifest.json')
        assert m['model_prepared_sha256'] == sha(model / 'prepared.json')
        result = read(run / 'result.json')
        assert result['status'] == read(run / 'process.json')['returncode'] == 0
        assert all(result[k] == 0 for k in ['write_errors', 'scan_errors', 'cram_errors'])
        assert all(q['errors'] == 0 for q in result['readers'].values())
        assert result['upload_words'] == 46036 and result['fence'] == 363
    assert sha(runs[1] / 'manifest.json') == '8a9e0b94cf337ab5d5f079f1946ecdb55ac89b8c1ff28fed41cfeea82aa0ddac'
    for name in ['main.cpp', 'input.cfg', 'recipe.py']:
        assert (runners[0] / name).read_bytes() == (runners[1] / name).read_bytes(), name
        bind(runners[0] / name); bind(runners[1] / name)
    exact = ['result.json', 'boundaries.tsv', 'pc.csv', 'mmio.tsv', 'commands.bin',
             'entry-regs.bin', 'entry-2.bin', 'entry-3.bin', 'frame.bin']
    exact += [f'frame-{i}{suffix}.bin' for i in (1, 2) for suffix in ('', '-buffer0', '-buffer1', '-buffer2')]
    assert len(exact) == 17
    artifact_checks = {}
    for name in exact:
        assert (runs[0] / name).read_bytes() == (runs[1] / name).read_bytes(), name
        artifact_checks[name] = sha(runs[1] / name)
        bind(runs[0] / name); bind(runs[1] / name)
    rows = [list(map(int, line.split())) for line in (runs[1] / 'boundaries.tsv').read_text().splitlines()]
    assert [q[1] for q in rows] == [1380, 3723654, 7870417]
    assert [q[3:] for q in rows] == [[0, 361, 0], [23036, 362, 0], [46036, 363, 0]]
    assert read(runs[0] / 'verification.json') == read(runs[1] / 'verification.json')
    verification = read(runs[1] / 'verification.json')
    assert verification['matching_actual_buffers'] == {'1': [0], '2': [1]}
    assert verification['exact']['frame-2.bin'] is False
    assert verification['exact']['commands.bin'] is False
    bind(Path(__file__))
    review = dict(passed=True, source_delta=delta, source_hashes_checked=330,
        exact_artifact_sha256=artifact_checks, exact_artifact_count=17,
        model_command_and_parameters_unchanged=True, host_guest_inputs_unchanged=True,
        first_interval_cycles=3722274, second_interval_cycles=4146763,
        total_renderer_entry_interval_cycles=7869037, model_total_cycles=7870418,
        cycle_change=0, no_new_execution=True, deployment_ready=False,
        evidence_hashes_checked=len(evidence),
        limitations=[
            'This is a paired two-interval native replay, not full-game or Pocket FPS qualification.',
            'The analytic reference second-frame role/address and first-use-clear differences remain unchanged. Exactness here is raw native candidate versus native control; no output normalization is performed.',
            'The host stops at the third renderer entry. Identical outstanding I and M1 prefetch requests are present, so this is not a drained-system completion proof.',
            'Bounded guest services, synthetic audio traffic and cold initial caches remain the inherited fixture contract; no new native OS, IRQ, PCM or physical timing claim is added.',
            'Resource mapping and routed timing remain separate gates.'
        ])
    O.mkdir(exist_ok=False)
    shutil.copyfile(__file__, O / 'recipe.py')
    (O / 'result.json').write_text(json.dumps(review, indent=2, sort_keys=True) + '\n')
    (O / 'REPORT.txt').write_text(
        'PASS: only CPU82b7 ->878e and GPU238f ->e75a differ in165 model inputs.\n'
        'All17 raw result artifacts are byte/cycle identical to the qualified native control.\n'
        'Renderer-entry intervals:3,722,274 +4,146,763 =7,869,037 cycles.\n'
        'No simulation was repeated. Existing analytic-reference role/clear and stop-boundary limitations remain explicit.\n'
        'This is a compatibility gate, not a physical timing or device FPS result.\n')
    files = {p.name: sha(p) for p in O.iterdir() if p.is_file()}
    (O / 'manifest.json').write_text(json.dumps(dict(passed=True, files=files,
        evidence_sha256=evidence), indent=2, sort_keys=True) + '\n')
    print('PASS', len(evidence), 'evidence files;', len(exact), 'exact artifacts')
    print('MANIFEST', sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
