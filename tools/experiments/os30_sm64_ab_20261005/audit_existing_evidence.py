#!/usr/bin/env python3
"""Bind existing P3 app captures and GPU replays; no new execution or fit."""
import hashlib,json,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-sm64-ab-20261005/validation'
CAP=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
DUAL=ROOT/'docs/measurements/os30-dual-20261004'
WORKER=ROOT/'docs/measurements/os30-worker-20261003'
P3=ROOT/'docs/measurements/sm64-vertex-perf-20261004'
APP='990ba23281d6793a479df4c9094cb0cd600894ca3ab5548df360a62c016f2984'
OS='054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
def main():
 evidence=[];report={'app_sha256':APP,'os_sha256':OS,'configurations':{}}
 specs=[
  ('A',DUAL/'fit-single128-v1',DUAL/'copy-to-games-20261005T125923Z/backups/SM64/runtime/pocket/os30.rbf_r','d3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f',False),
  ('B',DUAL/'fit-rvc-skid-pointer-packer-coord-v1',DUAL/'copy-to-games-20261005T125923Z/os30.rbf_r','b4d55466283320c798047effa1dd9895d990fd6f297857e52ad99ec9bbf3778d',False),
  ('C',WORKER/'fit-worker-v6',WORKER/'deploy-worker-test/Cores/ThinkElastic.SM64/os30.rbf_r','a950b6d7a8343d610cd46adb1cd85676ad3f83c6e80985522ba6c29c682295a0',True)]
 sources={}
 reverse=bytes(int(f'{i:08b}'[::-1],2) for i in range(256))
 for label,fit,rbfr,digest,geo in specs:
  f=read(fit/'frozen-sources.json');sources[label]=f
  raw=fit/'jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.rbf'
  assert sha(rbfr)==digest and raw.read_bytes().translate(reverse)==rbfr.read_bytes()
  qsf=fit/'jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
  text=qsf.read_text();defs=[line for line in text.splitlines() if 'VERILOG_MACRO' in line]
  has=lambda name:any(name in line for line in defs)
  assert has('INCLUDE_GPU_SM64_GEOMETRY')==geo and has('INCLUDE_GPU_GEOMETRY_WORKER')==geo
  report['configurations'][label]=dict(rbf_r_sha256=digest,raw_sha256=sha(raw),geometry=geo,gpu_sources={k:f['src/fpga/common/'+k] for k in ['gpu_core.v','gpu_geometry_worker.v','gpu_tex_cache.v','gpu_color_depth_cache.sv']})
  evidence += [fit/'frozen-sources.json',fit/'inventory.json',qsf,raw,rbfr]
 captures={}
 for geo in [0,1]:
  d=CAP/f'capture-baseline-geo{geo}';m=read(d/'inputs.json');assert m['success'] and m['geometry_capability']==bool(geo)
  assert sha(d/'sm64.elf')==APP
  for rel,digest in m['results_sha256'].items():assert sha(d/rel)==digest,(geo,rel)
  captures[str(geo)]=dict(frames=read(d/'summary.json')['frames'],elf_sha256=APP,hw_features=m['hw_features'],forced_geometry=bool(geo),audio=m['audio'],panel=m['panel'])
  evidence += [d/'inputs.json',d/'frames.csv',d/'scene.csv',d/'summary.json']
 assert captures['0']['frames']==captures['1']['frames']==721
 assert (CAP/'capture-baseline-geo0/scene.csv').read_bytes()==(CAP/'capture-baseline-geo1/scene.csv').read_bytes()
 report['existing_app_model_captures']=captures
 selected=read(CAP/'replay-selected-dual/manifest.json')
 for name in ['gpu_core.v','gpu_tex_cache.v','gpu_color_depth_cache.sv']:
  assert selected['sources']['frozen/common/'+name]==sources['B']['src/fpga/common/'+name]
  assert sha(CAP/'replay-selected-dual/frozen/common'/name)==sources['B']['src/fpga/common/'+name]
 worker=read(WORKER/'replay-worker-v6/manifest.json')
 for name in ['gpu_core.v','gpu_geometry_worker.v','gpu_tex_cache.v','gpu_color_depth_cache.sv']:
  assert worker['sources']['frozen/common/'+name]==sources['C']['src/fpga/common/'+name]
  assert sha(WORKER/'replay-worker-v6/frozen/common'/name)==sources['C']['src/fpga/common/'+name]
 assert '-GINCLUDE_GPU_XFORM_MAC=0' in worker['command']
 assert all('+define+'+name in worker['command'] for name in ['INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_GPU_SM64_GEOMETRY'])
 evidence += [CAP/'replay-selected-dual/manifest.json',WORKER/'replay-worker-v6/manifest.json']
 before=read(CAP/'replay-results-baseline-geo0-selected-dual.json');older=read(CAP/'replay-results-baseline-geo1-worker-control.json')
 assert len(before)==16 and len(older)==8
 assert all(v['hardware_sha256']==sha(CAP/'replay-selected-dual/obj/Vtb_gpu') for v in before)
 assert all(v['hardware_sha256']==sha(WORKER/'replay-worker-v6/obj/Vtb_gpu') for v in older)
 old_known={v['frame']:v for v in read(P3/'replay-results-final-hidden-final-panel-worker.json') if v['label']=='final-hidden'}
 ref={v['frame']:v for v in before if v['mode']==1};cross=[]
 for v in older:
  frame=v['frame']
  for k in ['framebuffer_sha256','depth_sha256']:assert v[k]==old_known[frame][k]
  a=CAP/f'replays-selected-dual/baseline-geo0/1/frame_{frame:05d}.bin';b=CAP/f'replays-worker-control/baseline-geo1/1/frame_{frame:05d}.bin'
  x,y=a.read_bytes(),b.read_bytes();assert len(x)==len(y)==307212
  for k,start,end in [('framebuffer_sha256',0,153600),('depth_sha256',153612,307212)]:
   assert hashlib.sha256(x[start:end]).hexdigest()==ref[frame][k]
   assert hashlib.sha256(y[start:end]).hexdigest()==v[k]
  cross.append(dict(frame=frame,color_pixels_different=sum(a!=b for a,b in zip(struct.iter_unpack('<H',x[:153600]),struct.iter_unpack('<H',y[:153600]))),depth_values_different=sum(a!=b for a,b in zip(struct.iter_unpack('<H',x[153612:]),struct.iter_unpack('<H',y[153612:])))))
  evidence += [a,b]
 report['existing_gpu_replay']=dict(B_frames=8,B_traffic_modes=2,C_frames=8,C_traffic_modes=1,C_preserves_known_worker_output=True,cross_configuration_differences=cross,A_exact_same_app_replay=False)
 evidence += [CAP/'replay-results-baseline-geo0-selected-dual.json',CAP/'replay-results-baseline-geo1-worker-control.json',P3/'replay-results-final-hidden-final-panel-worker.json']
 deployed=read(P3/'pocket-deploy-20261004T134406Z/deployment.json')
 assert deployed['after_sha256']['Assets/sm64/common/sm64.elf']==APP and deployed['after_sha256']['Assets/sm64/common/os.bin']==OS
 assert deployed['after_sha256']['Cores/ThinkElastic.SM64/os30.rbf_r']==report['configurations']['C']['rbf_r_sha256']
 evidence.append(P3/'pocket-deploy-20261004T134406Z/deployment.json')
 timing=read(P3/'timing-tests.json');source=P3/'SM64/src/sm64/pocket/perf_pocket.c';header=source.with_suffix('.h')
 assert timing['source_sha256']==sha(source) and timing['header_sha256']==sha(header)
 assert read(P3/'results.json')['diagnostic_app_sha256']==APP
 evidence += [P3/'timing-tests.json',P3/'results.json',source,header,Path(__file__)]
 report['limits']=[
  'Same exact P3 ELF has full functional app-model captures under GEO0 and GEO1 and eight actual GPU-rendered scenes per route; these are not full FPGA/system execution.',
  'App-model capabilities use the selected-dual feature word plus explicit GEO0/1. This is not an exact model of every older hardware capability or texture-memory latency.',
  'C component sources match the actual older geometry-enabled bitstream and a deployment record binds the exact app/OS/C image. File deployment is not live hardware FPS evidence.',
  'A uses a separately validated ordinary GPU source; no retained exact990b framebuffer replay on the full A source/configuration was identified. No new simulation was run for this audit.',
  'GEO0 and GEO1 gameplay framebuffer/depth are not bit-identical. C retains its known worker output; compare complete configuration speed and visual quality, not pure issue width.',
  'P3 panel is a frozen completed window, not a paused game; 1/8 detailed sampling differs from all-frame FPS/LCD/REND.'
 ]
 provenance=OUT.parent/'provenance/manifest.json'
 assert provenance.exists()
 report['separate_full_source_provenance']=str(provenance.relative_to(ROOT))
 evidence += [provenance,OUT.parent/'provenance/REVIEW.txt']
 report['evidence_sha256']={str(p.relative_to(ROOT)):sha(p) for p in evidence}
 (OUT/'existing-compatibility.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report['existing_gpu_replay'],indent=2))
if __name__=='__main__':main()
