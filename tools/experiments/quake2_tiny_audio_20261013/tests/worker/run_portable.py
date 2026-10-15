#!/usr/bin/env python3
"""Run final worker lockstep/width proofs against a staged core; no Quartus/device I/O."""
from pathlib import Path
import argparse,hashlib,json,re,subprocess
D=Path(__file__).resolve().parent
EXPECTED='e0f4ffbd20f2a20ab416543a0e9d830d7ec6b4d8d55df094479428b67f933fbb'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def invoke(cmd,log):
    with log.open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
def main():
    p=argparse.ArgumentParser();p.add_argument('--core',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    core=a.core.resolve();out=a.output.resolve();worker=core/'src/fpga/common/gpu_geometry_worker.v'
    if sha(worker)!=EXPECTED:raise SystemExit('Staged worker does not match the final qualified source pin')
    out.mkdir(parents=True,exist_ok=False);f=D/'lockstep-fixtures-v2';w=D/'width-proof'
    rel=['lockstep-fixtures-v2/geometry_worker_tb.v','lockstep-fixtures-v2/geometry_worker.cpp','lockstep-fixtures-v2/gpu_geometry_worker_reference.v','lockstep-fixtures-v2/source-pins.json','width-proof/width_proof.v','width-proof/main.cpp','width-proof/configurations.json']
    receipt={'passed':False,'worker_sha256':sha(worker),'recipe_sha256':sha(Path(__file__)),'fixture_sha256':{r:sha(D/r) for r in rel},'verilator':subprocess.check_output(['verilator','--version'],text=True).strip(),'configurations':{},'scope':'Native6 paired-worker cycle/output equivalence for all four native/particle combinations plus signed near-plane, malformed, reset, pause and typed-width gates. Physical timing, audio scheduling and full GPU captures have separate receipts.'}
    total=0
    for n,p in [(1,1),(1,0),(0,1),(0,0)]:
        name=f'native{n}-particles{p}';d=out/name;d.mkdir()
        cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','geometry_worker_tb','--Mdir',str(d/'obj'),f'+define+TEST_NATIVE={n}',f'+define+TEST_PARTICLES={p}','-CFLAGS',f'-DTEST_NATIVE={n} -DTEST_PARTICLES={p}',str(f/'geometry_worker_tb.v'),str(worker),str(f/'gpu_geometry_worker_reference.v'),str(f/'geometry_worker.cpp')]
        (d/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n');invoke(cmd,d/'build.log');invoke([str(d/'obj/Vgeometry_worker_tb')],d/'result.txt')
        text=(d/'result.txt').read_text();m=re.search(r'PASS cycle lockstep (\d+) cycles,',text);assert m
        total+=int(m.group(1));receipt['configurations'][name]={'passed':True,'cycles':int(m.group(1)),'result':text,'result_sha256':sha(d/'result.txt'),'executable_sha256':sha(d/'obj/Vgeometry_worker_tb')}
    d=out/'width-proof';d.mkdir()
    cmd=['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','width_proof','--Mdir',str(d/'obj'),str(w/'width_proof.v'),str(w/'main.cpp')]
    (d/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n');invoke(cmd,d/'build.log');invoke([str(d/'obj/Vwidth_proof')],d/'result.txt')
    text=(d/'result.txt').read_text();assert text.startswith('PASS 1851968 pointer/consume vectors across 35 ')
    receipt['width_proof']={'passed':True,'configurations':json.loads((w/'configurations.json').read_text()),'result':text,'result_sha256':sha(d/'result.txt'),'executable_sha256':sha(d/'obj/Vwidth_proof')}
    assert total==27163742;receipt['total_lockstep_cycles']=total;receipt['passed']=True
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(f'PASS worker: {total} lockstep cycles, 1851968 width vectors; {out}/receipt.json')
if __name__=='__main__':main()
