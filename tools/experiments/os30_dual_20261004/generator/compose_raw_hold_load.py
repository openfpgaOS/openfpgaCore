#!/usr/bin/env python3
"""Compose frozen disjoint inflight-hold and load-byte patches in both orders."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'raw-normalize-v1/immediate/VexiiRiscv.v'
BASE_SHA='06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048'
PARTS=[('hold',ART/'inflight-hold-v1/immediate','inflight-hold.patch','5e9affaba19a5ef2555ec3f900724e37d63f232287b729c8a853f851604e163c'),
       ('load',ART/'load-byte-factor-v1/immediate','byte-before-way.patch','92b6cbb95623589ba8dff7c128e9b0fb32bf55d7ce445fa820c3747ae6465ea1')]

def sha(data):return hashlib.sha256(data).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ART/'raw-hold-load-v1/immediate')
    args=parser.parse_args()
    source=BASE.read_bytes();assert sha(source)==BASE_SHA
    parts={}
    for name,path,patch,expected in PARTS:
        manifest=json.loads((path/'manifest.json').read_text())
        assert manifest['reference_sha256']==BASE_SHA
        assert manifest['candidate_sha256']==expected==sha((path/'VexiiRiscv.v').read_bytes())
        parts[name]=dict(directory=path,patch=path/patch,sha256=expected)
    outputs=[];runs=[]
    for order in [('hold','load'),('load','hold')]:
        with tempfile.TemporaryDirectory(prefix='os30-hold-load-compose-') as tmp:
            directory=Path(tmp);target=directory/'VexiiRiscv.v';target.write_bytes(source)
            log=[]
            for index,name in enumerate(order):
                result=subprocess.run(['patch','--batch','--fuzz=0','-p1','-i',str(parts[name]['patch'])],
                                      cwd=directory,check=True,capture_output=True,text=True)
                log.append(result.stdout)
                if index==0:assert sha(target.read_bytes())==parts[name]['sha256']
            combined=target.read_bytes();outputs.append(combined)
            for index,name in enumerate(reversed(order)):
                result=subprocess.run(['patch','--batch','--fuzz=0','-R','-p1','-i',str(parts[name]['patch'])],
                                      cwd=directory,check=True,capture_output=True,text=True)
                log.append(result.stdout)
                if index==0:assert sha(target.read_bytes())==parts[order[0]]['sha256']
            assert target.read_bytes()==source
            runs.append(dict(order=list(order),candidate_sha256=sha(combined),reverse_restores_base=True,patch_output=log))
    assert outputs[0]==outputs[1]
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    (out/'VexiiRiscv.v').write_bytes(outputs[0]);shutil.copy2(__file__,out/'compose_raw_hold_load.py')
    for name,part in parts.items():
        shutil.copy2(part['patch'],out/part['patch'].name)
        shutil.copy2(part['directory']/'manifest.json',out/(name+'-manifest.json'))
    manifest=dict(reference=str(BASE.relative_to(ROOT)),reference_sha256=BASE_SHA,
                  candidate_sha256=sha(outputs[0]),script_sha256=sha(Path(__file__).read_bytes()),
                  parts={name:dict(candidate_sha256=part['sha256'],patch_sha256=sha(part['patch'].read_bytes()),
                                   path=str(part['directory'].relative_to(ROOT))) for name,part in parts.items()},
                  order_proofs=runs,both_orders_byte_identical=True,all_unrelated_source_unchanged=True,
                  added_register_bits=0,added_pipeline_cycles=0,whole_cpu_validation='pending')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
