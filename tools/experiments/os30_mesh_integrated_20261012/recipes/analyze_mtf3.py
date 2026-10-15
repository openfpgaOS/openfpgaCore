#!/usr/bin/env python3
"""Read-only native replay attribution; elapsed intervals determine performance.
Retirement-gap attribution and hardware busy histograms are NOT additive savings.
"""
import argparse,bisect,collections,csv,hashlib,json,subprocess
from pathlib import Path
A=argparse.ArgumentParser();A.add_argument('--control',type=Path,required=True);A.add_argument('--candidate',type=Path);A.add_argument('--out',type=Path,required=True);a=A.parse_args()
bind={}
def file(p):
 p=p.resolve();bind[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest();return p
def js(p):return json.loads(file(p).read_text())
def table(p,delimiter=','):return list(csv.DictReader(file(p).open(),delimiter=delimiter))
def symbols(elf,overlay):
 rows=[]
 for p,origin in [(elf,'app'),(overlay,'native_services')]:
  out=subprocess.check_output(['readelf','-Ws',str(file(p))],text=True)
  # The assembly entry stubs have zero size; delimit these with the next .text symbol.
  raw=[]
  for line in out.splitlines():
   s=line.split()
   if len(s)>=8 and s[6]!='UND' and ((s[3]=='FUNC' and int(s[2])) or (origin=='native_services' and s[3]=='NOTYPE' and s[6]=='1' and not s[7].startswith('$'))):
    raw.append((int(s[1],16),int(s[2]),s[7]))
  raw.sort()
  for i,(start,size,name) in enumerate(raw):
   if not size and i+1<len(raw):size=raw[i+1][0]-start
   if size:rows.append((start,size,origin+':'+name))
 rows.sort();return rows,[x[0] for x in rows]
def profile(p,syms,starts,cycles):
 sums=collections.defaultdict(lambda:[0,0]);tot=[0,0]
 for r in table(p):
  pc=int(r['pc']);i=bisect.bisect_right(starts,pc)-1;s=syms[i] if i>=0 else None
  name=s[2] if s and pc<s[0]+s[1] else 'unmapped:'+hex(pc>>16)
  v=[int(r['retired']),int(r['cycles_since_previous_retirement'])]
  for j,x in enumerate(v):sums[name][j]+=x;tot[j]+=x
 return {'retired':tot[0],'retirement_gap_cycles':tot[1],'gap_minus_elapsed_cycles':tot[1]-cycles,
  'functions':[{'name':n,'retired':v[0],'gap_cycles':v[1],'percent_elapsed':100*v[1]/cycles} for n,v in sorted(sums.items(),key=lambda x:-x[1][1])]}
def stage(r):
 if not r['owner']:return 'no_owner'
 if r['legacy']:return 'legacy_load_owner'
 if r['pending']:return 'mesh_start_pending'
 if not r['raw_seen'] and not r['fallback']:return 'mesh_raw_load'
 if r['fallback']:return 'mesh_fallback_hold'
 if r['packet_error']:return 'mesh_packet_error_hold'
 if not r['preflight']:return 'mesh_command_fetch_and_cull'
 if r['validate_pending']:return 'mesh_validation_alias_drain'
 if not r['validation_started']:return 'mesh_material_prepare_hold'
 if r['publication_started']:return 'mesh_copy_release' if r['packet_complete'] else ('mesh_private_prefill' if 'queue_state' in r else 'mesh_publication')
 if r['packet_state']==10:
  if 'queue_state' not in r:return 'mesh_validated_marker_wait'
  if not r['arm_pending']:return 'mesh_validated_before_arm'
  if 'fill_active' in r:
   if r['fill_active'] and r['fill_serial']==r['serial']:return 'mesh_armed_alias_drain_or_header'
   return 'mesh_arm_wait_credit_or_reservation'
  if r['queue_state'] and r['queue_serial']!=r['serial']:return 'mesh_arm_wait_older_queue'
  return 'mesh_armed_alias_drain_or_reserve'
 return 'mesh_conversion_validation'
def mesh_details(run,bounds):
 es=run/'mesh-events.tsv';ov=run/'mesh-overlap.tsv'
 if not es.exists():return None
 ev=[{k:int(v) for k,v in r.items()} for r in table(es,'\t')];out={}
 for phase,(start,end) in enumerate(bounds,1):
  counts=collections.Counter();queues=collections.Counter();producer=collections.Counter();occupancy=collections.Counter();reserved_occupancy=collections.Counter();tot=collections.Counter();tasks=[];previous_owner=0
  for i,r in enumerate(ev):
   lo=max(start,r['cycle']);hi=min(end,ev[i+1]['cycle'] if i+1<len(ev) else end)
   if hi>lo:
    counts[stage(r)]+=hi-lo
    if 'queue_state' in r:
     labels={0:'empty',1:'header_prime',2:'ready_waiting_marker',3:'draining',4:'fault',5:'header_capture'} if 'fill_active' in r else {0:'empty',1:'filling',2:'ready_waiting_marker',3:'draining',4:'fault'}
     queues[labels.get(r['queue_state'],'unknown')]+=hi-lo
    if 'fill_active' in r:
     producer['filling' if r['fill_active'] else 'idle']+=hi-lo
     used=(r['committed_ptr']-r['read_ptr'])&1023
     reserved=(r['reserved_end']-r['read_ptr'])&1023 if r['fill_active'] else used
     assert used<=512 and reserved<=512,(r['cycle'],used,reserved)
     occupancy[used]+=hi-lo;reserved_occupancy[reserved]+=hi-lo
   if start<=r['cycle']<end and r['owner'] and not previous_owner:tasks.append({'serial':r['descriptor_serial'],'legacy':r['legacy'],'start_cycle':r['cycle']})
   previous_owner=r['owner']
  overlaps={int(r['mask']):int(r['cycles']) for r in table(ov,'\t') if int(r['phase'])==phase}
  def n(test):return sum(v for k,v in overlaps.items() if test(k))
  out[str(phase)]={'stage_cycles':dict(counts),'private_queue_state_cycles':dict(queues),'private_producer_cycles':dict(producer),'committed_word_occupancy_cycles':dict(sorted(occupancy.items())),'reserved_word_occupancy_cycles':dict(sorted(reserved_occupancy.items())),'owner_episodes':tasks,'overlap_mask_cycles':overlaps,
   'overlap':{'cycles':sum(overlaps.values()),'cpu_retire':n(lambda k:k&1),'gpu_busy':n(lambda k:k&2),'owner':n(lambda k:k&4),'raw_state_nonzero':n(lambda k:k&8),'cull_fma_inflight':n(lambda k:k&16),'cpu_retire_and_gpu_busy':n(lambda k:(k&3)==3),'owner_and_gpu_busy':n(lambda k:(k&6)==6),'owner_and_cpu_retire':n(lambda k:(k&5)==5),'raw_and_gpu_busy':n(lambda k:(k&10)==10),'cull_and_gpu_busy':n(lambda k:(k&18)==18),'generated_word_accept':n(lambda k:k&64),'bridge_output_accept':n(lambda k:k&32),'gpu_raster_state6':n(lambda k:k&128),'private_packet_write_accept':n(lambda k:k&256),'private_queue_busy':n(lambda k:k&512),'raw_owner_differs_from_queued_token':n(lambda k:k&1024),'arm_pending':n(lambda k:k&2048),'raw_state_and_gpu_raster':n(lambda k:(k&136)==136),'cull_fma_and_gpu_raster':n(lambda k:(k&144)==144),'new_owner_old_queue_and_gpu_raster':n(lambda k:(k&1152)==1152)}}
 return out
def run_analysis(base):
 run=base/'run';build=js(base/'build.json');result=js(run/'result.json');manifest=js(run/'manifest.json')
 for n,sha in manifest['outputs_sha256'].items():assert hashlib.sha256((run/n).read_bytes()).hexdigest()==sha,n
 check=js(base/'check.json') if (base/'check.json').exists() else {'status':'PENDING'}
 elf=Path(build['elf']);fixture=Path(build['config']).parent;overlay=fixture/'guest/overlay/overlay.elf';syms,starts=symbols(elf,overlay)
 file(Path(build['model'])/'prepared.json');file(fixture/'guest/manifest.json');file(fixture/'capture/manifest.json')
 rows=[[int(x) for x in line.split()] for line in file(run/'boundaries.tsv').read_text().splitlines()];assert len(rows)==3
 bounds=[(rows[i][1],rows[i+1][1]) for i in range(2)]
 gpu=js(run/'gpu-profile.json');stall=js(run/'stalls.json');fw=js(run/'first-word.json');events=table(run/'gpu-profile-events.csv');timeline=table(run/'gpu-profile-timeline.csv')
 phases={}
 for i,(start,end) in enumerate(bounds,1):
  phase=str(i);g=gpu['windows'][phase];cycles=end-start;assert g['cycles']==cycles
  cp=profile(run/f'pc-phase-{i}.csv',syms,starts,cycles);assert cp['retired']==rows[i][2]-rows[i-1][2]
  ev=[{'cycle':int(r['cycle']),'name':r['name']} for r in events if int(r['window'])==i];times={r['name']:r['cycle'] for r in ev}
  event_durations={'entry_to_render_end':times['sm64_perf_render_end']-start,'render_end_to_next_entry':end-times['sm64_perf_render_end'],'game_loop_to_next_entry':end-times['game_loop_one_iteration'],'render_end_to_game_loop':times['game_loop_one_iteration']-times['sm64_perf_render_end']}
  # Timeline bins bound overlap exactly without assuming distribution inside the two edge bins.
  segments={}
  for name,lo,hi in [('through_render_end',start,times['sm64_perf_render_end']),('after_render_end',times['sm64_perf_render_end'],end)]:
   low=high=0
   for r in timeline:
    l,h=int(r['begin']),int(r['end']);v=int(r['idle_empty'])
    if h<=lo or l>=hi:continue
    overlap=min(h,hi)-max(l,lo);span=h-l
    low+=max(0,v-(span-overlap));high+=min(v,overlap)
   segments[name]={'cycles':hi-lo,'gpu_idle_empty_cycles_min':low,'gpu_idle_empty_cycles_max':high}
  phases[phase]={'elapsed_cycles':cycles,'entry_cycle':start,'next_entry_cycle':end,'cpu':cp,'cpu_upload_words':rows[i][3]-rows[i-1][3],
   'gpu_partitions':{n:{'cycles':v,'percent_elapsed':100*v/cycles} for n,v in zip(gpu['partition_names'],g['partition'])},'gpu_metrics':g['metrics'],'gpu_reads':g['reads'],'cpu_load_stalls':stall['windows'][phase],'first_word':{k:v for k,v in fw['windows'][phase].items() if not isinstance(v,list)},'events':ev,'event_durations':event_durations,'timeline_segment_bounds':segments}
 return {'model':build['model'],'elf':str(elf),'elf_sha256':hashlib.sha256(elf.read_bytes()).hexdigest(),'check_status':check.get('status'), 'result':result,'phases':phases,'mesh':mesh_details(run,bounds)}
control=run_analysis(a.control.resolve());out={'schema':1,'semantics':['Elapsed gfx-entry-to-next-entry intervals include completed GPU output, audio service and next game-loop work.','Per-PC cycles_since_previous_retirement assigns the preceding gap to the next retired instruction, not an isolated self-time or guaranteed removable cost.','GPU partitions are exclusive; raw cache/bus/CPU wait metrics overlap each other and cannot be added to infer a frame bound.','Mesh ownership and FMA masks show actual overlap; they are not CPU wait unless joined to retired poll sites.','Native services execute on the modeled CPU and are separately named; they are not application geometry work.'],'control':control}
if a.candidate:
 cand=run_analysis(a.candidate.resolve());assert cand['elf_sha256']==control['elf_sha256'];out['candidate']=cand;comp={}
 for phase in ['1','2']:
  b=control['phases'][phase];c=cand['phases'][phase];bf={x['name']:x for x in b['cpu']['functions']};cf={x['name']:x for x in c['cpu']['functions']}
  diffs=[{'name':n,'control_gap_cycles':bf.get(n,{}).get('gap_cycles',0),'candidate_gap_cycles':cf.get(n,{}).get('gap_cycles',0),'gap_cycle_delta':cf.get(n,{}).get('gap_cycles',0)-bf.get(n,{}).get('gap_cycles',0),'retired_delta':cf.get(n,{}).get('retired',0)-bf.get(n,{}).get('retired',0)} for n in set(bf)|set(cf)]
  comp[phase]={'cycle_delta':c['elapsed_cycles']-b['elapsed_cycles'],'percent_cycle_change':100*(c['elapsed_cycles']/b['elapsed_cycles']-1),'retired_delta':c['cpu']['retired']-b['cpu']['retired'],'upload_word_delta':c['cpu_upload_words']-b['cpu_upload_words'],'gpu_busy_delta':c['gpu_metrics']['gpu_busy']-b['gpu_metrics']['gpu_busy'],'function_deltas':sorted(diffs,key=lambda r:-abs(r['gap_cycle_delta']))}
 out['comparison']=comp
file(Path(__file__));out['inputs_sha256']=bind;a.out.parent.mkdir(parents=True,exist_ok=True);assert not a.out.exists();a.out.write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'control':{k:{'cycles':p['elapsed_cycles'],'retired':p['cpu']['retired'],'gpu_busy':p['gpu_metrics']['gpu_busy'],'render':p['event_durations']['entry_to_render_end']} for k,p in control['phases'].items()},'comparison':out.get('comparison',{}),'output':str(a.out)},indent=2)[:2500])
