#!/usr/bin/env python3
"""Retain the existing GEO1 bounds/cache/moving-frustum regression on both sources."""
from pathlib import Path
import importlib.util,subprocess,hashlib,json,os
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
spec=importlib.util.spec_from_file_location('old',ROOT/'tools/experiments/sm64_triangle_cache_20261004/check_visibility.py');old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 dest=OUT/'geo1-checks';dest.mkdir(exist_ok=True);records=[]
 for label,gfx in [('baseline',OUT/'source-baseline'),('candidate',OUT/'SM64/src/sm64/sm64/src/pc/gfx')]:
  pre=old.PRE.replace('#define OF_PC 1','').replace('#define SM64_PERF_SCOPE(x) ((void)0)','#define SM64_PERF_SCOPE(x) ((void)0)\n#define SM64_PERF_COUNT(x,n) ((void)0)')
  pre+='\nstatic uintptr_t test_ro_begin,test_ro_end;\nstatic void gfx_materialize_vertex_attributes(unsigned slot) {(void)slot;}\n'
  body=''.join((gfx/n).read_text() for n in ['gfx_vertex_cull.h','gfx_geometry.h']).replace('(uintptr_t)&_start','test_ro_begin').replace('(uintptr_t)__EH_FRAME_BEGIN__','test_ro_end')
  c=dest/(label+'.c');c.write_text(pre+body+old.TEST);exe=dest/label
  cmd=['cc','-std=gnu11','-O3','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-fno-strict-aliasing','-fwrapv',str(c),'-lm','-o',str(exe)]
  subprocess.run(cmd,check=True);r=subprocess.run([str(exe)],capture_output=True,text=True)
  log=dest/(label+'.log');log.write_text(r.stdout+r.stderr);print(label,r.stdout,r.stderr);r.check_returncode()
  records.append({'label':label,'sources':{n:sha(gfx/n) for n in ['gfx_vertex_cull.h','gfx_geometry.h']},'command':cmd,'harness_sha256':sha(c),'binary_sha256':sha(exe),'log_sha256':sha(log),'output':r.stdout})
 assert records[0]['output']==records[1]['output']
 (dest/'manifest.json').write_text(json.dumps({'passed':True,'baseline_candidate_output_identical':True,'script_sha256':sha(Path(__file__)),'records':records},indent=2)+'\n')
if __name__=='__main__':main()
