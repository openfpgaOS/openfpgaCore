#!/usr/bin/env python3
"""Compose exact FP, symmetric-ready and GShare control factors privately."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil
import subprocess

R = Path(__file__).resolve().parents[3]
P = R/'docs/measurements/os30-fps25-20261005/phase10'
B = P/'cpu-control-compose-v1/VexiiRiscv.v'
OUT = P/'cpu-parallel-control-v1'
PARENT = '878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e'
COMPONENTS = [
    ('cpu-fp-forward-uniform-v1','5d743205163933b8c326c4a5e4a42b3cc72a5179300bc8554bb262cf60d697ff','hunks.json'),
    ('cpu-decode-ready-all-v1','c7eb69e1dcf106c2d4124cac8805ce4c77e095a0e3d56bd48aeddf5f1b380471','hunk.json'),
    ('cpu-gshare-control-v1','de122183ffc0eac72ca7671182f1ceb2714bacf01dd23653de7043178de7fef2','hunks.json'),
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
        if isinstance(hunks,list): hunks = {str(i):h for i,h in enumerate(hunks)}
        elif 'old' in hunks: hunks = {'only':hunks}
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
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(parent.splitlines(True),candidate.splitlines(True),fromfile='878e/VexiiRiscv.v',tofile='parallel-control/VexiiRiscv.v')))
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
