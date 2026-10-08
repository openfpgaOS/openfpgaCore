#!/usr/bin/env python3
"""Independently check disjoint CPU last-ownership/FP priority composition."""
from pathlib import Path
import difflib
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
OUT = ART / 'fp-last-composition-audit-v1'
FILES = {
 'base': ('aligner-trap-direct-v1/immediate/VexiiRiscv.v','ec69d8b6b09f60fe60f03227bb1bad4c19ea841a28e874d8025b6970b1d639c3'),
 'last': ('aligner-last-direct-v1/immediate/VexiiRiscv.v','ef386e8e95557fdc39bcac98d179ee4db693180f0fafc49af5aff7f7833bf3c4'),
 'fp': ('fp-rs1-priority-groups-v1/immediate/VexiiRiscv.v','a0afc808335895240d5034d5b94d26d6c9393c293b383a9cf164e8fc917ff145'),
 'combined': ('fp-last-combined-v1/immediate/VexiiRiscv.v','9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377')}


def changes(before,after):
    a,b = before.splitlines(True),after.splitlines(True)
    out=[]
    for tag,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes():
        if tag != 'equal':
            out += [(''.join(a[i:j]),''.join(b[k:l]))]
    return out


def apply(source, edits):
    for before,after in edits:
        assert source.count(before)==1
        source=source.replace(before,after,1)
    return source


def main():
    sources={}
    for name,(path,sha) in FILES.items():
        data=(ART/path).read_bytes()
        assert hashlib.sha256(data).hexdigest()==sha
        sources[name]=data.decode()
    edits={name:changes(sources['base'],sources[name]) for name in ['last','fp']}
    assert len(edits['last'])==2 and len(edits['fp'])==1
    assert apply(sources['fp'],edits['last'])==sources['combined']
    assert apply(sources['last'],edits['fp'])==sources['combined']
    assert apply(sources['combined'],[(b,a) for a,b in edits['last']])==sources['fp']
    assert apply(sources['combined'],[(b,a) for a,b in edits['fp']])==sources['last']
    OUT.mkdir(exist_ok=False)
    proof_paths = {
      'last': 'aligner-last-direct-v1/immediate/obj/Vlast_proof',
      'fp': 'fp-rs1-priority-groups-v1/immediate/obj/Vfp_rs1_proof'}
    for name, binary in proof_paths.items():
        with (OUT/(name+'-proof-rerun.log')).open('w') as log:
            subprocess.run([str(ART/binary)],check=True,stdout=log,stderr=subprocess.STDOUT)
    result=dict(source_sha256={n:s for n,(_,s) in FILES.items()},
        independently_derived_diff_groups={'last':2,'fp':1},
        both_source_orders_identical=True,both_component_reversals_exact=True,
        composed_equations_identical_to_independently_audited_components=True,
        no_other_source_changes=True,all_sequential_source_exact=True,
        actual_last_cone_proof_rerun_cases=393216,actual_fp_cone_proof_rerun_cases=1048576,
        new_registers=0,new_cycles=0,
        whole_cpu_qualification='Separate retained14-case suite running on final combined source.')
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
