#!/usr/bin/env python3
"""Compose four proved, disjoint, zero-cycle CPU timing factors privately."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess

R = Path(__file__).resolve().parents[3]
P = R/'docs/measurements/os30-fps25-20261005/phase10'
B = P/'cpu-load-forward-v1/VexiiRiscv.v'
OUT = P/'cpu-control-compose-v1'
PARENT = '82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534'
COMPONENTS = [
    ('cpu-btb-balanced-v2','c6b01ce1d6c27f3e052bae59733f69bc67b192cc79f9117445d199b88b27f9c0','hunk.json'),
    ('cpu-load-sign-v1','0ac352f65916e15244afac5c34fdd07a8370f9aa1e5ff10e56fed1cecb153adb','hunk.json'),
    ('cpu-fp-rs3-tag-v1','29e15fb89efbfeb71a570e4d9567988e2db346b36387cce833e8ac173a552c50','hunks.json'),
    ('cpu-decode-ready-v2','0ed91703067b13620e7bdef971839580f7cd5977533b10a68b3e45c568fd2cf9','hunk.json'),
]
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert sha(B) == PARENT
    parent = B.read_text(); candidate = parent; patches=[]; evidence=[B,Path(__file__)]
    for name,digest,hname in COMPONENTS:
        d = P/name; manifest = d/'manifest.json'
        assert sha(manifest) == digest
        m = json.loads(manifest.read_text())
        for n,h in m.get('evidence_sha256',{}).items(): assert sha(R/n)==h,n
        for n,h in m.get('files',{}).items(): assert sha(d/n)==h,n
        hunks = json.loads((d/hname).read_text())
        if 'old' in hunks: hunks = {'only':hunks}
        local = parent
        for label,h in hunks.items():
            assert local.count(h['old']) == candidate.count(h['old']) == 1,(name,label)
            local = local.replace(h['old'],h['new'],1)
            candidate = candidate.replace(h['old'],h['new'],1)
            patches.append(dict(component=name,label=label,**h))
        assert local == (d/'VexiiRiscv.v').read_text(), name
        evidence += [manifest,d/hname,d/'VexiiRiscv.v']
    reverse = candidate
    for h in reversed(patches):
        assert reverse.count(h['new']) == 1
        reverse = reverse.replace(h['new'],h['old'],1)
    assert reverse == parent
    OUT.mkdir(exist_ok=False)
    (OUT/'VexiiRiscv.v').write_text(candidate)
    (OUT/'hunks.json').write_text(json.dumps(patches,indent=2)+'\n')
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(parent.splitlines(True),candidate.splitlines(True),fromfile='82b7/VexiiRiscv.v',tofile='control/VexiiRiscv.v')))
    shutil.copyfile(__file__,OUT/'recipe.py')
    with (OUT/'lint.log').open('x') as f:
        subprocess.run(['verilator','--lint-only','--Wno-fatal','--top-module','VexiiRiscv',str(OUT/'VexiiRiscv.v')],stdout=f,stderr=subprocess.STDOUT,check=True)
    result=dict(passed=True,parent_sha256=PARENT,candidate_sha256=sha(OUT/'VexiiRiscv.v'),
        components=len(COMPONENTS),literal_hunks=len(patches),exact_disjoint_forward_reverse=True,
        new_registers=0,new_cycles=0,native_and_physical_pending=True,production_modified=False)
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    evidence += [p for p in OUT.iterdir() if p.is_file()]
    (OUT/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}),indent=2)+'\n')
    print(json.dumps(result,indent=2)); print('MANIFEST',sha(OUT/'manifest.json'))


if __name__ == '__main__': main()
