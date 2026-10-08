#!/usr/bin/env python3
"""Build/run one private native renderer model with immutable captured inputs."""
import argparse,hashlib,json,shutil,subprocess,time
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
W=P/'native-renderer-window-v1';G=W/'guest-v2';S=P/'native-renderer-capture-v1/run-reference/snapshot'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def main():
 a=argparse.ArgumentParser();a.add_argument('model');a.add_argument('label');a.add_argument('--max-cycles',type=int,default=15000000);args=a.parse_args()
 assert '/' not in args.model and '/' not in args.label
 model=W/args.model;o=W/('runner-'+args.label);run=W/('run-'+args.label)
 d=json.loads((model/'prepared.json').read_text())
 for n,h in d['source_sha256'].items():assert sha(model/n)==h,n
 assert json.loads((model/'build.json').read_text())['returncode']==0
 o.mkdir(exist_ok=False);run.mkdir(exist_ok=False)
 source=R/'tools/experiments/os30_fps25_20261005/native_renderer_main.cpp';shutil.copyfile(source,o/'main.cpp');shutil.copyfile(__file__,o/'recipe.py')
 overlay=json.loads((G/'overlay.json').read_text());config=(G/'run-config.txt').read_text()
 config+='finished_pc='+str(overlay['symbols']['native_finished'])+'\n'+'snapshot='+str(S)+'\n'+'segments='+str(len(overlay['segments']))+'\n'
 inputs=[G/'overlay.json',G/'run-config.txt',G/'prepared.json',model/'prepared.json',model/'build.json',source,Path(__file__)]
 inputs+=list(p for p in S.iterdir() if p.is_file())
 for i,s in enumerate(overlay['segments']):
  f=G/s['path'];assert sha(f)==s['sha256'];inputs.append(f);config+=f'segment{i}_address={s["address"]}\nsegment{i}_path={f}\n'
 config+='scan_period=6361\nscan_words=160\nscan_base_halfword=16777216\n';(o/'input.cfg').write_text(config)
 obj=model/'obj';cmd=['g++','-std=c++17','-O2','-I'+str(obj),'-I/usr/share/verilator/include','-I/usr/share/verilator/include/vltstd',str(o/'main.cpp'),str(obj/'Vtb_system__ALL.a'),str(obj/'libverilated.a'),'-pthread','-latomic','-o',str(o/'native_renderer')]
 inputs.extend([obj/'Vtb_system__ALL.a',obj/'libverilated.a']);inventory={str(p.relative_to(R)):sha(p) for p in inputs}
 save(o/'prepared.json',dict(inputs_sha256=inventory,build_command=cmd,scope='One cold original renderer/end-frame call. Synthetic scanout/audio memory pressure is aligned to first renderer entry, excluding differing cache-init delays from the traffic phase. No full-game/FPS claim.'))
 with (o/'build.log').open('x') as f:ret=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
 save(o/'build.json',dict(returncode=ret.returncode));assert ret.returncode==0,(o/'build.log')
 cmd=[str(o/'native_renderer'),str(o/'input.cfg'),str(run),str(args.max_cycles)];save(run/'command.json',cmd);start=time.monotonic()
 with (run/'run.log').open('x') as f:ret=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,cwd=model/'frozen/src/fpga/test')
 save(run/'process.json',dict(returncode=ret.returncode,seconds=time.monotonic()-start));print('Native process',ret.returncode,flush=True)
 for n,h in inventory.items():assert sha(R/n)==h,n
 assert ret.returncode==0,(run/'run.log')
 refs={'commands.bin':S/'commands.bin','frame.bin':S.parent/'frame_00360.bin'}
 exact={n:(run/n).read_bytes()==p.read_bytes() for n,p in refs.items()};exact['entry-regs.bin']=(run/'entry-regs.bin').read_bytes()==(S/'cpu-state.bin').read_bytes()[:66*4]
 result=json.loads((run/'result.json').read_text());assert result['status']==0 and all(exact.values()),exact
 save(run/'verification.json',dict(exact=exact,renderer_cycles=result['finished_cycle']-result['entry_cycle'],gpu_drain_cycles=result['complete_cycle']-result['finished_cycle'],configured_clock_mhz=100,full_frame_FPS_claimed=False))
 files=[p for folder in (o,run) for p in folder.iterdir() if p.is_file()]
 save(run/'manifest.json',dict(model_prepared_sha256=sha(model/'prepared.json'),inputs_sha256=inventory,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
 print((run/'verification.json').read_text());print('MANIFEST',sha(run/'manifest.json'))
if __name__=='__main__':main()
