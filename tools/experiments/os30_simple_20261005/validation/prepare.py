#!/usr/bin/env python3
"""Reuse byte-identical baseline captures; extract exact A GPU parameters."""
from common import *
import re,shutil,subprocess

def main():
 OUT.mkdir(parents=True,exist_ok=True)
 inputs={}
 for label,path in BASELINES.items():
  m=check_capture(path);name='normal' if label.endswith('normal') else 'diagnostic'
  elf=OUT.parent/'baseline'/(name+'.elf');assert sha(elf)==sha(path/'sm64.elf')
  inputs[label]={'capture':str(path.relative_to(ROOT)),'inputs_sha256':sha(path/'inputs.json'),'elf_sha256':sha(elf),'all_retained_result_hashes_verified':True}
 q=OUT/'qsim-observed';
 if not q.exists():shutil.copytree(OLD/'qsim-observed',q)
 qm=read(OLD/'qsim-observed-manifest.json')
 for rel,digest in qm['sources'].items():assert sha(q/rel)==digest,rel
 inputs['qsim']={'exe_sha256':sha(q/'qsim'),'sources':qm['sources'],'retained_manifest_sha256':sha(OLD/'qsim-observed-manifest.json')}
 # Read the exact saved-seed A bitstream's preprocessed macros and frozen top.
 prov=read(ROOT/'docs/measurements/os30-sm64-ab-20261005/provenance/manifest.json')['configurations']['A_single_geo0']
 defs=prov['macros'];assert 'INCLUDE_TEX_QUEUE_MLAB' in defs
 assert not set(defs)&{'INCLUDE_GPU_SM64_GEOMETRY','INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_CLK90','INCLUDE_CLK96','INCLUDE_OS30_SDRAM166'}
 frozen=read(FIT/'frozen-sources.json')
 for rel,digest in frozen.items():assert sha(FIT/'frozen'/rel)==digest,rel
 target=FIT/'frozen/src/fpga/targets/pocket';common=FIT/'frozen/src/fpga/common'
 cmd=['verilator','-E','-P','-I'+str(target),'-I'+str(target/'apf'),*['+define+'+d for d in defs],str(target/'core_top.v')]
 pre=subprocess.run(cmd,capture_output=True,text=True,check=True).stdout;(OUT/'preprocessed-A.v').write_text(pre)
 block=re.search(r'gpu_core\s*#\((.*?)\)\s*gpu\s*\(',pre,re.S)[1];block=re.sub(r'//[^\n]*','',block)
 params={k:int(v) for k,v in re.findall(r'\.(\w+)\(\s*(\d+)\s*\)',block)};assert len(params)==block.count('.')
 core=(common/'gpu_core.v').read_text();head=core[core.index('module gpu_core'):core.index('// AXI4 Read Master')]
 defaults={k:int(v) for k,v in re.findall(r'parameter\s+(\w+)\s*=\s*(\d+)\s*[,\n]',head)};defaults.update(params)
 fixture=(ROOT/'src/fpga/test/tb_gpu.v').read_text();forwarded=re.findall(r'parameter\s+(\w+)\s*=',fixture[:fixture.index('input  wire')])
 old=read(OLD/'replay-selected-dual/manifest.json')['gpu']
 assert defaults==old['parameters'],[(k,defaults.get(k),v) for k,v in old['parameters'].items() if defaults.get(k)!=v]
 # Capability changes between A/B are storage/mapping only. Check actual top
 # byte identity and peripheral identity after removing B's sole KEEP attribute.
 dual=ROOT/'docs/measurements/os30-dual-20261004/fit-rvc-skid-pointer-packer-coord-v1/frozen'
 assert sha(target/'core_top.v')==sha(dual/'src/fpga/targets/pocket/core_top.v')
 ap=(common/'axi_periph_slave.v').read_text();bp=(dual/'src/fpga/common/axi_periph_slave.v').read_text()
 assert bp.replace('(* keep = "true" *) reg [31:0] sysreg_rdata;', 'reg [31:0] sysreg_rdata;')==ap
 assert set(defs)^set(old['variant']['defs'].split())=={'INCLUDE_TEX_QUEUE_MLAB'}
 # Helpers read these production inputs; require them byte-identical to A.
 for rel in ['src/fpga/targets/pocket/io_sdram.v','src/fpga/targets/pocket/core_top.v']:
  assert sha(ROOT/rel)==frozen[rel],rel
 config={'variant':{'variant':'os30','target':'pocket','defs':' '.join(defs)},'parameters':defaults,'flags':['-G'+k+'='+str(defaults[k]) for k in forwarded],'cflags':old['cflags'],'source_fit':str(FIT.relative_to(ROOT)),'frozen_sources_sha256':sha(FIT/'frozen-sources.json')}
 (OUT/'gpu-A-config.json').write_text(json.dumps(config,indent=2)+'\n')
 inputs['A']={'fit':str(FIT.relative_to(ROOT)),'pocket_sha256':prov['pocket_sha256'],'cpu_sha256':frozen['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'],'gpu_sha256':sha(common/'gpu_core.v'),'hw_features':'0x2b136753','geometry':False,'macros':defs,'configuration_sha256':sha(OUT/'gpu-A-config.json')}
 inputs['scope']='Exact app/model reuse plus A ordinary GPU RTL; qsim CPU is an estimated single-issue model, not cycle-accurate CPU RTL or device FPS. Textures use SDRAM, not hardware CRAM1.'
 inputs['passed']=True
 (OUT/'inputs.json').write_text(json.dumps(inputs,indent=2)+'\n')
 print('PASS: both retained baselines reused, exact A GPU configuration extracted')
if __name__=='__main__':main()
