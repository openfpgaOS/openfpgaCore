#!/usr/bin/env python3
"""Bind exact CPU/GPU-only native comparison and independent component gates."""
import json
import pathlib
import phase10_control_cones_compose_map as m

R, P, O, sha, put = m.R, m.P, m.O, m.sha, m.put
d = m.checked()
W = P / 'native-renderer-window-v1'
new = W / 'run-interval-control-cones-v1'
old = W / 'run-interval-render32-forward-texture-v1'
gates = {
    new / 'manifest.json': '8a9e0b94cf337ab5d5f079f1946ecdb55ac89b8c1ff28fed41cfeea82aa0ddac',
    P / 'cpu-control-compose-source-review-v1/manifest.json': '28fa4eaef99154c359e120b05ebff994049a4e673e07cb0d6d360fecec09683b',
    P / 'gpu-control-owner-source-review-v1/manifest.json': '6347bc0eb02905619d4a31aa03f71bf9f69fa463f300fc56a3836aad83c509b5',
}
proofs = dict(gates)
count = 0
for p, expected in gates.items():
    assert sha(p) == expected, p
    proof = json.loads(p.read_text())
    assert proof.get('passed', True)
    for key in ('evidence_sha256', 'inputs_sha256'):
        for name, value in proof.get(key, {}).items():
            assert sha(R / name) == value, (p, name)
            count += 1
    for name, value in proof.get('files', {}).items():
        assert sha(p.parent / name) == value, (p, name)
        count += 1
names = json.loads((P / 'native-cpu-load-forward-check-v1/result.json').read_text())['exact_artifacts']
assert len(names) == 17
for name in names:
    assert (old / name).read_bytes() == (new / name).read_bytes(), name
    proofs[old / name] = sha(old / name)
    proofs[new / name] = sha(new / name)
models = [W / 'model-cache64-render32-forward-texture-v1/prepared.json',
          W / 'model-control-cones-v1/prepared.json']
ma, mb = [json.loads(p.read_text()) for p in models]
a, b = ma['source_sha256'], mb['source_sha256']
assert a.keys() == b.keys()
delta = sorted(n for n in a if a[n] != b[n])
assert set(delta) == {'frozen/' + m.CPU, 'frozen/' + m.GPU}
assert b['frozen/' + m.CPU] == m.CPU_SHA and b['frozen/' + m.GPU] == m.GPU_SHA
assert mb['cpu_sha256'] == m.CPU_SHA and mb['gpu_cache_kib'] == 32
for p, model in zip(models, (ma, mb)):
    for name, expected in model['source_sha256'].items():
        assert sha(p.parent / name) == expected, (p, name)
    proofs[p] = sha(p)
for name, expected in d['source_sha256'].items():
    if name != m.TOP:
        assert sha(models[1].parent / 'frozen' / name) == expected, name
proofs[pathlib.Path(__file__)] = sha(pathlib.Path(__file__))
put(O / 'native-map-gate.json', dict(
    passed_for_map=True, native_byte_cycle_equivalence_passed=True,
    prepared_sha256=sha(O / 'prepared.json'), cpu_sha256=m.CPU_SHA, gpu_sha256=m.GPU_SHA,
    native_model_prepared=str(models[1].relative_to(R)),
    scope='Root-authorized one resource map after exact17-file CPU/GPU-only native comparison and independent component source gates. No fitter.',
    exact_native_artifacts=names, source_delta=delta, checked_manifest_bindings=count,
    evidence_sha256={str(p.relative_to(R)): h for p, h in proofs.items()}))
print('GATE', sha(O / 'native-map-gate.json'))
