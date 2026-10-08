#!/usr/bin/env python3
"""Real LOD5/unified-v1 client with registered render-cache lookup and fill-hazard stages."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'cache-stage-lod5-coupled-v1';OUT=P/'cache-stage-lod5-coupled-v2';COMP=P/'cache-read-stage-v2';RTL=COMP/'gpu_color_depth_cache.sv'
def prepare():
 OUT.mkdir(exist_ok=False)
 assert sha(RTL)=='348549653e7a82f88a55fba3a30872ddbe84f96026b4f723a78c38a16cf121c5'
 shutil.copytree(OLD/'model/frozen',OUT/'model/frozen');shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 prep=json.loads((COMP/'prepared.json').read_text());assert sha(OLD/'model/frozen/common/gpu_color_depth_cache.sv')==prep['parent_source_sha256']
 shutil.copy2(RTL,OUT/'model/frozen/common/gpu_color_depth_cache.sv')
 p=OUT/'model/frozen/tb_gpu.v';s=p.read_text();old='.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4), .REGISTER_READ_LOOKUP(1)';assert s.count(old)==1;s=s.replace(old,old+', .REGISTER_FILL_HAZARD(1)');p.write_text(s)
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())];write(OUT/'build-command.json',cmd)
 write(OUT/'prepared.json',dict(scope=__doc__,parent_manifest_sha256=sha(OLD/'manifest.json'),component_prepared_sha256=sha(COMP/'prepared.json'),cache_source_sha256=sha(RTL),parameter=dict(REGISTER_READ_LOOKUP=1,REGISTER_FILL_HAZARD=1),elf_sha256=sha(P/'load-cram-driver-v1/app.elf'),source_sha256={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def build():
 with(OUT/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((OUT/'build-command.json').read_text())
 with(OUT/'model/build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run(window):
 m=module('cache_stage_lod5_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),window,'--elf',str(P/'load-cram-driver-v1/app.elf'),'--start','360'if window=='warm'else'234','--frames','8'];m.main()
 d=OUT/f'run-{window}';assert 'FATAL'not in(d/'run.log').read_text()
 f=d/'inputs.json';v=json.loads(f.read_text());v['environment'].update(IDEAL_GPU_MEMORY='0',LOD5_MODEL_MODE='normal');f.write_text(json.dumps(v,indent=2)+'\n')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('--window',choices=['warm','cold'],default='warm');a=p.parse_args();run(a.window)if a.action=='run'else globals()[a.action]()
