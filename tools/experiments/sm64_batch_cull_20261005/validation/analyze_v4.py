#!/usr/bin/env python3
"""Audit paired first-level software captures and actual GPU RTL replays."""
import csv,hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/sm64-batch-cull-20261005'
OUT=BASE/'validation'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
rows=lambda p:list(csv.DictReader(p.open()))
def compare(frames0,frames1,start,end):
 a,b=frames0[start:end],frames1[start:end]
 metrics={}
 for key in ['insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict']:
  x,y=sum(int(v[key]) for v in a),sum(int(v[key]) for v in b)
  metrics[key]=dict(reference=x,candidate=y,saving_percent=100*(1-y/x) if x else None)
 return dict(first_frame=start,last_frame=end-1,frames=end-start,metrics=metrics)
def main():
 evidence=[];result={'scope':'Paired functional software model plus separate GPU RTL replay; no device FPS or CPU/GPU overlap measurement.','geometry_modes':{}}
 for geo in [0,1]:
  ref=OUT/f'capture-reference-geo{geo}';new=OUT/f'capture-candidate-v4-geo{geo}'
  a,b=[read(p/'inputs.json') for p in [ref,new]]
  assert a['success'] and b['success'] and a['hw_features']==b['hw_features']=='0x2b136753'
  for key in ['geometry_capability','command_dma','audio','panel']:assert a[key]==b[key],key
  assert a['geometry_capability']==bool(geo) and not a['panel'] and a['audio'] and not a['command_dma']
  for path,expected in a['sha256'].items():
   if Path(path).name!='sm64.elf':assert b['sha256'][path]==expected==sha(Path(path)),path
  for directory,inputs in [(ref,a),(new,b)]:
   for rel,digest in inputs['results_sha256'].items():assert sha(directory/rel)==digest,(directory,rel)
   evidence.append(directory/'inputs.json')
   summary=read(directory/'summary.json');assert summary['frames']==721 and summary['gpu_sync']['ring_overflows']==0 and summary['status']=='limit'
   evidence += [directory/x for x in ['summary.json','scene.csv','frames.csv','geometry.csv','diagnostic.csv','culling.csv','console.log']]
  assert (ref/'scene.csv').read_bytes()==(new/'scene.csv').read_bytes()
  x,y=rows(ref/'frames.csv'),rows(new/'frames.csv');assert len(x)==len(y)==721
  c0,c1=rows(ref/'culling.csv'),rows(new/'culling.csv');assert len(c0)==len(c1)==721
  work={key:dict(reference=int(c0[720][col])-int(c0[359][col]),candidate=int(c1[720][col])-int(c1[359][col])) for key,col in [('outside_batches','stat0'),('outside_vertices','stat1'),('recovered_vertices','stat2')]}
  perframe=[]
  for f in range(360,721):
   r=compare(x,y,f,f+1);r['outside_batches']=int(c1[f]['stat0'])-int(c1[f-1]['stat0']);r['outside_vertices']=int(c1[f]['stat1'])-int(c1[f-1]['stat1']);r['recovered_vertices']=int(c1[f]['stat2'])-int(c1[f-1]['stat2']);perframe.append(r)
  noout=[r for r in perframe if r['outside_batches']==0]
  diag=[]
  for directory in [ref,new]:
   last=rows(directory/'diagnostic.csv')[-1]
   assert last['stat0']=='1' and last['stat18']=='3' and int(last['stat15'])==geo
   diag.append({k:int(last[f'stat{i}']) for k,i in [('raw_vertices',35),('cpu_vertices',36),('outside_vertices',37),('recovered_vertices',38)]})
  result['geometry_modes'][str(geo)]=dict(gameplay=compare(x,y,360,721),windows=[compare(x,y,f,min(f+60,721)) for f in range(360,721,60)],work_counts=work,last_diagnostic_sample=dict(reference=diag[0],candidate=diag[1]),per_frame=perframe,no_rejection_gameplay_frames=[v['first_frame'] for v in noout],
    worst_modeled_cycle_frame=min(perframe,key=lambda v:v['metrics']['est_cycles']['saving_percent']),best_modeled_cycle_frame=max(perframe,key=lambda v:v['metrics']['est_cycles']['saving_percent']))
  if geo==0:assert work['outside_vertices']['reference']==0 and work['outside_vertices']['candidate']>0
 # The host-only observer leaves the retained baseline outputs unchanged.
 observer=read(OUT/'observation-identity.json');assert observer['retained_scene_and_geometry_identical']
 for geo in ['0','1']:assert all(observer[geo].values())
 result['host_observer_identity']=observer;evidence.append(OUT/'observation-identity.json')
 replay_pairs=[]
 for geo,hardware in [(0,'selected-dual'),(1,'worker-control')]:
  before=read(OUT/f'replay-results-baseline-geo{geo}-{hardware}.json');after=read(OUT/f'replay-results-candidate-v4-geo{geo}-{hardware}.json')
  index={(v['mode'],v['frame']):v for v in before};assert len(after)==len(before)==(16 if geo==0 else 8)
  for v in after:
   old=index[(v['mode'],v['frame'])]
   for key in ['hardware_sha256','framebuffer_sha256','depth_sha256']:assert v[key]==old[key],(geo,v['frame'],v['mode'],key)
   assert v['depth_sha256'] is not None
   replay_pairs.append(dict(geometry=geo,mode=v['mode'],frame=v['frame'],framebuffer_sha256=v['framebuffer_sha256'],depth_sha256=v['depth_sha256'],reference_gpu_cycles=old['stats']['cycles'],candidate_gpu_cycles=v['stats']['cycles'],gpu_cycle_saving_percent=100*(1-v['stats']['cycles']/old['stats']['cycles']),reference_commands=old['commands'],candidate_commands=v['commands']))
  evidence += [OUT/f'replay-results-{label}-geo{geo}-{hardware}.json' for label in ['baseline','candidate-v4']]
  for label in ['baseline','candidate-v4']:
   evidence += list((OUT/f'replays-{hardware}/{label}-geo{geo}').rglob('*.log'))
 # Retained, known-good GEO1 pixel/depth hashes survive the harness caps change.
 old=read(ROOT/'docs/measurements/sm64-vertex-perf-20261004/replay-results-final-hidden-final-panel-worker.json');old={v['frame']:v for v in old if v['label']=='final-hidden'}
 for v in replay_pairs:
  if v['geometry']==1:
   for key in ['framebuffer_sha256','depth_sha256']:assert v[key]==old[v['frame']][key]
 result['gpu_replay_pairs']=replay_pairs;result['retained_geo1_known_good_frames']=8
 # Repeat GEO0 without diagnostic probes using the same normal app configuration.
 normal0=OUT/'capture-normal-reference-geo0';normal1=OUT/'capture-normal-candidate-v4-geo0'
 assert (normal0/'scene.csv').read_bytes()==(normal1/'scene.csv').read_bytes()==(OUT/'capture-reference-geo0/scene.csv').read_bytes()
 for directory in [normal0,normal1]:
  inputs=read(directory/'inputs.json');assert inputs['success'] and not inputs['geometry_capability']
  for rel,digest in inputs['results_sha256'].items():assert sha(directory/rel)==digest
  evidence += [directory/x for x in ['inputs.json','summary.json','frames.csv','scene.csv','culling.csv']]
 normal=compare(rows(normal0/'frames.csv'),rows(normal1/'frames.csv'),360,721)
 nrpath=OUT/'replay-results-normal-candidate-v4-geo0-selected-dual.json'
 nr=[v for v in read(OUT/'replay-results-normal-reference-geo0-normal-candidate-geo0-selected-dual.json') if v['label']=='normal-reference-geo0']+read(nrpath);assert len(nr)==16
 nrindex={(v['label'],v['frame']):v for v in nr}
 for frame in [40,120,200,360,400,500,600,720]:
  a,b=[nrindex[(label,frame)] for label in ['normal-reference-geo0','normal-candidate-v4-geo0']]
  diag=next(v for v in replay_pairs if v['geometry']==0 and v['mode']==1 and v['frame']==frame)
  for key in ['framebuffer_sha256','depth_sha256']:assert a[key]==b[key]==diag[key]
  assert a['stats']['cycles']==diag['reference_gpu_cycles']
 normal['pixel_depth_pairs']=8;normal['gpu_cycles_reported_separately']=True;result['normal_geo0_control']=normal;evidence.append(nrpath)
 # Verify exact GPU/cache/source snapshot and current executable identity.
 replay=OUT/'replay-selected-dual';manifest=read(replay/'manifest.json')
 assert sha(replay/'obj/Vtb_gpu')==manifest['simulator_sha256']
 for rel,digest in manifest['sources'].items():assert sha(replay/rel)==digest,rel
 selected=read(ROOT/'docs/measurements/os30-dual-20261004/fit-rvc-skid-pointer-packer-coord-v1/frozen-sources.json')
 for path in (replay/'frozen/common').iterdir():
  if path.is_file():assert sha(path)==selected['src/fpga/common/'+path.name],path
 assert manifest['gpu']['parameters']['INCLUDE_GPU_XFORM_MAC']==0 and manifest['gpu']['parameters']['INCLUDE_VERT_TRI']==1
 assert not set(manifest['gpu']['variant']['defs'].split())&{'INCLUDE_GPU_GEOMETRY_WORKER','INCLUDE_GPU_SM64_GEOMETRY','INCLUDE_TEX_QUEUE_MLAB'}
 result['selected_gpu_configuration']=dict(cpu_geometry_accelerator=False,triangle_gpu=True,texture_queues_bram=True,gpu_sha256=sha(replay/'frozen/common/gpu_core.v'),texture_cache_sha256=sha(replay/'frozen/common/gpu_tex_cache.v'),render_cache_sha256=sha(replay/'frozen/common/gpu_color_depth_cache.sv'),simulator_sha256=manifest['simulator_sha256'])
 evidence += [replay/'manifest.json',OUT/'qsim-manifest.json',OUT/'qsim-observed-manifest.json',OUT/'capabilities/manifest.json',Path(__file__)]
 result['limitations']=['qsim uses an estimated single-issue CPU/memory timing model, not the selected dual CPU RTL. Instruction counts are actual guest instructions; modeled cycle changes are not hardware FPS.',
  'GPU replay executes the selected ordinary triangle RTL and Pocket SDRAM model separately from CPU execution; traffic schedules are sensitivity tests, not measured SM64 contention.',
  'The retained capture uses a 512KiB SDRAM-backed texture arena, not the hardware CRAM1 texture performance path.',
  'Eight frames cover the existing deterministic first-level route and introductory scenes; this does not establish every level/camera/input sequence.',
  'GEO1 uses the retained worker renderer as a compatibility control, not the selected GEO0 core.',
  'No-rejection frames, if absent, provide no first-level visible-only overhead isolation; focused eligibility tests are separate evidence.']
 result['evidence_sha256']={str(p.relative_to(ROOT)):sha(p) for p in evidence}
 (OUT/'results-v4.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:{n:v[n] for n in ['gameplay','work_counts','worst_modeled_cycle_frame']} for k,v in result['geometry_modes'].items()},indent=2))
if __name__=='__main__':main()
