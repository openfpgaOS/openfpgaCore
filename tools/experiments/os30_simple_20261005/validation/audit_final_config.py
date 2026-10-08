#!/usr/bin/env python3
"""Recheck retained A hardware/model identities and final capture capability gates."""
from common import *
import argparse
p=argparse.ArgumentParser(description=__doc__);p.add_argument('labels',nargs='*');a=p.parse_args()
i=read(OUT/'inputs.json');m=read(OUT/'replay-single-A/manifest.json');config=read(OUT/'gpu-A-config.json')
assert i['A']['geometry'] is False and i['A']['hw_features']=='0x2b136753'
assert i['A']['pocket_sha256']=='d3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f'
assert i['A']['cpu_sha256']=='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119'
assert sha(OUT/'qsim-observed/qsim')==i['qsim']['exe_sha256']
assert sha(OUT/'replay-single-A/obj/Vtb_gpu')==m['simulator_sha256']
for rel,h in m['sources'].items():assert sha(OUT/'replay-single-A'/rel)==h,rel
assert not {'INCLUDE_GPU_SM64_GEOMETRY','INCLUDE_GPU_GEOMETRY_WORKER'}&set(config['variant']['defs'].split())
for key in ['INCLUDE_COMMAND_DMA','INCLUDE_GPU_XFORM_MAC','INCLUDE_GPU_LIGHT']:assert config['parameters'][key]==0,key
captures={}
for label in ['baseline-normal','baseline-diagnostic',*a.labels]:
 path=capture_path(label);check_capture(path);console=(path/'console.log').read_text()
 assert 'gpu caps=2b136753 cpu=100000000Hz' in console
 assert 'geometry accelerator=1' not in console
 captures[label]={'inputs_sha256':sha(path/'inputs.json'),'elf_sha256':sha(path/'sm64.elf'),'geometry_disabled_in_console':'geometry accelerator=0' in console,'gpu_caps_clock_verified':True}
report={'passed':True,'A_core_sha256':i['A']['pocket_sha256'],'A_CPU_sha256':i['A']['cpu_sha256'],'gpu_replay_sha256':m['simulator_sha256'],'qsim_sha256':i['qsim']['exe_sha256'],'hardware_feature_word':'0x2b136753','configured_clock_hz':100000000,'geometry_status_bit':10,'geometry_capability_enabled':False,'command_DMA_enabled':False,'worker_defines_present':False,'model_configuration':{'issue':'single','Icache_bytes':32768,'Dcache_bytes':131072,'BTB_sets':1024,'gshare_2bit_counters':16384,'return_stack_depth':8},'scope':'Ordinary A clip-load/vertex-cache GPU remains enabled; this is distinct from optional raw-coordinate geometry accelerator and worker. Model estimates use os30 cache/predictor sizing with os25-derived execution latencies and guessed memory parameters, not exact CPU RTL/frame timing. A GPU replay validates component output independently; no CPU/GPU overlap or hardware FPS claim.','captures_checked':captures,'source_hashes':{str(path.relative_to(ROOT)):sha(path) for path in [OUT/'inputs.json',OUT/'gpu-A-config.json',OUT/'replay-single-A/manifest.json',OUT/'qsim-observed/cache.c',OUT/'qsim-observed/gpu.c',OUT/'qsim-observed/env.c',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py',Path(__file__).resolve()]}}
(OUT/'final-config-audit.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'passed':True,'captures_checked':list(captures),'model_configuration':report['model_configuration']},indent=2))
