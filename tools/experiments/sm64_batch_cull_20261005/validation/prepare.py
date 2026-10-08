#!/usr/bin/env python3
"""Freeze the paired CPU capture model and selected-dual GPU replay inputs."""
import hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
BASE=ROOT/'docs/measurements/sm64-vertex-perf-20261004/qsim'
FIT=ROOT/'docs/measurements/os30-dual-20261004/fit-rvc-skid-pointer-packer-coord-v1'
CONFIG=ROOT/'docs/measurements/os30-dual-20261004/validation/gpu-coordinate-stage-v1/rendering/manifest.json'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b,1)
def run(cmd,log,cwd=None):
 with log.open('w') as f:subprocess.run(list(map(str,cmd)),cwd=cwd,stdout=f,stderr=subprocess.STDOUT,check=True)
def main():
 OUT.mkdir(parents=True,exist_ok=True)
 cfg=json.loads(CONFIG.read_text()); defs=cfg['variant']['defs'].split()
 assert 'EXCLUDE_GPU_XFORM_MAC' in defs and not any(x in defs for x in ['INCLUDE_GPU_SM64_GEOMETRY','INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_TEX_QUEUE_MLAB'])
 # Resolve actual core_top feature overrides and the actual peripheral constant.
 capdir=OUT/'capabilities';capdir.mkdir(exist_ok=False)
 top=(FIT/'frozen/src/fpga/targets/pocket/core_top.v').read_text()
 per=(FIT/'frozen/src/fpga/common/axi_periph_slave.v').read_text()
 instance=top.split('    axi_periph_slave #(\n',1)[1].split('    ) periph (',1)[0]
 params=re.findall(r'\.(\w+)\(([^\n]+)\)',instance)
 assert len(params)==19,len(params)
 local='\n'.join('localparam '+k+' = '+v+';' for k,v in params)
 begin=per.rindex('`ifdef INCLUDE_LINK',0,per.index('localparam [31:0] FEAT_LINK'))
 feat=per[begin:per.index('`endif',begin)+len('`endif')]
 hw=per[per.index('localparam [31:0] HW_FEATURES_RESOLVED ='):per.index("    | 32'h0100_0000;")+len("    | 32'h0100_0000;")]
 code='module caps;\n'+local+'\n'+feat+'\n'+hw+'\ninitial begin $display("CAPS=%08x", HW_FEATURES_RESOLVED); $finish; end\nendmodule\n'
 (capdir/'actual.sv').write_text(code)
 run(['verilator','--binary','-j','2','-Wno-fatal','--top-module','caps','--Mdir',capdir/'obj',*['+define+'+d for d in defs],capdir/'actual.sv'],capdir/'build.log')
 run([capdir/'obj/Vcaps'],capdir/'result.log')
 caps=int(re.search(r'CAPS=([0-9a-f]+)',(capdir/'result.log').read_text())[1],16)
 assert caps==0x2b136753,hex(caps)
 (capdir/'manifest.json').write_text(json.dumps(dict(actual_feature_word=hex(caps),defs=defs,parameters=params,
  fit=str(FIT),sources={str(p):sha(p) for p in [FIT/'frozen/src/fpga/targets/pocket/core_top.v',FIT/'frozen/src/fpga/common/axi_periph_slave.v',CONFIG,capdir/'actual.sv']},
  notes=['The test texture arena remains the retained 512KiB SDRAM-backed allocation, rather than a CRAM1 performance model.','GEO1 is a separate compatibility control, not the selected dual hardware configuration.']),indent=2)+'\n')
 q=OUT/'qsim';q.mkdir(exist_ok=False)
 old={}
 for p in BASE.iterdir():
  if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name);old[p.name]=sha(p)
 p=q/'env.c';s=p.read_text();s=rep(s,'0x2913775bu',f'0x{caps:08x}u');p.write_text(s)
 p=q/'gpu.c';s=p.read_text();s=rep(s,'(getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)','(getenv("SM64_GEOMETRY_CAP") && !strcmp(getenv("SM64_GEOMETRY_CAP"), "1") ? 0x400u : 0)');p.write_text(s)
 run(['make','-j3'],OUT/'qsim-build.log',q)
 (OUT/'qsim-manifest.json').write_text(json.dumps(dict(exe=str(q/'qsim'),exe_sha256=sha(q/'qsim'),original_sources=old,
  sources={p.name:sha(p) for p in q.iterdir() if p.is_file()},hw_features=hex(caps),geometry_capability_control='Explicit string 0 or 1; same executable for both apps and both modes.',
  model='Retained functional GPU + estimated single-issue os25 CPU cycles; no dual-issue or device FPS prediction.',
  patch_scope='Only hardware feature constant and explicit geometry-capability switch; all input/clock/audio/diagnostic code retained.'),indent=2)+'\n')
 print('QSIM_READY',q/'qsim',hex(caps),flush=True)
if __name__=='__main__':main()
