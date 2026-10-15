#!/usr/bin/env python3
"""Freeze and test one mixer; no source mutation, fitter or board access."""
from pathlib import Path
import argparse, hashlib, json, os, re, shutil, subprocess

D=Path(__file__).resolve().parent
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def invoke(cmd,log,env=None):
    with log.open('w') as f: subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True,env=env)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('mixer',type=Path);ap.add_argument('out',type=Path)
    ap.add_argument('--skip-capacity',action='store_true');args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);s=out/'sources';s.mkdir()
    paths={name:D/name for name in ['tb_audio_mixer.v','tb_audio_mixer_main.cpp','independent_reference.hpp']}
    paths.update({n:D/'sources'/n for n in ['sync_fifo.v','mister_audio_output.v']})
    paths['audio_mixer.v']=args.mixer.resolve()
    pins={}
    for name,p in paths.items():
        shutil.copy2(p,s/name);pins[name]={'original':str(p),'sha256':sha(p)}
    (out/'source-pins.json').write_text(json.dumps(pins,indent=2)+'\n')
    cmd=['verilator','--cc','--exe','--build','--assert','-j','3','-Wno-fatal',
        '-Wno-WIDTHEXPAND','-Wno-WIDTHTRUNC','-Wno-CASEINCOMPLETE','-Wno-SYNCASYNCNET','-Wno-UNOPTFLAT',
        '--top-module','tb_audio_mixer','--Mdir',str(out/'obj'),'-I'+str(s),
        *[str(s/n) for n in ['tb_audio_mixer.v','audio_mixer.v','sync_fifo.v','mister_audio_output.v','tb_audio_mixer_main.cpp']]]
    (out/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n');invoke(cmd,out/'build.log')
    exe=str(out/'obj/Vtb_audio_mixer');env=dict(os.environ,AUDIO_PERF_REPORT='1')
    invoke([exe],out/'inherited.log',env);invoke([exe,'--reference'],out/'reference.log')
    if not args.skip_capacity:invoke([exe,'--capacity'],out/'capacity.csv')
    text=(out/'reference.log').read_text();m=re.search(r'REFERENCE samples=(\d+) scenarios=(\d+) errors=(\d+) saturation_samples=(\d+) stream_holds=(\d+) hash=([0-9a-f]+) processing_cycles=(\d+)',text)
    assert m and m[3]=='0',text
    receipt={'passed':True,'mixer_sha256':pins['audio_mixer.v']['sha256'],'reference':dict(zip(['samples','scenarios','errors','saturation_samples','stream_holds','hash','processing_cycles'],m.groups())),
        'inherited_results':re.search(r'=== Results: .* ===',(out/'inherited.log').read_text())[0],
        'limits':['Dynamic component tests, not a formal proof.','SDRAM response latency/AR/R stalls are synthetic, not the shared physical arbiter.','FIFO sink uses existing synchronous48 kHz model; Pocket CDC and physical audio pins require separate checks.','No physical fitter, timing result or game FPS claim.']}
    (out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt,indent=2))
if __name__=='__main__':main()
