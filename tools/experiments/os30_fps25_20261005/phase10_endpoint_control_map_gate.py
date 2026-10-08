#!/usr/bin/env python3
"""Bind exact CPU/GPU/cache native comparison and independent component gates."""
import json
import pathlib
import phase10_endpoint_control_map as m

R, P, O, sha, put = m.R, m.P, m.O, m.sha, m.put
d = m.checked()
W = P / 'native-renderer-window-v1'
new = W / 'run-endpoint-control-v1'
old = W / 'run-parallel-control-v1'
gates = {
    new / 'manifest.json': '9be9679f2611034feaa0035622511438c672934c024bca7d89c179d643c5bdd8',
    P / 'cpu-endpoint-control-source-review-v1/manifest.json': '4099c85b8e9966d86129a3f7687da8dafec4da80fc8b0bc92d1ffb3ed1b4a7c8',
    P / 'gpu-endpoint-control-v1/manifest.json': '2e6690eaf44158ff8ca4cc654473235ba7a7bfff03a67b897910623fa136e213',
    P / 'gpu-span-step-enable-source-review-v1/manifest.json': '4023290fbeef877aeb0e31de1e9ecb4fa3c16b51a1d9e1b5ae2303f315b5572e',
    P / 'gpu-endpoint-replay-v1/manifest.json': '6995c3c7716379c4f00a7a084f242f31a7ca53531d539b3a373e80ce2d72ede1',
    P / 'render-cache-owned-write-slot-source-review-v1/manifest.json': '3e4260303b40fe004784201a62a66ad6161fb2804c451a0a6a0daea1634b0749',
    P / 'native-endpoint-control-check-v1/manifest.json': 'cdc704a2c7bfc2d197cada839e3728c237d66045abdd2fd01b5a80b69a09437c',
}
assert all(len(v)==64 for v in gates.values()), 'Qualification pins pending; map gate cannot be generated'

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
models = [W / 'model-parallel-control-v1/prepared.json',
          W / 'model-endpoint-control-v1/prepared.json']
ma, mb = [json.loads(p.read_text()) for p in models]
a, b = ma['source_sha256'], mb['source_sha256']
assert a.keys() == b.keys()
delta = sorted(n for n in a if a[n] != b[n])
assert set(delta) == {'frozen/' + m.CPU, 'frozen/' + m.GPU, 'frozen/' + m.CACHE}
assert b['frozen/' + m.CPU] == m.CPU_SHA and b['frozen/' + m.GPU] == m.GPU_SHA
assert b['frozen/' + m.CACHE] == m.CACHE_SHA
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
    prepared_sha256=sha(O / 'prepared.json'), cpu_sha256=m.CPU_SHA, gpu_sha256=m.GPU_SHA, cache_sha256=m.CACHE_SHA,
    native_model_prepared=str(models[1].relative_to(R)),
    scope='Root-authorized one resource map after exact17-file CPU/GPU/cache native comparison and independent component source gates. No fitter.',
    exact_native_artifacts=names, source_delta=delta, checked_manifest_bindings=count,
    evidence_sha256={str(p.relative_to(R)): h for p, h in proofs.items()}))
print('GATE', sha(O / 'native-map-gate.json'))
