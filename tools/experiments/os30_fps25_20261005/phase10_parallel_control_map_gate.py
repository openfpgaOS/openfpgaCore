#!/usr/bin/env python3
"""Bind exact CPU/GPU/cache native comparison and independent component gates."""
import json
import pathlib
import phase10_parallel_control_map as m

R, P, O, sha, put = m.R, m.P, m.O, m.sha, m.put
d = m.checked()
W = P / 'native-renderer-window-v1'
new = W / 'run-parallel-control-v1'
old = W / 'run-interval-control-cones-v1'
gates = {
    new / 'manifest.json': 'b607caf4482e1f92708ddc4d92607bd834bebb68595d97a4011ed9475c26e3c8',
    P / 'cpu-parallel-control-source-review-v1/manifest.json': 'db4312cf2d7fe8aeee2a7950f43c9c194de288ae329fa85dabf7ef96354ce776',
    P / 'gpu-parallel-control-v1/manifest.json': '88542a32028d1d923e4ac0e83c750c99d6d6710724480380922e097926684252',
    P / 'gpu-light-prefix-source-review-v1/manifest.json': '3474c14b4b335d2685ca1a296f5dbd2c9cd7fa68ccd9c5e499308fc60665bd29',
    P / 'render-cache-speculative-read-source-review-v1/manifest.json': '245790186d3f48318578097c0a6a6ba8171a44bd52bf8aab936e3d5d0e9e12c0',
    P / 'native-parallel-control-check-v1/manifest.json': '26f66d654c8c948be86816ba1c00b6f118c142baf398db1b60ccf15de2c71688',
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
models = [W / 'model-control-cones-v1/prepared.json',
          W / 'model-parallel-control-v1/prepared.json']
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
