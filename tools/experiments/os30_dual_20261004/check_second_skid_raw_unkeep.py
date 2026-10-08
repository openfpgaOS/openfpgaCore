#!/usr/bin/env python3
"""Independent exact identity audit for the thirteen attribute-only removals."""
from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
OLD=ART/'decode-second-skid-draft-v1/immediate/VexiiRiscv.v'
NEW=ART/'decode-second-skid-raw-unkeep-v1/immediate/VexiiRiscv.v'
OUT=ART/'decode-second-skid-raw-unkeep-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    assert sha(OLD)=='f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
    assert sha(NEW)=='0cc8133832fc0f36121c241d7f84efe9c2b291ea70b10f28dfe255d9145820f1'
    old,new=OLD.read_text(),NEW.read_text()
    names=[f'aligner_raw_upper_sel_{lane}_{pos}' for lane,positions in [(0,range(7)),(1,range(1,7))] for pos in positions]
    original=old;reverse=new
    for name in names:
        a=f'  (* keep , syn_keep *) wire {name} /* synthesis syn_keep = 1 */;'
        b=f'  wire {name};'
        assert old.count(a)==new.count(b)==1
        old=old.replace(a,b,1);reverse=reverse.replace(b,a,1)
    assert old==new and reverse==original
    qual=OLD.with_name('qualification.json')
    assert qual.is_file()
    OUT.mkdir(exist_ok=False)
    report=dict(passed=True,parent_sha256=sha(OLD),candidate_sha256=sha(NEW),exact_complete_source_reconstruction=True,
        removed_attributes_only_on=names,all_equations_and_registers_byte_identical=True,
        exact_inverse_restores_parent=True,identical_four_state_behavior_for_all_inputs_and_states=True,
        parent_qualification=str(qual),parent_qualification_sha256=sha(qual),
        no_duplicate_simulations_required=True,physical_benefit='Unmeasured; synthesis attributes can change area and timing.')
    (OUT/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
if __name__=='__main__':main()
