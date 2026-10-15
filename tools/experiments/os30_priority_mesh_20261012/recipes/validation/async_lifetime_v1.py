"""Independent AJ02 ingress/context/source/lease/repair/output lifetime audit."""
import bisect,csv,hashlib,json,struct,re
from collections import Counter,defaultdict
from pathlib import Path
from elftools.elf.elffile import ELFFile
SHA_ELF='abcdf9bfb5729edb5fa96489c543b203cfba8f721b0a5b3043166475183ec8bd'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p):
 with Path(p).open() as f:return [{k:int(v,0) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]
def mmio(p):
 return [(int(c),k,int(a)//4,int(v)) for c,k,a,v in (x.split() for x in (p/'mmio.tsv').read_text().splitlines())]
def uploads(p):
 data=[(c,v) for c,k,a,v in mmio(p) if k=='GPU' and a==1];out=[];i=0
 while i<len(data):
  n=data[i][1]&8191;assert i+n<len(data),'truncated CPU command'
  out.append({'op':data[i][1]>>24,'header':data[i][1],'data':[v for _,v in data[i+1:i+n+1]],'cycle':data[i][0],'end':data[i+n][0]})
  i+=n+1
 assert struct.pack('<%dI'%len(data),*[v for _,v in data])==(p/'commands.bin').read_bytes()
 return out
def check(run,baseline,fixture,ingress,output):
 run,baseline,fixture=map(Path,(run,baseline,fixture))
 binding=json.loads((run.parent/'build.json').read_text());elf=Path(binding['elf']);assert sha(elf)==SHA_ELF
 model=Path(binding['model']);prepared=json.loads((model/'prepared.json').read_text())['source_sha256']
 rtls=[model/'frozen/src/fpga/common'/n for n in ('gpu_mesh_job_front.v','geometry_async_mesh.v')]
 for p in rtls:assert sha(p)==prepared[str(p.relative_to(model))]
 assert 'HEADER=0,PASS_HEADER=1,COPY=2,CTX_BASE=3,CTX_DATA=4,FIELDS=5,REQUEST=6,STREAM=7,FAULT=8' in rtls[0].read_text()
 with elf.open('rb') as f:
  e=ELFFile(f);segments=[(s['p_vaddr'],s.data()) for s in e.iter_segments() if s['p_type']=='PT_LOAD'];sy=e.get_section_by_name('.symtab')
  low=sy.get_symbol_by_name('_start')[0]['st_value'];high=sy.get_symbol_by_name('__EH_FRAME_BEGIN__')[0]['st_value']
 def immutable(a,n):
  assert low<=a<=high-n
  for begin,data in segments:
   if begin<=a and a+n<=begin+len(data):return data[a-begin:a-begin+n]
  assert False,'immutable ELF range missing'
 def pair(a):return struct.unpack('<II',immutable(a,8))
 def dead(a,count):
  alive=set(range(count))
  for _ in range(256):
   w,v=pair(a);op=w>>24
   if op==1:
    n=w>>12&255;end=w>>1&127;assert n and n<=end<=32
    alive.difference_update(range(end-n,end))
    if not alive:return True
   elif op in (5,6):
    for word in ([w] if op==5 else [w,v]):
     idx=[(word>>s&255)//2 for s in (16,8,0)]
     if any(x>=32 or x in alive for x in idx):return False
   else:return False
   a+=8
  return False
 ev=rows(run/'async-events.tsv');words=rows(run/'worker-words.tsv');sources=rows(run/'job-sources.tsv')
 assert ev and words and sources
 assert all(not r[k] for r in ev for k in ('aborting','protocol_error','front_fault','packet_error','fetch_error'))
 assert len({r['epoch'] for r in ev})==1,'unexpected consumer reset in measured run'
 assert all(r['front_state']!=8 for r in ev)
 assert all(not ev[-1][k] for k in ('active','pending','repair_pending','lease_granted','lease_pending','stream_busy'))
 assert struct.pack('<%dI'%len(words),*[r['word'] for r in words])==(run/'worker-commands.bin').read_bytes()
 assert all(a['cycle']<b['cycle'] and ((a['output_pointer']+1)&4095)==b['output_pointer'] for a,b in zip(words,words[1:]))
 uploaded=uploads(run);plain=[];expected_ctx=[];jobs=[];context=[None]*28
 calls=rows(run/'vtx-calls.tsv');bcalls=rows(baseline/'vtx-calls.tsv');callcycles=[c['cycle'] for c in calls];eventcycles=[r['cycle'] for r in ev]
 keys=('phase','a0','a1','a2','s1','ra')
 assert [[c[k] for k in keys] for c in calls]==[[c[k] for k in keys] for c in bcalls],'original LOAD call identity/order changed'
 context_pending=False
 for command in uploaded:
  op,d=command['op'],command['data']
  if op==0x63:
   assert not context_pending and command['header']==0x6300001d and len(d)==29 and d[0]==0,'AJ02 requires one complete context packet'
   context_pending=True
   for i,v in enumerate(d[1:],d[0]):context[i]=v;expected_ctx.append((i,v))
  elif op==0x64:
   assert context_pending and command['header']==0x6400000b and len(d)==11 and None not in context
   context_pending=False
   seq,source,vtx,cmdsrc,config,flags,u,v,cpusrc,cpucmd,alpha=d
   assert seq==len(jobs)+1 and config>>10==0 and not(config&128) and 0<(config&127)<=64
   assert flags>>19==0 and alpha<=255 and context[23] in (0,1)
   if flags&(1<<16):assert u<=524280 and v<=524280 and not((u|v)&7)
   count=(vtx>>12)&255;assert 0<count<=32 and ((vtx>>1)&127)==count and vtx>>24==1
   # GPU MMIO uploads can lag CPU submission through SDK DMA staging.
   # Match exact immutable call identity within its drained frame, not the
   # nearest CPU call at the later upload handshake.
   phase=ev[bisect.bisect_right(eventcycles,command['end'])-1]['phase']
   matches=[i for i,c in enumerate(calls) if c['phase']==phase and (c['a0'],c['a1'],c['a2'],c['s1']+8)==(count,0,cpusrc,cpucmd)]
   assert len(matches)==1,('ambiguous original LOAD owner',seq,matches)
   callidx=matches[0];call=calls[callidx];assert call['cycle']<command['cycle']
   assert pair(cpucmd-8)[0]==vtx
   raw=immutable(cpusrc,count*24);commands=immutable(cpucmd,(config&127)*8);tris=0
   for a,b in struct.iter_unpack('<II',commands):
    assert a>>24 in (5,6)
    for w in ([a] if a>>24==5 else [a,b]):
     assert all(((w>>s)&255)//2<count for s in (16,8,0));tris+=1
   assert 0<tris<=64 and pair(cpucmd+(config&127)*8)[0]>>24 not in (5,6)
   assert dead(cpucmd+(config&127)*8,count),'queued canonical slots escape proven suffix'
   jobs.append({'seq':seq,'fields':d,'context':context.copy(),'call':callidx,'phase':call['phase'],'upload_end':command['end'],'raw':raw,'commands':commands,'triangles':tris})
  else:
   assert not context_pending,'ordinary command interrupted atomic context/job bundle'
   plain.extend([command['header'],*d])
 assert not context_pending
 assert jobs and len({j['call'] for j in jobs})==len(jobs)
 accepted_ctx=[(r['ctx_index'],r['ctx_data']) for r in ev if r['ctx_take']]
 assert accepted_ctx==expected_ctx,'accepted context words differ from ordered ingress'
 fields=('seq','vertex','vtx_w0','commands','control','flags','uofs','vofs','cpu_vertex','cpu_commands','alpha')
 taken=[r for r in ev if r['job_take']];assert len(taken)==len(jobs)
 for j,r in zip(jobs,taken):
  assert [r['job_'+f] for f in fields]==j['fields']
  assert r['cycle']>j['upload_end'] and not r['active'] and not r['lease_granted'] and not r['view_active']
  j['take']=r['cycle']
 assert [r['word'] for r in words if r['front_state'] in (1,2)]==plain,'ordinary ingress changed or reordered'
 assert all(r['front_state'] in (1,2,7) for r in words)
 captured=defaultdict(list)
 for r in sources:captured[(r['serial'],r['kind'])].append(r)
 assert set(captured)=={(j['seq'],kind) for j in jobs for kind in (0,1)}
 for j in jobs:
  for kind,expected,address in ((0,j['raw'],j['fields'][1]),(1,j['commands'],j['fields'][3])):
   batch=captured[j['seq'],kind]
   assert [r['index'] for r in batch]==list(range(len(expected)//4))
   assert all(r['cycle']==j['take'] and r['address']==address for r in batch)
   assert struct.pack('<%dI'%len(batch),*[r['word'] for r in batch])==expected,'PSRAM task source differs from immutable CPU bytes'
 # Every completed sequence belongs to exactly one accepted job. Capture
 # transitions separately from compressed rows: levels can persist.
 prev=ev[0];active=None;completed=[];repair_tokens={};lease_grants=[];priority_grants=[]
 for r in ev[1:]:
  if r['job_take']:
   assert active is None;active=r['job_seq']
  if r['completed_serial']!=prev['completed_serial']:
   assert active==r['completed_serial']==len(completed)+1
   completed.append({'seq':active,'cycle':r['cycle'],'success':prev['success'],'repair':active in repair_tokens,'live':prev['live_count'],'used':prev['used_mask'],'checked':prev['checked_count'],'clipped':prev['clip_count'],'culled':prev['cull_count']});active=None
  if r['repair_pending'] and not prev['repair_pending']:
   assert active and active not in repair_tokens;repair_tokens[active]=r['repair_token']
  if r['lease_granted'] and not prev['lease_granted']:
   assert not active and not r['context_open'] and not prev['context_open'] and not r['pending'] and not r['view_active'] and r['stream_ready']
   assert r['completed_serial']<=r['lease_target']<=len(jobs) and r['granted_ticket']==r['lease_ticket'] and r['granted_ticket']
   if r['lease_target']>r['completed_serial']:
    priority_grants.append({k:r[k] for k in ('cycle','phase','lease_target','completed_serial','granted_ticket')})
   lease_grants.append(r['granted_ticket'])
  if r['old_start']:assert r['lease_granted'] and not r['active'] and not r['pending']
  if r['matrix_we']:
   assert not r['active'] and not r['pending'] and (r['ctx_take'] or r['lease_granted'])
  if r['lease_granted']:
   assert not r['context_open'] and not r['context_next'] and (not r['active'] or r['legacy'])
  if r['ctx_take']:
   assert not r['lease_granted'] and not r['active'] and not r['pending']
   assert r['ctx_index']==r['context_next'] and r['ctx_index']<28
   assert (r['context_open'] if r['ctx_index'] else not r['context_open'] and not r['lease_pending'])
   if r['ctx_index']<16:
    assert r['matrix_we'] and r['matrix_addr']==r['ctx_index'] and r['matrix_data']==r['ctx_data']
  if r['job_take']:
   assert r['context_open'] and r['context_next']==28 and not r['front_context_pending']
  prev=r
 assert priority_grants,'priority handoff was not exercised'
 assert active is None and len(completed)==len(jobs) and len(set(lease_grants))==len(lease_grants)
 produced=defaultdict(list)
 for r in words:
  if r['front_state']==7:produced[r['serial']].append(r)
 done={c['seq']:c for c in completed}
 # The same CPU upload portal also writes task-source PSRAM. Decode its
 # actual address/data writes and reject overlap with any accepted task until
 # the hardware's true completed sequence releases that source lifetime.
 source_write_count=0;source_write_live_checks=0;upload_word=None
 take_cycles=[j['take'] for j in jobs]
 for cycle,kind,reg,value in mmio(run):
  if kind!='GPU':continue
  if reg==2:upload_word=value&0x3fffff
  elif reg==15:
   assert upload_word is not None,'source upload data without address'
   address=upload_word*4;upload_word+=1;source_write_count+=1
   i=bisect.bisect_right(take_cycles,cycle)-1
   if i>=0 and cycle<done[jobs[i]['seq']]['cycle']:
    j=jobs[i];source_write_live_checks+=1
    for start,size in ((j['fields'][1],len(j['raw'])),(j['fields'][3],len(j['commands']))):
     assert not(start<=address<start+size),('in-flight task source overwritten',j['seq'],cycle,address)
 for j in jobs:
  c=done[j['seq']];packet=produced[j['seq']]
  assert all(j['take']<r['cycle']<c['cycle'] for r in packet)
  if not c['repair']:
   assert c['checked']==j['triangles'] and c['clipped']+c['culled']+c['live']==c['checked']
   assert len(packet)==c['used'].bit_count()*9+c['live']*2
  values=[r['word'] for r in packet];i=0
  while i<len(values):
   h=values[i];n=h&8191;assert h>>24 in (0x56,0x54,0x4e);assert i+n<len(values);i+=n+1
  assert i==len(values)
 # MMIO repair ACK/DATA/END and legacy acquire/release are correlated with
 # pre-edge ownership, not inferred from final frame equivalence.
 cycles=[r['cycle'] for r in ev];acks={};ends=[];repair_data=[];acquires=[];releases=[]
 for cycle,kind,reg,value in mmio(run):
  if kind!='GEO':continue
  r=ev[bisect.bisect_right(cycles,cycle)-1]
  if reg==0x29:
   assert r['repair_pending'] and value==r['repair_token'];acks[value]=cycle
  elif reg==0x28:
   assert r['repair_pending'] and r['repair_ack']==r['repair_token'] and r['repair_token'] in acks
   repair_data.append((cycle,value,r['descriptor_serial']))
  elif reg==0x2a:
   assert value==1 and r['repair_pending'] and r['repair_ack']==r['repair_token'];ends.append(r['repair_token'])
  elif reg==0x2d:
   if value==1:
    assert not r['lease_granted'] and not r['lease_pending'];acquires.append(r['lease_ticket'])
   else:
    assert value==2 and r['lease_granted'] and not r['active'] and not r['pending'];releases.append(r['granted_ticket'])
 assert sorted(acks)==sorted(ends)==sorted(repair_tokens.values())
 assert acquires==lease_grants==releases
 assert [(r['cycle'],r['word'],r['serial']) for r in words if r['repair']]==repair_data
 # Compare all consumed raw transform inputs at exact CPU LOAD call identity.
 # Async and legacy share context registers; interleave their actual accepted
 # CONTEXT words with legacy MMIO instead of treating either as an isolated bank.
 initial=list(struct.unpack('<80I',(fixture/'capture/run-reference/snapshot/geometry-registers.bin').read_bytes()))
 mapping={**{64+i:i for i in range(16)},**{5+i:16+i for i in range(8)}}
 def raw_contexts(path,cs,accepted_context=()):
  state=[initial[64+i] for i in range(16)]+initial[5:13]
  actions=[(cycle,0,reg,value) for cycle,kind,reg,value in mmio(path) if kind=='GEO']
  actions += [(r['cycle'],1,r['ctx_index'],r['ctx_data']) for r in accepted_context if r['ctx_take']]
  stamps=[c['cycle'] for c in cs];found={}
  for cycle,kind,index,value in sorted(actions):
   if kind:
    if index<24:state[index]=value
   elif index in mapping:state[mapping[index]]=value
   elif index==2 and value&1:
    call=bisect.bisect_right(stamps,cycle)-1;assert call>=0 and call not in found
    found[call]=state.copy()
  return found
 refctx=raw_contexts(baseline,bcalls)
 legacyctx=raw_contexts(run,calls,ev)
 jobctx={j['call']:j['context'][:24] for j in jobs}
 assert not(set(jobctx)&set(legacyctx))
 assert set(refctx)==set(jobctx)|set(legacyctx),'hardware LOAD omission/addition requires exact CPU-call provenance review'
 def consumed(values):
  assert len(values)==24
  return values[:17]+(values[17:22] if values[23] else [])+values[22:24]
 for call,values in {**legacyctx,**jobctx}.items():
  assert consumed(values)==consumed(refctx[call]),('raw transform context mismatch at CPU LOAD',call)
 # Independently reconstruct the actually shared transform bank at each
 # accepted JOB. Priority may let CPU legacy writes overtake an older upload;
 # the next full context must restore it before the raw engine can consume it.
 shared=[initial[64+i] for i in range(16)]+initial[5:13]
 actions=[(cycle,0,reg,value) for cycle,kind,reg,value in mmio(run) if kind=='GEO' and reg in mapping]
 actions += [(r['cycle'],1,r['ctx_index'],r['ctx_data']) for r in ev if r['ctx_take']]
 actions += [(j['take'],2,j['seq'],0) for j in jobs]
 last_legacy_cycle=-1;last_legacy_context=None;restored_after_legacy=[]
 for cycle,kind,index,value in sorted(actions):
  if kind==0:
   shared[mapping[index]]=value;last_legacy_cycle=cycle;last_legacy_context=shared.copy()
  elif kind==1:
   if index<24:shared[index]=value
  else:
   j=jobs[index-1]
   assert consumed(shared)==consumed(j['context'][:24]),('accepted JOB shared context corrupted',index)
   if last_legacy_cycle>calls[j['call']]['cycle']:
    restored_after_legacy.append({'seq':index,'phase':j['phase'],'CPU_LOAD_call':j['call'],
     'legacy_write_cycle':last_legacy_cycle,'job_take_cycle':cycle,
     'legacy_context_differed':consumed(last_legacy_context)!=consumed(shared)})
 assert restored_after_legacy,'no older queued context restoration after a priority legacy write was exercised'
 # Source lifetime includes CPU-visible publication before every wait. Every
 # job's input copy is bound above; completion is the documented release point.
 phases={}
 for phase in (1,2):
  subset=[j for j in jobs if j['phase']==phase]
  phases[str(phase)]={'jobs':len(subset),'input_triangles':sum(j['triangles'] for j in subset),'repairs':sum(j['seq'] in repair_tokens for j in subset)}
 return {'source_upload_words':source_write_count,'source_upload_words_during_live_task':source_write_live_checks,'queued_contexts_restored_after_legacy_writes':restored_after_legacy,'priority_grants_before_requested_prefix_completed':priority_grants,'full_context_bundles':len(jobs),'abi':'AJ02','status':'PASS_ORDERED_TASK_SOURCE_AND_OWNERSHIP','jobs':len(jobs),'legacy_acquires':len(acquires),'repairs':len(ends),'input_triangles':sum(j['triangles'] for j in jobs),'phases':phases,'original_LOAD_calls':len(calls),'exact_raw_contexts':len(refctx),'legacy_raw_contexts':len(legacyctx),
  'checks':['Exact original LOAD call identity/order, upload-to-call binding, immutable VTX/TRI ELF bytes versus accepted PSRAM source snapshots.','Independent dead-slot suffix traversal, scoped shader flag/UV constraints, full context ingress-to-handshake identity; all consumed raw transform inputs equal same-ELF baseline at exact CPU LOAD call.','Every ordinary input word forwarded exactly; every generated packet stored within owning task; output bytes bind actual FIFO handshakes.','AJ02 priority grants before requested-prefix completion; indivisible full-context/job ownership; legacy lease acquire/grant/release and repair token ACK/DATA/END checks; matching complete sequences and no outstanding ownership at final boundary.'],
  'source_sha256':{str(p):sha(p) for p in rtls+[elf,Path(__file__).resolve()]}}
