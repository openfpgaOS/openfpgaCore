#!/usr/bin/env python3
"""Exact sampled pending-lease overlaps; architectural upper bounds, not cycle credits."""
from pathlib import Path
import bisect,csv,hashlib,json,collections
O=Path(__file__).resolve().parent;V=O.parent;R=V/'candidate-v1/run'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p):
 with p.open()as f:return[{k:int(v,0)for k,v in r.items()}for r in csv.DictReader(f,delimiter='\t')]
ev=rows(R/'async-events.tsv');words=rows(R/'worker-words.tsv');overlap=rows(R/'async-overlap.tsv')
bounds=[list(map(int,l.split()))for l in(R/'boundaries.tsv').read_text().splitlines()]
receipt=V/'candidate-v1/check-independent-v1.json';good=json.loads(receipt.read_text());assert good['status'].startswith('PASS_AJ02_')
starts={r['job_seq']:r['cycle']for r in ev if r['job_take']};ends={}
for a,b in zip(ev,ev[1:]):
 if b['completed_serial']!=a['completed_serial']:ends[b['completed_serial']]=b['cycle']
byjob=collections.defaultdict(list)
for w in words:
 if w['front_state']==7:byjob[w['serial']].append(w)
jobs={}
for seq,start in starts.items():
 ws=byjob[seq];i=0;last_vertex=None;first_draw=None;vertices=draws=0;repaired=any(w['repair']for w in ws)
 while i<len(ws):
  h=ws[i]['word'];op=h>>24;n=h&8191;assert i+n<len(ws)
  if op==0x56:last_vertex=ws[i+n]['cycle'];vertices+=1
  elif op in(0x54,0x4e):
   draws+=1
   if first_draw is None:first_draw=ws[i]['cycle']
  else:raise AssertionError(op)
  i+=n+1
 assert i==len(ws)
 jobs[seq]={'seq':seq,'take':start,'done':ends[seq],'repaired':repaired,'first_output':ws[0]['cycle']if ws else None,'last_vertex_output':last_vertex,'first_draw':first_draw,'last_output':ws[-1]['cycle']if ws else None,'vertices':vertices,'draws':draws,'output_words':len(ws)}
assert len(jobs)==good['async_lifetime']['jobs']
pending=[]
for i,r in enumerate(ev):
 if r['lease_pending']and(i==0 or not ev[i-1]['lease_pending']):pending.append({'ticket':r['lease_ticket'],'start':r['cycle'],'descriptor_at_request':r['descriptor_serial'],'active_at_request':r['active'],'context_open_at_request':r['context_open'],'requested_prefix':r['lease_target']})
 if r['lease_granted']and i and not ev[i-1]['lease_granted']:
  assert pending[-1]['ticket']==r['granted_ticket'];pending[-1]['grant']=r['cycle'];pending[-1]['completed_at_grant']=r['completed_serial']
