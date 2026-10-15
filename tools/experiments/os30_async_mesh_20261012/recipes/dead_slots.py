#!/usr/bin/env python3
"""Read-only source-bound dead-canonical-slot coverage on the frozen native trace."""
from pathlib import Path
from collections import Counter
import hashlib,json,struct
from elftools.elf.elffile import ELFFile
A=Path(__file__).resolve().parents[1];P=A.parent;M=P/'mesh-front-v1'
TASKS=M/'review/native-task-v3/tasks-classified.json'
OUT=M/'work-core/task-outcomes-v1.json'
ELF=P/'queued-front-v1/work/sm64-v2/.obj/sm64-queued-front-v2-perfdiag/app.elf'
SNAP=P/'queued-front-v1/fixture-v2/capture/run-reference/snapshot/sdram.bin'
tasks=json.loads(TASKS.read_text());outcomes={x['task']:x for x in json.loads(OUT.read_text())['tasks']}
snapshot=SNAP.read_bytes()
with ELF.open('rb') as f:
 e=ELFFile(f);syms=e.get_section_by_name('.symtab')
 lo=syms.get_symbol_by_name('_start')[0]['st_value'];hi=syms.get_symbol_by_name('__EH_FRAME_BEGIN__')[0]['st_value']
 segments=[(s['p_vaddr'],s.data()) for s in e.iter_segments() if s['p_type']=='PT_LOAD']
def word_pair(addr):
 assert lo<=addr<=hi-8 and not addr&7
 for begin,data in segments:
  if begin<=addr and addr+8<=begin+len(data):
   raw=data[addr-begin:addr-begin+8]
   assert raw==snapshot[addr-0x10000000:addr-0x10000000+8]
   return struct.unpack('<II',raw)
 raise AssertionError('Unmapped immutable command')
def proof(t,immediate=False):
 pending=((1<<t['count'])-1)<<t['first'];addr=t['end'];loads=0
 for steps in range(1,257):
  if not lo<=addr<=hi-8 or addr&7:return {'safe':False,'reason':'bounds','steps':steps}
  a,b=word_pair(addr);op=a>>24
  if op==1:
   count=a>>12&255;first=(a>>1&127)-count
   if not 0<count<=32 or not 0<=first<=32-count:return {'safe':False,'reason':'shape','steps':steps}
   loads+=1;pending&=~(((1<<count)-1)<<first)
   if not pending:return {'safe':True,'steps':steps,'loads':loads,'proof_end':addr+8}
  elif op in (5,6):
   for w in ([a] if op==5 else [a,b]):
    for shift in (16,8,0):
     index=((w>>shift)&255)//2
     if index>=32 or pending&(1<<index):return {'safe':False,'reason':'canonical_read','steps':steps}
  else:return {'safe':False,'reason':hex(op),'steps':steps}
  if immediate:return {'safe':False,'reason':'not_immediately_dead','steps':steps}
  addr+=8
 return {'safe':False,'reason':'bound','steps':256}
details=[]
for t in tasks:
 if t['task'] not in outcomes:continue
 # Original span is also independently bound to immutable ELF bytes.
 pairs=[word_pair(a) for a in range(t['dl'],t['end'],8)]
 assert pairs[0][0]>>24==1
 triangles=[]
 for a,b in pairs[1:]:
  assert a>>24 in (5,6)
  triangles += [[((w>>s)&255)//2 for s in (16,8,0)] for w in ([a] if a>>24==5 else [a,b])]
 assert triangles==t['triangles']
 outcome=outcomes[t['task']]
 details.append({k:t[k] for k in ['task','phase','dl','source','count','first','end','triangle_count','material_ready','material_elig','geometry_mode']} |
                {'suffix':proof(t),'immediate':proof(t,True),'accepted_geometry':outcome['accepted_geometry'],
                 'fallback_reason':outcome['fallback_reason'],'emitted_triangles':outcome['emitted_triangles']})
summary={}
for phase in (1,2):
 selected=[t for t in details if t['phase']==phase];summary[str(phase)]={}
 for mode in ('immediate','suffix'):
  for ready in (False,True):
   eligible=[t for t in selected if (not ready or(t['material_ready'] and t['material_elig']))]
   accepted=[t for t in eligible if t[mode]['safe']]
   ids={t['task'] for t in accepted};runs=[]
   for i in sorted(ids):
    if i-1 not in ids:runs.append(1)
    else:runs[-1]+=1
   summary[str(phase)][mode+('_prepared' if ready else '_all')]={
    'tasks':len(accepted),'input_triangles':sum(t['triangle_count'] for t in accepted),
    'observed_geometry_accepted':sum(t['accepted_geometry'] for t in accepted),
    'fallback_reasons':dict(Counter(t['fallback_reason'] for t in accepted if not t['accepted_geometry'])),
    'G_FOG_clear_tasks':sum(not t['geometry_mode']&0x10000 for t in accepted),
    'consecutive_LOAD_run_lengths':dict(Counter(runs)),'runs':len(runs),
    'max_suffix_commands':max((t[mode]['steps'] for t in accepted),default=0),
    'rejected_reason':dict(Counter(t[mode]['reason'] for t in eligible if not t[mode]['safe']))}
inputs=[Path(__file__).resolve(),TASKS,OUT,ELF,SNAP,M/'review/native-task-v3/passivity.json']
report={'status':'PASS_SOURCE_BOUND_COVERAGE_NOT_INTEGRATED_ADMISSION','summary':summary,'tasks':details,
 'scope':['Frozen qualified queued-front app trace, not a fabricated warm display list. Every inspected original/suffix command matches ELF and captured SDRAM bytes.',
 'Only VTX/TRI1/TRI2 suffixes are traversed. State commands, unknown/list/end commands and canonical reads reject; maximum256commands. Runtime caller must retain immutable ELF ownership contract.',
 'Prepared/nonzero material is a proxy. Final shader/alpha/LOD material gate remains required; G_FOG alone is not a material exclusion.',
 'Consecutive LOAD runs use actual task-call IDs. Every nonadmitted/ineligible LOAD still requires a raw-engine lease and can limit CPU lead; counts are not a speedup estimate.'],
 'inputs_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
dest=A/'analysis/dead-slots.json';assert not dest.exists();dest.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(summary,indent=2))
