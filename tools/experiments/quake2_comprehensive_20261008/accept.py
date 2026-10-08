#!/usr/bin/env python3
"""Bind final source, model screens, arithmetic tests and actual GPU gates."""
from pathlib import Path
import hashlib
import json

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-comprehensive-20261008'
S = R / 'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
W = P / 'work/combined6'

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def read(path):
    return json.loads(path.read_text())

assert not (P / 'acceptance.json').exists()
evidence = {}

def bind(path):
    path = path.resolve()
    key = str(path.relative_to(R))
    if key in evidence:
        assert sha(path) == evidence[key]
        return
    evidence[key] = sha(path)
    if path.suffix == '.json':
        data = read(path)
        if isinstance(data, dict):
            for rel, expected in data.get('evidence_sha256', {}).items():
                child = R / rel
                assert sha(child) == expected, rel
                bind(child)

checks = [
    'gpu-final6-qualification-manifest-v1.json',
    'final-checks-combined6/manifest.json',
    'final-checks-combined6/result.json',
    'supplemental-v1/manifest.json',
    'supplemental-v1/observer-v2/combined6-vs-baseline/result.json',
    'combined6-independent-source-review-v1.json',
    'components/floor-leaf-v1/tests.json',
    'components/alias-direct-v1/tests.json',
    'components/packed-v2/manifest.json',
    'components/hud-v1/manifest.json',
    'components/plane-mask-v1/tests.json',
    'components/plane-mask-v1/full-demo-parity.json',
]
for rel in checks:
    path = P / rel
    assert read(path)['passed'], rel
    bind(path)
for rel in ['before.json', 'composition-combined6/manifest.json', 'audit.txt',
            'cpu-game-audio-visibility-audit.txt', 'cpu-game-audio-visibility-audit.json',
            'renderer-audit-v1/manifest.json', 'build-combined6/result.json', 'build-combined6/app.elf']:
    bind(P / rel)

composition = read(P / 'composition-combined6/manifest.json')
before = read(P / 'before.json')['source_sha256']
changed = []
for rel, old_hash in before.items():
    assert sha(P / 'work/base' / rel) == old_hash, rel
    new_hash = sha(W / rel)
    if new_hash != old_hash:
        changed.append(rel)
        assert composition['files'][rel] == dict(before=old_hash, after=new_hash), rel
        bind(W / rel)
assert set(changed) == set(composition['files'])

base = read(S / 'pass2_trio-result.json')
final = read(S / 'thorough_combined6-result.json')
device = read(S / 'lightmap-result.json')
assert final['passed'] and final['elf_sha256'] == sha(P / 'build-combined6/app.elf')
bind(S / 'pass2_trio-result.json')
bind(S / 'lightmap-result.json')
matrix = []
for suffix in ['floor_leaf', 'alias', 'clear', 'packed', 'packed2', 'hud', 'lightmap_rect', 'plane_mask', 'combined5', 'combined6']:
    path = S / ('thorough_' + suffix + '-result.json')
    result = read(path)
    assert result['passed']
    bind(path)
    matrix.append(dict(candidate=suffix, cycles_per_frame=result['modeled_cycles_per_frame'],
        cpu_time_saved_percent=100*(1-result['modeled_cycles_per_frame']/base['modeled_cycles_per_frame']),
        selected_component=suffix in composition['candidates'], elf_sha256=result['elf_sha256']))
bind(Path(__file__))
result = dict(passed=True, selected='combined6', elf_sha256=final['elf_sha256'],
    baseline_elf_sha256=base['elf_sha256'], last_sd_elf_sha256=device['elf_sha256'],
    cpu_model_percent_saved=100*(1-final['modeled_cycles_per_frame']/base['modeled_cycles_per_frame']),
    cpu_model_percent_saved_vs_last_sd=100*(1-final['modeled_cycles_per_frame']/device['modeled_cycles_per_frame']),
    changed_paths=sorted(changed), unchanged_source_guards=len(before)-len(changed),
    candidate_matrix=matrix, evidence_sha256=evidence,
    limits=['CPU and GPU work reductions are separate simulation results, not measured Pocket FPS.',
            'Three fixed workloads do not cover prolonged combat, music, cinematics or physical contention.',
            'Core and clock unchanged; no FPGA seed sweep. SD deployment is recorded separately.'])
(P / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['candidate_matrix','evidence_sha256']}, indent=2))
print('Bound evidence files:', len(evidence))
