#!/usr/bin/env python3
"""Read-only vertex liveness proof coverage; state dispatch is never bypassed."""
import collections,hashlib,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile
H=Path(__file__).resolve().parent;C=H.parents[2];P=C.parent;B=P/'priority-mesh-v1'
OLD=C/'analysis/caller-liveness-v1/native-traversal';LED=C/'analysis/span-coverage-v1/ledger.json';ADM=C/'analysis/admission-v1/result.json'
ledger=json.loads(LED.read_text());events=json.loads((OLD/'events.json').read_text());old=json.loads((OLD/'result.json').read_text());admission=json.loads(ADM.read_text())
ELF=Path(json.loads((B/'validation/control-v2/build.json').read_text())['elf'])
with ELF.open('rb') as f:
 e=ELFFile(f);segs=[(s['p_vaddr'],s.data()) for s in e.iter_segments() if s['p_type']=='PT_LOAD'];sy=e.get_section_by_name('.symtab');lo=sy.get_symbol_by_name('_start')[0]['st_value'];hi=sy.get_symbol_by_name('__EH_FRAME_BEGIN__')[0]['st_value']
def pair(a):
 if not lo<=a<=hi-8:return None
 for b,s in segs:
  if b<=a<=b+len(s)-8:return struct.unpack_from('<II',s,a-b)
 return None
def indices(w,v):return [(x>>s&255)//2 for x in ([w,v] if w>>24==6 else [w]) for s in [16,8,0]]
SKIP={0xd7:'TEXTURE',0xfd:'SETTIMG',0xe8:'TILESYNC',0xe7:'PIPESYNC',0xd9:'GEOMETRYMODE',0xfb:'SETENVCOLOR',0xf5:'SETTILE',0xf2:'SETTILESIZE',0xf3:'LOADBLOCK',0xf4:'LOADTILE',0xf0:'LOADTLUT',0xfa:'SETPRIMCOLOR',0xfc:'SETCOMBINE',0xf8:'SETFOGCOLOR',0xe6:'LOADSYNC',0xe9:'FULLSYNC'}
def proof(start,count,returns,callers,skip):
 alive=(1<<count)-1;pc=start;stack=list(returns);seen=set();path=[];nret=0;ncall=0;skipped=[]
 for step in range(256):
  if (pc,tuple(stack),alive) in seen:return False,'cycle',path,nret,ncall,skipped
  seen.add((pc,tuple(stack),alive));p=pair(pc)
  if p is None:return False,'mutable_or_unknown_command',path,nret,ncall,skipped
  w,v=p;op=w>>24;path.append([pc,w,v,alive])
  if op==1:
   n=w>>12&255;end=w>>1&127
   if not(0<n<=end<=32):return False,'invalid_vtx',path,nret,ncall,skipped
   alive&=~(((1<<n)-1)<<(end-n))
   if not alive:return True,'overwritten',path,nret,ncall,skipped
  elif op in [5,6]:
   if any(x>=32 or alive>>x&1 for x in indices(w,v)):return False,'live_triangle_read',path,nret,ncall,skipped
  elif op in skip:skipped.append(op)
  elif callers and op==0xdf:
   if not stack:return False,'frame_end',path,nret,ncall,skipped
   pc=stack.pop();nret+=1;continue
  elif callers and op==0xde:
   if pair(v) is None:return False,'mutable_or_unknown_DL_target',path,nret,ncall,skipped
   if not(w>>16&1):
    stack.append(pc+8)
    if len(stack)>16:return False,'stack_limit',path,nret,ncall,skipped
   pc=v;ncall+=1;continue
  else:return False,'state_or_list_0x%02x'%op,path,nret,ncall,skipped
  pc+=8
 return False,'scan_limit',path,nret,ncall,skipped
cold_vtx=[e for e in events if e['w0']>>24==1];assert len(cold_vtx)==old['exact_cold_CPU_LOAD_calls']
context={r['call']:e['stack'] for r,e in zip(ledger['1']['calls'],cold_vtx)}
routes={r['call']:r['route'] for r in admission['details']}
outputs={};details={}
def run(phase,callers,skip,verbose=False):
 out=[]
 for r in ledger[phase]['calls']:
  if r['reason']:continue
  okay,why,path,returns,calls,skipped=proof(r['end'],r['count'],context[r['call']] if callers else [],callers,skip)
  out.append({'call':r['call'],'source':r['address'],'end':r['end'],'count':r['count'],'triangles':r['triangles'],'previous_dead':r['dead_after'],'previous_reason':r['dead_reason'],'route':routes[r['call']],'raw':r['reference_raw'],'new_dead':okay,'reason':why,'returns':returns,'nested_calls':calls,'skipped_opcodes':[SKIP[o] for o in skipped],**({'proof_path':path} if verbose else {})})
 return out
def summarize(rows):
 new=[r for r in rows if r['new_dead'] and not r['previous_dead']]
 return {'candidate_spans':len(rows),'old_proved':sum(r['previous_dead'] for r in rows),'proved':sum(r['new_dead'] for r in rows),'new_spans':len(new),'new_triangles':sum(r['triangles'] for r in new),'new_hardware_LOADs':sum(r['raw'] for r in new),'new_by_route':dict(collections.Counter(r['route'] for r in new)),'new_by_prior_reason':dict(collections.Counter(r['previous_reason'] for r in new)),'new_by_returns':dict(collections.Counter(r['returns'] for r in new)),'new_by_skipped_opcode':dict(collections.Counter(o for r in new for o in set(r['skipped_opcodes']))),'remaining_failures':dict(collections.Counter(r['reason'] for r in rows if not r['new_dead']))}
for name,phase,callers in [('cold_same_list','1',False),('warm_same_list','2',False),('cold_with_immutable_callers','1',True)]:
 rr=run(phase,callers,set(SKIP),True);details[name]=rr;outputs[name]=summarize(rr)
 assert summarize(run(phase,callers,set()))['proved']==sum(r['previous_dead'] for r in rr)
 outputs[name]['single_opcode_new']={n:summarize(run(phase,callers,{o}))['new_spans'] for o,n in SKIP.items()}
 outputs[name]['lost_new_if_removed']={n:outputs[name]['new_spans']-summarize(run(phase,callers,set(SKIP)-{o}))['new_spans'] for o,n in SKIP.items()}
inputs=[Path(__file__),ELF,LED,ADM,OLD/'events.json',OLD/'result.json']
result={'status':'PASS_READ_ONLY_COVERAGE_SOURCE_SAFETY_AUDIT_PENDING','allowed_skip_opcodes':{hex(o):n for o,n in SKIP.items()},'cases':outputs,'limits':['Only liveness proof advances past the listed state opcodes; actual dispatcher and GROUP span boundaries remain unchanged.','Counts establish structural dead-slot coverage, not material eligibility, transformed arithmetic support or frame-time improvement. Previously suffix-blocked paths have not reached later material admission.','Current-list warm proofs use exact immutable command words and measured warm call identities. Caller-aware results are cold-only, from the independently matched full snapshot graph; warm caller stacks are not assumed.','Mutable commands/targets/callers, all unlisted state operations, live TRI reads, loops and depth/scan limits remain rejection barriers.','Opcode canonical-read/fog safety is independently reviewed before implementation; this report does not itself authorize skipping those instructions at runtime.'],'inputs_sha256':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}}
(H/'ledger.json').write_text(json.dumps(details,indent=2)+'\n');(H/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(outputs,indent=2))