assert all('grant'in p for p in pending)
def duration(a,b,c,d):return max(0,min(b,d)-max(a,c))
phases={}
for phase,lo,hi in((1,bounds[0][1],bounds[1][1]),(2,bounds[1][1],bounds[2][1])):
 counts=collections.Counter();parts=collections.Counter();windows=[]
 for i,r in enumerate(ev):
  a=max(lo,r['cycle']);b=min(hi,ev[i+1]['cycle']if i+1<len(ev)else hi)
  if b<=a or not r['lease_pending']:continue
  n=b-a;counts['lease_pending']+=n
  # Exclusive partition describes visible protocol state, not a stall root cause.
  if r['repair_pending']:part='repair'
  elif r['raw_state']:part='raw_engine'
  elif r['cull_owner']:part='cull_engine'
  elif r['context_open']:part='context_bundle'
  elif r['active']and not r['legacy']:part='async_other_including_conversion_output'
  elif r['active']:part='legacy_active'
  else:part='handoff_idle'
  parts[part]+=n
  if r['active']and not r['legacy']:
   j=jobs[r['descriptor_serial']]
   if not j['repaired']and j['first_output']is not None:
    counts['after_first_output_before_done']+=duration(a,b,j['first_output']+1,j['done'])
   if not j['repaired']and j['last_vertex_output']is not None:
    counts['after_last_vertex_word_before_done']+=duration(a,b,j['last_vertex_output']+1,j['done'])
 # Joint flags are sampled every cycle by the immutable native observer.
 joint=collections.Counter()
 for r in overlap:
  if r['phase']==phase and r['mask']&1024:
   joint['lease_pending']+=r['cycles']
   for bit,name in((1,'cpu_retired'),(8,'raw_engine'),(16,'cull_engine'),(64,'repair_pending'),(128,'gpu_fragment'),(256,'output_fifo_full')):
    if r['mask']&bit:joint[name]+=r['cycles']
 assert joint['lease_pending']==counts['lease_pending'],(phase,joint,counts)
 for p in pending:
  if not duration(p['start'],p['grant'],lo,hi):continue
  relevant=[]
  for seq,j in jobs.items():
   if j['repaired']or j['first_output']is None:continue
   first=duration(p['start'],p['grant'],max(lo,j['first_output']+1),min(hi,j['done']))
   last=0 if j['last_vertex_output']is None else duration(p['start'],p['grant'],max(lo,j['last_vertex_output']+1),min(hi,j['done']))
   if first or last:
    remaining=sum(w['cycle']>=p['start']for w in byjob[seq])
    relevant.append({'seq':seq,'after_first_output':first,'after_last_vertex_word':last,'remaining_output_words_at_request':remaining,**{k:j[k]for k in('vertices','draws','output_words','first_output','last_vertex_output','done')}})
  windows.append({**p,'pending_cycles_in_phase':duration(p['start'],p['grant'],lo,hi),'jobs':relevant})
 assert sum(sum(j['after_first_output']for j in p['jobs'])for p in windows)==counts['after_first_output_before_done']
 assert sum(sum(j['after_last_vertex_word']for j in p['jobs'])for p in windows)==counts['after_last_vertex_word_before_done']
 phases[str(phase)]={'interval_cycles':hi-lo,'lease_pending_cycles':counts['lease_pending'],'exclusive_protocol_state_partition':dict(parts),'overlapping_cycle_flags':dict(joint),'conservative_raw_release_windows':dict(counts),'requests':windows}
result={'status':'PASS_EXACT_TRACE_LEASE_WINDOWS','description':'Existing AJ02 candidate trace only. No replay/source changes. These are overlapping intervals during which an earlier-release architecture might unblock CPU; they are not additive saved cycles or a net performance prediction.',
 'milestones':{'first_output':'After first accepted ordinary packet word. Classification/prevalidation has completed, but current implementation can still read/convert later vertices. A complete converted-vertex snapshot could release raw earlier; snapshot latency/cost is not measured here.','last_vertex_word':'After final payload word of the last0x56 vertex packet is accepted. Successful hardware jobs then emit only0x54 indices; this is a conservative observable bound after the final raw vertex read.','completion':'completed_serial transition, after all generated output words are stored; does not imply GPU raster completion.'},
 'limitations':['Only successful hardware jobs qualify for the two windows; repair jobs excluded. Warm interval has no repair jobs.','Shared FMA usage, packet conversion and memory latency may overlap GPU work. Releasing raw early requires separate legacy/job context and output-controller correctness; no architecture is assumed implemented.','The trace gives remaining generated words and exact aggregate FIFO-full overlaps, but does not expose FIFO read pointer per request. Remaining words are not instantaneous FIFO occupancy.','The after-last-vertex window is a subset of after-first-output. Do not add them. Snapshot filling, tighter FIFO capacity, arbitration, BRAM ports and CPU overhead can consume the apparent headroom.'],
 'phases':phases,'inputs_sha256':{str(p):sha(p)for p in(R/'async-events.tsv',R/'worker-words.tsv',R/'async-overlap.tsv',R/'boundaries.tsv',receipt,Path(__file__).resolve())}}
out=O/'result.json';assert not out.exists();out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({p:{k:v for k,v in x.items()if k!='requests'}for p,x in phases.items()},indent=2))
