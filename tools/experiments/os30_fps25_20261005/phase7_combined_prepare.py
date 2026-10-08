#!/usr/bin/env python3
"""Compose prepared-v3 records with the independently checked fallback reuse."""
import difflib
import hashlib
import json
from pathlib import Path
import phase7_rejection_prepare as rejection
from phase7_eligibility_tests import function

OUT = rejection.OUT.with_name('combined-v1')
PREPARED = OUT.with_name('prepared-v3')
sha = lambda b: hashlib.sha256(b).hexdigest()


def main():
    original = (PREPARED / 'gfx_pc.c').read_text()
    candidate = rejection.transform(original)
    run = function(candidate, 'gfx_cpu_indexed_run')
    # Preparation and batch emission must finish before fallback can flush
    # attributes, clip vertices, prepare material, or alter slot-stale state.
    assert run.index('for (unsigned i = 0; prepared;') < run.index('gfx_gpu_vtx_cache_batch(')
    assert run.index('gfx_gpu_vtx_cache_batch(') < run.index('gfx_cpu_push_checked(')
    assert run.index('vc_slot_stale = stale;') < run.index('gfx_cpu_push_checked(')
    expected = json.loads((PREPARED / 'build-normal.json').read_text())['inputs']
    inputs = json.loads((OUT.parent / 'baseline.json').read_text())['inputs']
    OUT.mkdir(exist_ok=True)
    before, after = {}, {}
    for name in inputs:
        data = (PREPARED / name).read_bytes()
        assert sha(data) == expected[name]['sha256'], name
        before[name] = sha(data)
        if name == 'gfx_pc.c':
            data = candidate.encode()
        if name == 'perf_pocket.c':
            assert data.count(b'PERF F8 SEND') == 1
            data = data.replace(b'PERF F8 SEND', b'PERF F9 TRI')
        dest = OUT / name
        assert not dest.exists() or dest.read_bytes() == data, dest
        dest.write_bytes(data)
        after[name] = sha(data)
    (OUT / 'combined-from-prepared.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='prepared-v3/gfx_pc.c', tofile='combined-v1/gfx_pc.c')))
    record = dict(parent='prepared-v3', parent_sha256=before, candidate_sha256=after,
        recipe_sha256=sha(Path(__file__).read_bytes()),
        rejection_transform_sha256=sha(Path(rejection.__file__).read_bytes()),
        changes='Prepared vertex records plus completed-rejection reuse; diagnostic header PERF F9 TRI with unchanged snapshot version8.',
        deferred_preparation_precedes_fallback=True, qualified=False)
    (OUT / 'combined-source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
