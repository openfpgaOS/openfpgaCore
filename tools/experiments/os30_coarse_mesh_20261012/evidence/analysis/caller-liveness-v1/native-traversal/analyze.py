#!/usr/bin/env python3
"""Exact initial snapshot DL traversal; immutable caller suffix proof only.
Warm memory is not captured, so warm caller stacks are not asserted from it.
"""
import collections,csv,hashlib,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
H=Path(__file__).resolve().parent;C=H.parents[2];P=C.parent
B=P/'priority-mesh-v1';V=B/'validation';R=V/'control-v2/run';F=V/'fixture-v2'
ELF=Path(json.loads((R.parent/'build.json').read_text())['elf'])
SNAP=F/'capture/run-reference/snapshot';state=json.loads((SNAP/'state.json').read_text());mem=(SNAP/'sdram.bin').read_bytes()
APP=B/'work-app-v2/src/sm64/sm64/src/pc/gfx/gfx_pc.c'
ledgerpath=C/'analysis/span-coverage-v1/ledger.json';ledger=json.loads(ledgerpath.read_text())
with ELF.open('rb') as f:
 e=ELFFile(f);segs=[(s['p_vaddr'],s.data()) for s in e.iter_segments() if s['p_type']=='PT_LOAD'];sy=e.get_section_by_name('.symtab');lo=sy.get_symbol_by_name('_start')[0]['st_value'];hi=sy.get_symbol_by_name('__EH_FRAME_BEGIN__')[0]['st_value']
def immutable(a,n=8):
 if not lo<=a<=hi-n:return None
 for b,s in segs:
  if b<=a<=b+len(s)-n:return s[a-b:a-b+n]
 return None
def pair(a,static=False):
 d=immutable(a) if static else mem[a-0x10000000:a-0x10000000+8]
 if d is None or len(d)!=8:return None
 return struct.unpack('<II',d)
def indices(w,v):return [(x>>s&255)//2 for x in ([w,v] if w>>24==6 else [w]) for s in [16,8,0]]
events=[];pc=state['commands'];stack=[]
while True:
 assert len(events)<200000
 w,v=pair(pc);op=w>>24;events.append({'address':pc,'w0':w,'w1':v,'stack':stack.copy()})
 if op==0xdf:
  if not stack:break
  pc=stack.pop();continue
 if op==0xde:
  if not(w>>16&1):stack.append(pc+8)
  assert len(stack)<128;pc=v;continue
 pc+=24 if op in [0xe4,0xe5] else 16 if op==0xf6 else 8
vt=[(i,e) for i,e in enumerate(events) if e['w0']>>24==1]
with (R/'vtx-calls.tsv').open() as f:calls=[{k:int(v,0) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]
cold=[c for c in calls if c['phase']==1];warm=[c for c in calls if c['phase']==2]
expected=[(e['address'],e['w0']>>12&255,(e['w0']>>1&127)-(e['w0']>>12&255),e['w1']) for _,e in vt]
def ident(c):return(c['s1'],c['a0'],c['a1'],c['a2'])
assert [ident(c) for c in cold]==expected,'initial snapshot traversal differs from actual cold CPU calls'

def proof(start,count,returns):
 alive=(1<<count)-1;pc=start;stack=list(returns);seen=set();path=[];nret=0;ncall=0
 for step in range(256):
  if (pc,tuple(stack),alive) in seen:return False,'cycle',path,nret,ncall
  seen.add((pc,tuple(stack),alive));p=pair(pc,True)
  if p is None:return False,'mutable_or_unknown_command',path,nret,ncall
  w,v=p;op=w>>24;path.append([pc,w,v,alive])
  if op==1:
   n=w>>12&255;end=w>>1&127
   if not(0<n<=end<=32):return False,'invalid_vtx',path,nret,ncall
   alive&=~(((1<<n)-1)<<(end-n))
   if not alive:return True,'overwritten',path,nret,ncall
  elif op in [5,6]:
   if any(x>=32 or alive>>x&1 for x in indices(w,v)):return False,'live_triangle_read',path,nret,ncall
  elif op==0xdf:
   if not stack:return False,'frame_end',path,nret,ncall
   pc=stack.pop();nret+=1;continue
  elif op==0xde:
   if immutable(v) is None:return False,'mutable_or_unknown_DL_target',path,nret,ncall
   if not(w>>16&1):
    stack.append(pc+8)
    if len(stack)>16:return False,'stack_limit',path,nret,ncall
   pc=v;ncall+=1;continue
  else:return False,'state_0x%02x'%op,path,nret,ncall
  pc+=8
 return False,'scan_limit',path,nret,ncall
rows=[]
for c,(_,e),l in zip(cold,vt,ledger['1']['calls']):
 assert c['s1']==l['address']
 if l['reason']:continue
 okay,why,path,returns,calls=proof(l['end'],l['count'],e['stack'])
 rows.append({'call':l['call'],'source':l['address'],'count':l['count'],'triangles':l['triangles'],'previous_dead':l['dead_after'],'previous_reason':l['dead_reason'],'async':l['async'],'raw':l['reference_raw'],'return_stack':e['stack'],'caller_dead':okay,'reason':why,'returns':returns,'nested_calls':calls,'proof_path':path})
new=[r for r in rows if r['caller_dead'] and not r['previous_dead']]
same_order=len(warm)==len(expected) and [(c['s1'],c['a0'],c['a1']) for c in warm]==[x[:3] for x in expected]
warm_statics=[(c['s1'],c['a0'],c['a1'],c['a2']) for c in warm if immutable(c['s1']) is not None]
cold_statics=[x for x in expected if immutable(x[0]) is not None]
summary={'status':'PASS_COLD_SOURCE_BOUND_TRAVERSAL_AND_IMMUTABLE_CALLER_PROOF','command_events':len(events),'exact_cold_CPU_LOAD_calls':len(cold),'candidate_spans':len(rows),'old_proved':sum(r['previous_dead'] for r in rows),'caller_proved':sum(r['caller_dead'] for r in rows),'new_spans':len(new),'new_triangles':sum(r['triangles'] for r in new),'new_hardware_LOADs':sum(r['raw'] for r in new),'new_by_prior_reason':dict(collections.Counter(r['previous_reason'] for r in new)),'remaining_failures':dict(collections.Counter(r['reason'] for r in rows if not r['caller_dead'])),'new_return_depth':dict(collections.Counter(r['returns'] for r in new)),'warm_VTX_shape_address_sequence_matches':same_order,'warm_immutable_VTX_full_identity_sequence_matches':warm_statics==cold_statics,'limits':['Only the initial cold full SDRAM snapshot supplies a complete dynamic caller graph. Warm caller stacks are not directly observed; matching VTX sequences do not prove identical dynamic caller edges.','Every proof command/target is immutable ELF; END returns to mutable caller storage are rejected, even when initial snapshot shows an overwrite.','Every non-VTX/TRI/DL/END opcode remains a state barrier. No renderer/material or predicted timing gain is claimed.']}
inputs=[Path(__file__),ELF,APP,SNAP/'sdram.bin',SNAP/'state.json',R/'vtx-calls.tsv',ledgerpath]
summary['inputs_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
(H/'events.json').write_text(json.dumps(events,separators=(',',':'))+'\n');(H/'ledger.json').write_text(json.dumps(rows,indent=2)+'\n');(H/'result.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
