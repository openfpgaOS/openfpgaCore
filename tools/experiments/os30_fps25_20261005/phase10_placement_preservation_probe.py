#!/usr/bin/env python3
"""Private-copy v7 placement annotation capability only; never invokes a fitter."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v7/fit-seed2/jobs/pocket-os30-s2'
OUT=P/'placement-preservation-readonly-v1'
CDB='/home/alberto/altera_lite/25.1std/quartus/bin/quartus_cdb'
TARGET=Path('src/fpga/targets/pocket')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory(p):return {str(x.relative_to(p)):sha(x) for x in sorted(p.rglob('*')) if x.is_file()}
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')

def prepare():
    assert OUT.is_dir() and not (OUT/'copied-db.json').exists()
    hashes=inventory(BASE)
    for mode in ['lc','lab']:
        shutil.copytree(BASE,OUT/mode)
        assert inventory(OUT/mode)==hashes
    save(OUT/'copied-db.json',{'original_job':str(BASE.relative_to(ROOT)),
        'original_sha256':hashes,'copied_modes':['lc','lab'],
        'helper_sha256':sha(Path(__file__)),'scope':__doc__})
    print('Copied and verified',len(hashes),'files per private job')

def annotate(mode):
    assert mode in ['lc','lab']
    prep=json.loads((OUT/'copied-db.json').read_text())
    assert inventory(BASE)==prep['original_sha256']
    assert inventory(OUT/mode)==prep['original_sha256']
    cwd=OUT/mode/TARGET
    cmd=[CDB,'ap_core','--back_annotate='+mode]
    save(OUT/(mode+'-invocation.json'),{'command':cmd,'cwd':str(cwd),
        'fitter_invoked':False,'only_private_copy_writable':True})
    start=time.monotonic()
    with (OUT/(mode+'.log')).open('x') as log:
        run=subprocess.run(cmd,cwd=cwd,stdout=log,stderr=subprocess.STDOUT)
    after=inventory(OUT/mode)
    assert inventory(BASE)==prep['original_sha256'],'original job changed'
    changed={n:{'before':prep['original_sha256'].get(n),'after':h}
             for n,h in after.items() if prep['original_sha256'].get(n)!=h}
    save(OUT/(mode+'-result.json'),{'returncode':run.returncode,
        'elapsed_seconds':time.monotonic()-start,'changed_private_files':changed,
        'original_all_files_unchanged':True,'fitter_invoked':False})
    print(mode,'returncode',run.returncode,'changed files',len(changed))

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:annotate(sys.argv[1])
