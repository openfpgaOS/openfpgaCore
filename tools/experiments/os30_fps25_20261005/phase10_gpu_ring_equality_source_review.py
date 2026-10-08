#!/usr/bin/env python3
"""Independent exact-source and evidence review for the grouped ring equality."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'gpu-ring-equality-v16'
TEST=P/'gpu-ring-equality-review-v16'
O=P/'gpu-ring-equality-source-review-v16'
BASE=P/'compact-lod5-core-v10/frozen/src/fpga/common/gpu_core.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def require(ok,why):
    if not ok:raise RuntimeError(why)

def main():
    old=BASE.read_text();new=(C/'gpu_core.v').read_text()
    original='wire ring_empty = (ring_rdptr == command_wrptr);'
    replacement='''// Four independent six-input equalities keep this late status/control
// comparison parallel. The ring remains 4096 words; no pointer/state changes.
(* keep *) wire [3:0] ring_pointer_equal_group;
assign ring_pointer_equal_group[0] = (ring_rdptr[2:0] == command_wrptr[2:0]);
assign ring_pointer_equal_group[1] = (ring_rdptr[5:3] == command_wrptr[5:3]);
assign ring_pointer_equal_group[2] = (ring_rdptr[8:6] == command_wrptr[8:6]);
assign ring_pointer_equal_group[3] = (ring_rdptr[11:9] == command_wrptr[11:9]);
wire ring_empty = &ring_pointer_equal_group;'''
    require(old.count(original)==1 and old.replace(original,replacement)==new,'Candidate is not the single expected substitution')
    require(old.count('localparam RING_ADDR_BITS = 12;')==1,'Ring width differs')
    prep=read(C/'prepared.json');literal=read(C/'literal/result.json')
    require(prep['parent_sha256']==sha(BASE) and prep['candidate_sha256']==sha(C/'gpu_core.v'),'Source binding differs')
    require(literal['passed'] and literal['all_pointer_pairs']==4096*4096 and literal['equal_pairs']==4096,'Exhaustive equality gate differs')
    require(literal['missing_high_group_negative_mismatches']==28672,'Negative control differs')
    require(literal['candidate_sha256']==sha(C/'gpu_core.v') and literal['parent_sha256']==sha(BASE),'Literal proof uses another source')
    probe=(C/'literal/tb.v').read_text()
    require(replacement in probe and original.replace('ring_empty','old_empty_wire') in probe,'Literal proof does not contain both exact source equations')
    m=read(TEST/'manifest.json')
    require(m['passed'] and m['passed_for_resource_screen'] and m['exact_cycle_pixel_pairs']==16,'Native GPU proof not qualified')
    require(m['candidate_sha256']==sha(C/'gpu_core.v'),'Native proof candidate differs')
    for n,h in m['evidence_sha256'].items():require(sha(ROOT/n)==h,'GPU proof evidence drift: '+n)
    rings=read(TEST/'ring-result.json')
    require(rings['passed'] and rings['pairs']==16,'Live ring proof failed')
    for n in ('cycles','empty','nonempty','status','resets','wraps'):require(rings['totals'][n]>0,'Live coverage missing: '+n)
    # No reference to a selected clock, MMIO mux or memory overlay is altered:
    # exact reconstruction leaves all consumers, registers and assignments
    # outside this one unsigned equality byte-for-byte identical.
    O.mkdir(exist_ok=False)
    review=dict(passed=True,source_review_only=True,no_duplicate_simulations=True,
        candidate_sha256=sha(C/'gpu_core.v'),parent_sha256=sha(BASE),
        exact_single_substitution=True,fixed_pointer_width_bits=12,
        equivalence_lemma='The fixed 12-bit unsigned words are equal iff each of their four disjoint 3-bit slices is equal; conjunction is the original equality.',
        all_consumers_and_sequential_logic_unchanged=True,added_registers=0,added_states=0,added_cycles=0,
        exhaustive_pointer_pairs=literal['all_pointer_pairs'],native_exact_cycle_pixel_pairs=16,
        live_ring_coverage=rings['totals'],arithmetic_MMIO_overlay_and_CPU_unchanged=True,
        timing_gain_not_established=True,no_map_or_fit=True,deployment_ready=False)
    (O/'review.json').write_text(json.dumps(review,indent=2)+'\n')
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    evidence=[BASE,C/'gpu_core.v',C/'prepared.json',C/'literal/result.json',C/'literal/tb.v',
        C/'literal/main.cpp',TEST/'manifest.json',TEST/'ring-result.json',Path(__file__)]
    (O/'manifest.json').write_text(json.dumps(dict(files={p.name:sha(p) for p in O.iterdir() if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}),indent=2)+'\n')
    print(sha(O/'manifest.json'))

if __name__=='__main__':main()
