#!/usr/bin/env python3
"""Seal the selected software changes and their final validation evidence."""
from pathlib import Path
import hashlib
import json

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/quake2-batch-memory-20261008'
S = R / 'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
W = P / 'work/combined'

def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def read(p):
    return json.loads(p.read_text())

assert not (P / 'acceptance.json').exists()
evidence = {}
def bind(path):
    path = path.resolve()
    key = str(path.relative_to(R))
    digest = sha(path)
    if key in evidence:
        assert evidence[key] == digest
        return
    evidence[key] = digest
    if path.suffix == '.json':
        data = read(path)
        if isinstance(data, dict):
            for rel, expected in data.get('evidence_sha256', {}).items():
                child = R / rel
                assert sha(child) == expected, rel
                bind(child)

checks = [
    'components/batch-attrs-v1/manifest.json',
    'components/alias-positions-v2/tests.json',
    'alias-position-acceptance-v2.json',
    'components/clear-kick-v1/manifest.json',
    'gpu-cache-semantic-audit-manifest-v1.json',
    'combined-regressions-v1/manifest.json',
    'combined-independent-source-review-v1.json',
    'combined-review-v1/manifest.json',
    'supplemental-v1/batchmem_combined-validation/manifest.json',
    'supplemental-v1/batchmem_combined-validation/result.json',
    'full-semantics-v1/batchmem_combined/manifest.json',
    'final-checks-v1/manifest.json',
    'timing-review-v3/manifest.json',
    'timed-input-check-v1/manifest.json',
]
for rel in checks:
    path = P / rel
    assert read(path)['passed'], rel
    bind(path)
for rel in ['before.json', 'composition-combined/manifest.json',
            'composition-combined/source.patch', 'audit.txt',
            'cache-memory-audit.md', 'build-combined/result.json',
            'build-combined/command.json', 'build-combined/build.log',
            'build-combined/app.elf']:
    bind(P / rel)
# The alias tests retain their actual host/target programs and fixtures.
for path in (P / 'components/alias-positions-v2').iterdir():
    if path.is_file():
        bind(path)

composition = read(P / 'composition-combined/manifest.json')
before = read(P / 'before.json')
changed = []
for rel, digest in before['source_sha256'].items():
    assert sha(P / 'work/base' / rel) == digest, rel
    after = sha(W / rel)
    if digest != after:
        changed.append(rel)
        assert composition['files'][rel] == dict(before=digest, after=after), rel
        bind(W / rel)
assert set(changed) == set(composition['files'])

baseline = read(S / 'thorough_combined6-result.json')
final = read(S / 'batchmem_combined-result.json')
device = read(S / 'lightmap-result.json')
assert final['passed'] and final['elf_sha256'] == sha(P / 'build-combined/app.elf')
assert baseline['elf_sha256'] == before['baseline_elf_sha256']
assert final['modeled_cycles_per_frame'] < baseline['modeled_cycles_per_frame']
for name in ['thorough_combined6', 'lightmap', 'batchmem_combined']:
    bind(S / (name + '-result.json'))
matrix = []
for name in ['batch', 'alias_positions', 'alias_positions2', 'clear_kick', 'combined']:
    path = S / ('batchmem_' + name + '-result.json')
    data = read(path)
    assert data['passed']
    bind(path)
    matrix.append(dict(candidate=name, elf_sha256=data['elf_sha256'],
        cycles_per_frame=data['modeled_cycles_per_frame'],
        cpu_model_percent_saved=100 * (1 - data['modeled_cycles_per_frame'] / baseline['modeled_cycles_per_frame']),
        selected=name in composition['candidates'],
        note='Inactive geometry-capability gate; apparent small gain is not evidence of reuse.' if name == 'alias_positions' else ''))
bind(Path(__file__))
result = dict(passed=True, selected='combined', components=composition['candidates'],
    elf_sha256=final['elf_sha256'], baseline_elf_sha256=baseline['elf_sha256'],
    last_sd_elf_sha256=device['elf_sha256'],
    cpu_model_percent_saved=100 * (1 - final['modeled_cycles_per_frame'] / baseline['modeled_cycles_per_frame']),
    cpu_model_percent_saved_vs_last_sd=100 * (1 - final['modeled_cycles_per_frame'] / device['modeled_cycles_per_frame']),
    supplemental=read(P / 'supplemental-v1/batchmem_combined-validation/result.json')['results'],
    candidate_matrix=matrix, changed_paths=sorted(changed),
    unchanged_source_guards=len(before['source_sha256']) - len(changed),
    evidence_sha256=evidence,
    limits=['CPU model and conditional isolated GPU replay are not measured Pocket FPS.',
            'Fixed workloads do not cover every level, prolonged combat or physical shared-memory contention.',
            'Core, clocks and FPGA resource use unchanged; no seed sweep. Deployment recorded separately.'])
(P / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k not in ['evidence_sha256', 'candidate_matrix']}, indent=2))
print('Bound evidence files:', len(evidence))
