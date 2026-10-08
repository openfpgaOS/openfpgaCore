#!/usr/bin/env python3
"""Add only a known-ineligible dispatch gate to the frozen indexed batch v1."""
import difflib
import hashlib
import json
from pathlib import Path
from prepare_lighting_layout import ROOT, OUT, HASHES

PARENT = OUT / 'indexed-batch-v1'
DEST = OUT / 'indexed-batch-v2'
EXPECTED_PC = '983892c9b5986b7961982ce12fdf7bda8f42aa0a986938674e16591e74e05592'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
OLD = '''#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
                {
                    Gfx *next = gfx_cpu_indexed_run(cmd);
                    if (next != cmd) { cmd = next; continue; }
                }
#endif'''
NEW = '''#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
                /* A valid ineligible material cannot emit a cached run.
                 * Keep its original handler without speculative culling. */
                if (!triangle_material_ready || triangle_vc_elig) {
                    Gfx *next = gfx_cpu_indexed_run(cmd);
                    if (next != cmd) { cmd = next; continue; }
                }
#endif'''


def main():
    assert not DEST.exists(), DEST
    parent = json.loads((PARENT/'manifest.json').read_text())
    expected = parent['source_files']
    assert expected['gfx_pc.c'] == EXPECTED_PC
    for name in HASHES: assert sha(PARENT/name) == expected[name], name
    original = (PARENT/'gfx_pc.c').read_text()
    assert original.count(OLD) == 2
    candidate = original.replace(OLD, NEW)
    assert candidate.replace(NEW, OLD) == original
    DEST.mkdir()
    for name in HASHES: (DEST/name).write_bytes((PARENT/name).read_bytes())
    (DEST/'gfx_pc.c').write_text(candidate)
    (DEST/'prepare.py').write_bytes(Path(__file__).read_bytes())
    (DEST/'change-from-v1.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='indexed-batch-v1/gfx_pc.c', tofile='indexed-batch-v2/gfx_pc.c')))
    after = {name:sha(DEST/name) for name in HASHES}
    assert [name for name in HASHES if after[name] != expected[name]] == ['gfx_pc.c']
    profile = ROOT/'docs/measurements/os30-fps25-20261005/validation/profile-delta-phase2-indexed-batch-v1-fps.json'
    m = dict(candidate='indexed-batch-v2', parent='indexed-batch-v1', baseline='render-v6',
        qualified=False, runtime_qualified=False, parent_files=expected, source_files=after,
        generator_sha256=sha(Path(__file__)), change='Only gate the two existing CPU indexed-run dispatch calls when the current material memo is already valid and cache-ineligible.',
        exact_reversible_two_dispatch_replacements=True, other_six_inputs_byte_identical=True,
        preserved=['Cold material still enters the original v1 run and establishes eligibility at its first visible triangle.',
                   'Known eligible material enters v1 unchanged. No per-run payload, culling, batching, math, alpha or backend change.',
                   'Known ineligible material uses the original TRI1/TRI2 handlers. The original material invalidation paths make stale eligibility unobservable to this guard.',
                   'An all-culled ineligible run now executes per-triangle generic culling, which preserves rejection counters and avoids material preparation just as v1 did.'],
        evidence={'profile_delta_file':str(profile.relative_to(ROOT)), 'profile_delta_sha256':sha(profile),
                  'v1_gameplay_run_calls':143583, 'v1_gameplay_backend_batch_calls':53402,
                  'v1_gameplay_generic_triangle_calls':80340,
                  'limits':'Aggregate profiles do not separate all-culled runs from failed runs or identify how many failures already had a known ineligible material. This gate targets a source-proven redundant subset; no gain is claimed.'},
        remaining='Independent two-site guard review and root paired capture. Parent exact16GPU replay pass does not qualify the newly compiled v2 artifact.')
    (DEST/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
    print(json.dumps({'candidate':str(DEST), 'gfx_pc_sha256':after['gfx_pc.c'],'qualified':False},indent=2))

if __name__ == '__main__': main()
