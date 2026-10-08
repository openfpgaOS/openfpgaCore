#!/usr/bin/env python3
"""Bind literal ownership, paired cache protocol and exact GPU rendering proof."""
import json
import shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P=CAMP/'phase10';SOURCE=P/'texture-read-address-v1';OUT=P/'texture-read-review-v1'


def main():
    assert not (OUT/'manifest.json').exists() and not (SOURCE/'manifest.json').exists()
    candidate=SOURCE/'gpu_tex_cache.v'
    assert sha(candidate)=='c3fdc0adfe5ed583c37bf09ad3dbbbecb1e3c6ae1f5714cab5064eacc328e51c'
    literal=json.loads((SOURCE/'literal/result.json').read_text());assert literal['passed']
    protocols={str(b):json.loads((SOURCE/f'protocol-{b}/result.json').read_text())for b in [4,10,11]}
    assert all(v['passed'] for v in protocols.values())
    names=['result.json','chunk-result.json','resume-result.json','queue-result.json',
           'request-result.json','paths-result.json','attribute-result.json','origin-result.json',
           'ring-result.json','texture-result.json','boundaries-v3/result.json','boundaries-v3/resume-result.json']
    results={n:json.loads((OUT/n).read_text())for n in names}
    assert all(v['passed']for v in results.values())
    assert len(results['resume-result.json']['exact_cycle_and_output_pairs'])==12
    assert len(results['boundaries-v3/resume-result.json']['pairs'])==4
    inputs=json.loads((OUT/'texture-inputs.json').read_text())
    assert inputs['candidate_sha256']==sha(candidate)==sha(OUT/'model/frozen/common/gpu_tex_cache.v')
    assert inputs['reference_sha256']==sha(SOURCE/'baseline.v')
    assert inputs['baseline_manifest_sha256']==sha(P/'gpu-ring-equality-review-v16/manifest.json')
    totals=results['texture-result.json']['totals']
    pc={k:sum(v['counts'][k]for v in protocols.values())for k in protocols['10']['counts']}
    report=f'''Private texture port-A owned-address factor

PASS: only the read_set_a and read_word_a mux selects change. They now select
the incoming request address when req_valid is true, otherwise the latched
fill address. The FSM, ready/response equations, read/write enables, fill-write
priority, port B, memory dimensions, reset and flush behavior are unchanged.
No functional flip-flop, state, cycle or interface is added.

Ownership proof: read_en_a is accept OR prime_a. Accept implies req_valid and
excludes prime_a, so both sources select the incoming address. prime_a implies
S_FILL_OUT, lat_port=A and no flush, which guarantees req_ready=1. Its negated
accept term therefore implies req_valid=0, so both sources select the latched
address. In every other case the read is disabled. Tag initialization and
fill writes override the read address identically. Thus every enabled RAM
read/write address is exactly unchanged for arbitrary source addresses. Idle
address differences are intentional and unobserved; read outputs hold while
disabled. This proof does not assume a particular request producer or payload.

The literal source test checks all eight state encodings and all 256 relevant
control combinations with 64 address basis/complement/random cases each:
{literal['counts']['checks']:,} checks, {literal['counts']['reads']:,} owned reads,
{literal['counts']['writes']:,} writes, {literal['counts']['idle_diff']:,} unowned
address differences. The always-incoming-address negative control disagrees
on {literal['counts']['negative']:,} enabled prime reads.

Separate complete old/new cache instances pass the retained independent-memory
dual-port workload at SET_BITS 4, 10 and 11. All outputs, including invalid
payloads, every ready/valid cycle, shared AXI fills, state and enabled RAM
addresses match. Random conflicts, byte/halfword lanes, stalls, held port-B
responses and flush/reset are covered. Added directed fill-out replacement
requests cover all byte/halfword lanes, with the old response consumed while
the next request is accepted on that same edge.
  Compared cycles: {pc['checks']:,}
  Owned reads / writes: {pc['reads']:,} / {pc['writes']:,}
  Port-A / port-B no-request primes: {pc['prime_a']:,} / {pc['prime_b']:,}
  Accepted replacement on fill-out: {pc['accept_fill']:,}
  Held port-B cycles: {pc['held']:,}
  Reset / flush observations: {pc['resets']:,} / {pc['flushes']:,}
An initial coverage-only attempt passed functional comparison but did not
exercise fill-out replacement; it is preserved. The directed additions and
pre-edge coverage sampling close that gap without changing candidate RTL.

Actual GPU v16 baseline versus this texture-only change also passes twelve
native and four adversarial boundary replay pairs: exact color/depth/fence,
total and busy cycles, and batch counts. Both texture routes and quiet/nominal/
contended memory, row/column and affine/perspective modes are covered. All
inherited numerical, span, request, queue and ring assertions remain enabled.
  Live cache ownership comparisons: {totals['checks']:,}
  Enabled port-A reads: {totals['reads']:,}
  Write observations: {totals['writes']:,}
  Prime reads: {totals['prime']:,}
  Unowned idle-address differences: {totals['idle_diff']:,}

This targets the measured eight-level tag-hit/response/request/prime-to-RAM
address cone. The proof establishes behavior and unchanged cycles, not a
routed timing improvement. No map, fit, production or SD change was performed
by this component proof. The selected full-core timing gate remains separate.
'''
    (OUT/'REPORT.txt').write_text(report);shutil.copy2(__file__,OUT/'seal-recipe.py')
    files=[p for p in OUT.rglob('*')if p.is_file()and 'obj'not in p.parts]
    files += [p for p in SOURCE.rglob('*')if p.is_file()and 'obj'not in p.parts]
    files += [OUT/'model/obj/Vtb_gpu',SOURCE/'literal/obj/Vtb_address']
    files += [SOURCE/f'protocol-{b}/obj/Vgpu_tex_cache'for b in [4,10,11]]
    files += [P/'gpu-ring-equality-review-v16/manifest.json',P/'compact-timing-audit-v11/manifest.json',
              P/'compact-lod5-core-v11/final-manifest.json']
    h=Path(__file__).resolve().parent
    files += [h/n for n in ['phase10_texture_read_address.py','phase10_texture_read_protocol.py',
              'phase10_texture_read_acceptance.py','phase10_texture_read_seal.py',
              'phase10_gpu_ring_acceptance.py','phase10_gpu_origin_acceptance.py',
              'phase10_gpu_attribute_acceptance.py','phase10_gpu_paths_acceptance.py',
              'phase10_gpu_request_acceptance.py','phase10_gpu_queue_acceptance.py',
              'phase10_span_resume_acceptance.py','phase10_span_chunk_acceptance.py','phase10_span_acceptance.py']]
    assert all(p.is_file()for p in files)
    write(OUT/'manifest.json',dict(passed=True,passed_for_resource_screen=True,deployment_ready=False,
        candidate_sha256=sha(candidate),parent_sha256=sha(SOURCE/'baseline.v'),
        added_registers=0,added_cycles=0,exact_cycle_pixel_pairs=16,
        protocol_set_bits=[4,10,11],evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(files))}))
    write(SOURCE/'result.json',dict(passed=True,candidate_sha256=sha(candidate),
        exact_cycle_pixel_pairs=16,literal_checks=literal['counts']['checks'],
        protocol_counts=pc,proof_manifest_sha256=sha(OUT/'manifest.json'),
        proof_manifest=str((OUT/'manifest.json').relative_to(ROOT)),deployment_ready=False))
    top=[p for p in SOURCE.rglob('*')if p.is_file()and 'obj'not in p.parts]+[OUT/'manifest.json']
    write(SOURCE/'manifest.json',dict(passed=True,candidate_sha256=sha(candidate),
        evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(top))}))
    print(json.dumps(dict(proof_manifest_sha256=sha(OUT/'manifest.json'),
        component_manifest_sha256=sha(SOURCE/'manifest.json'))))


if __name__=='__main__':main()
