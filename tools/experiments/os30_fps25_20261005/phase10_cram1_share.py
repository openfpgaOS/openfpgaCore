#!/usr/bin/env python3
"""Private two-reader CRAM1 protocol proof, with actual adapter/controller/PHY."""
import argparse,hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/cram1-share-v1'
SRC=ROOT/'tools/experiments/os30_fps25_20261005'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def prepare():
    f=OUT/'frozen';f.mkdir(parents=True,exist_ok=False)
    paths=[SRC/'phase10_cram1_read_arbiter.v',SRC/'phase10_cram1_share_tb.v',
      ROOT/'src/fpga/targets/pocket/gpu_cram1_tex_adapter.v',ROOT/'src/fpga/targets/pocket/cram1_controller.v',ROOT/'src/fpga/targets/pocket/cram1_phy.sv',
      ROOT/'src/fpga/test/cram_chip_model.v',ROOT/'src/fpga/test/altddio_out_stub.v']
    for p in paths:shutil.copy2(p,f/p.name)
    shutil.copy2(__file__,OUT/'recipe.py')
    save(OUT/'inputs.json',{str(p):sha(p) for p in paths})
def run(refresh):
    f=OUT/'frozen';d=OUT/f'refresh{refresh}';d.mkdir(exist_ok=False)
    cmd=['verilator','--binary','--timing','-j','4','-Wno-fatal','-Wno-WIDTH','-Wno-UNOPTFLAT','-Wno-CASEINCOMPLETE','--top-module','tb_geometry_cram1_share',
      '--Mdir',str(d/'obj'),f'-GEXTRA_REFRESH_CYCLES={refresh}']+[str(p) for p in f.iterdir() if p.suffix in ('.v','.sv')]
    save(d/'build-command.json',cmd)
    with (d/'build.log').open('w') as log:subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (d/'run.log').open('w') as log:subprocess.run([str(d/'obj/Vtb_geometry_cram1_share')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    print((d/'run.log').read_text())
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run']);p.add_argument('--refresh',type=int,default=0);a=p.parse_args()
    prepare() if a.action=='prepare' else run(a.refresh)
