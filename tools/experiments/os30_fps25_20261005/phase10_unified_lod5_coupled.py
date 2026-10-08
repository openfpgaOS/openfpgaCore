#!/usr/bin/env python3
"""Unified transform+lighting control, unchanged real LOD5 app and memories."""
import argparse,json,shutil,subprocess,sys,os
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'lod5-real-coupled-v2';OUT=P/'unified-lod5-coupled-v1';RTL=P/'unified-load-components-v1/frozen/geometry_transform_light.v'
def prepare(label):
 d=OUT/label;d.mkdir(parents=True,exist_ok=False)
 shutil.copytree(OLD/'model/frozen',d/'model/frozen');shutil.copytree(OLD/'qsim',d/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 if label=='unified':shutil.copy2(RTL,d/'model/frozen/common/geometry_transform_light.v')
 p=d/'model/frozen/coupled.cpp';t=p.read_text()
 def rep(a,b):
  nonlocal t
  assert t.count(a)==1,(a,t.count(a));t=t.replace(a,b)
 rep('static uint64_t gd_start_cycle;','static uint64_t gd_start_cycle,gd_actual_finish,gd_first_poll;\nstatic bool gd_seen_active;')
 rep('static void gd_begin_sample() {','static void gd_begin_sample() {\n    if(gd_operation) {\n        if(tb->rootp->tb_gpu__DOT__load_client__DOT__active)gd_seen_active=true;\n        else if(gd_seen_active&&!gd_actual_finish)gd_actual_finish=cycles;\n    }')
 rep('reason,words,count,lit\\n','reason,words,count,lit,hardware_finish,first_poll\\n')
 rep('    tb->gd_reg_addr=off/4;tb->eval();','    if(off==4&&gd_operation&&!gd_first_poll)gd_first_poll=cycles;\n    tb->gd_reg_addr=off/4;tb->eval();')
 rep('    if(off==4 && !(value&1) && gd_operation) {','    if(off==4 && !(value&1) && gd_operation) {\n        if(!gd_actual_finish){assert(gd_seen_active);gd_actual_finish=cycles;}')
 rep('%u,0,0,0,0,%u,%u\\n','%u,0,0,0,0,%u,%u,%llu,%llu\\n')
 rep('value,source_count,source_light);','value,source_count,source_light,(unsigned long long)gd_actual_finish,(unsigned long long)gd_first_poll);')
 rep('        ++gd_loads;','        gd_actual_finish=gd_first_poll=0;gd_seen_active=false;\n        ++gd_loads;');p.write_text(t)
 cmd=[x.replace(str(OLD),str(d))for x in json.loads((OLD/'build-command.json').read_text())];write(d/'build-command.json',cmd)
 write(d/'prepared.json',dict(parent_manifest_sha256=sha(OLD/'manifest.json'),rtl_sha256=sha(d/'model/frozen/common/geometry_transform_light.v'),elf_sha256=sha(P/'load-cram-driver-v1/app.elf'),source_sha256={str(p.relative_to(d)):sha(p)for p in d.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__)),scope=__doc__))
def build(label):
 d=OUT/label
 with(d/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=d/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((d/'build-command.json').read_text())
 with(d/'model/build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(d/'model/build.json',dict(command=cmd,simulator_sha256=sha(d/'model/obj/Vtb_gpu')))
def run(label,window):
 m=module('unified_lod5_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT/label
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),window,'--elf',str(P/'load-cram-driver-v1/app.elf'),'--start','360'if window=='warm'else'234','--frames','8'];m.main()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('label',choices=['baseline','unified']);p.add_argument('--window',choices=['warm','cold'],default='warm');a=p.parse_args();run(a.label,a.window)if a.action=='run'else globals()[a.action](a.label)
