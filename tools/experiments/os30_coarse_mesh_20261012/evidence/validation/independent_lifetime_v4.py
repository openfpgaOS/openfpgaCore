"""Independent AJ03 cached-group, raw/output and logical CPU-call lifetime audit."""
import bisect,csv,hashlib,json,struct,re
from collections import Counter,defaultdict
from pathlib import Path
from elftools.elf.elffile import ELFFile
APP_RECEIPTS={'7294008468ae3760e6aac7fda5539e5dadd255c604e3b4a6c66bc028f68e422b':'work-app-v4/evidence/app-build-v4/build-result.json'}  # Frozen F28.

import importlib.util
_group_path=Path(__file__).with_name('independent_group_v3.py')
_group_spec=importlib.util.spec_from_file_location('independent_group',_group_path)
_group=importlib.util.module_from_spec(_group_spec);_group_spec.loader.exec_module(_group)
_dl_path=Path(__file__).with_name('independent_dl_v1.py')
_dl_spec=importlib.util.spec_from_file_location('independent_dl',_dl_path)
_dl=importlib.util.module_from_spec(_dl_spec);_dl_spec.loader.exec_module(_dl)

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
 binding=json.loads((run.parent/'build.json').read_text());elf=Path(binding['elf']);elf_hash=sha(elf);assert elf_hash in APP_RECEIPTS
 app_receipt=Path(__file__).resolve().parent.parent/APP_RECEIPTS[elf_hash]
 app_build=json.loads(app_receipt.read_text());assert app_build['elf_sha256']==elf_hash and Path(app_build['elf']).resolve()==elf.resolve()
 for source,h in app_build['source_sha256'].items():assert sha(app_receipt.parents[2]/source)==h,source
 model=Path(binding['model']);prepared=json.loads((model/'prepared.json').read_text())['source_sha256']
 rtls=[model/'frozen/src/fpga/common'/n for n in ('gpu_mesh_job_front.v','geometry_async_mesh.v','geometry_mesh_packet.v','geometry_mesh_task_ctrl.v','geometry_mesh_stream.v','geometry_raw_load.v','geometry_mesh_raw_frontend.v','geometry_mesh_task_engine.v','geometry_mesh_command_fetch.v')]
 for p in rtls:assert sha(p)==prepared[str(p.relative_to(model))]
 assert 'HEADER=0,PASS_HEADER=1,COPY=2,CTX_BASE=3,CTX_DATA=4,FIELDS=5,REQUEST=6,STREAM=7,FAULT=8' in rtls[0].read_text()
 with elf.open('rb') as f:
  e=ELFFile(f);segments=[(s['p_vaddr'],s.data()) for s in e.iter_segments() if s['p_type']=='PT_LOAD'];sy=e.get_section_by_name('.symtab')
  low=sy.get_symbol_by_name('_start')[0]['st_value'];high=sy.get_symbol_by_name('__EH_FRAME_BEGIN__')[0]['st_value']
 def immutable(a,n,allow_missing=False):
  if low<=a<=high-n:
   for begin,data in segments:
    if begin<=a and a+n<=begin+len(data):return data[a-begin:a-begin+n]
  if allow_missing:return None
  assert False,'immutable ELF range missing'
 def pair(a):return struct.unpack('<II',immutable(a,8))
 def optional_pair(a):
  data=immutable(a,8,allow_missing=True)
  return struct.unpack('<II',data) if data is not None else None
 proof_records={}
 def dead(a,count,callidx):
  record=_dl.prove(optional_pair,a,count,bcalls[callidx]['dl_continuation'])
  proof_records[callidx]=record
  return record['proved']
 ev=rows(run/'async-events.tsv');words=rows(run/'worker-words.tsv');sources=rows(run/'job-sources.tsv')
 physical_stores=rows(run/'snapshot-stores.tsv');store_cycles={r['cycle']:r for r in physical_stores}
 assert len(store_cycles)==len(physical_stores),'multiple physical snapshot writes in one cycle'
 observed_store_cycles=set()
 assert ev and words and sources
 assert all(not r[k] for r in ev for k in ('aborting','protocol_error','front_fault','packet_error','fetch_error'))
 assert len({r['epoch'] for r in ev})==1,'unexpected consumer reset in measured run'
 assert all(r['front_state']!=8 for r in ev)
 assert all(not ev[-1][k] for k in ('active','pending','repair_pending','lease_granted','lease_pending','stream_busy','output_pending','group_active'))
 assert struct.pack('<%dI'%len(words),*[r['word'] for r in words])==(run/'worker-commands.bin').read_bytes()
 assert all(a['cycle']<b['cycle'] and ((a['output_pointer']+1)&4095)==b['output_pointer'] for a,b in zip(words,words[1:]))
 uploaded=uploads(run)
 calls=rows(run/'vtx-calls.tsv');bcalls=rows(baseline/'vtx-calls.tsv');callcycles=[c['cycle'] for c in calls];eventcycles=[r['cycle'] for r in ev]
 dl_binding_path=Path(binding['display_list_binding'])
 assert sha(dl_binding_path)==binding['display_list_binding_sha256']
 dl_binding=json.loads(dl_binding_path.read_text())
 assert dl_binding['elf_sha256']==elf_hash
 baseline_binding=json.loads((baseline.parent/'build.json').read_text())
 assert baseline_binding['display_list_binding_sha256']==binding['display_list_binding_sha256']
 observed_dl=_dl.audit(run,calls,dl_binding,immutable,True)
 baseline_dl=_dl.audit(baseline,bcalls,dl_binding,immutable,False)
 assert observed_dl['entry_identity']==baseline_dl['entry_identity'],'DL call/control traversal changed'
 # Bind repeated immutable lists to the independently consumed baseline
 # transform, before choosing a logical owner. The final exact raw-context
 # coverage and async/legacy disjointness checks below remain unchanged.
 owner_initial=list(struct.unpack('<80I',(fixture/'capture/run-reference/snapshot/geometry-registers.bin').read_bytes()))
 owner_state=owner_initial[64:80]+owner_initial[5:13]
 owner_mapping={**{64+i:i for i in range(16)},**{5+i:16+i for i in range(8)}}
 owner_stamps=[c['cycle'] for c in bcalls];owner_contexts={}
 for cycle,kind,reg,value in mmio(baseline):
  if kind!='GEO':continue
  if reg in owner_mapping:owner_state[owner_mapping[reg]]=value
  elif reg==2 and value&1:
   call=bisect.bisect_right(owner_stamps,cycle)-1
   assert call>=0 and call not in owner_contexts
   owner_contexts[call]=owner_state.copy()
 def owner_consumed(values):
  assert len(values)==24
  return values[:17]+(values[17:22] if values[23] else [])+values[22:24]
 def owner_match(call,context):
  return call in owner_contexts and owner_consumed(owner_contexts[call])==owner_consumed(context[:24])

 descriptors=rows(run/'group-descriptors.tsv');descriptor_groups={}
 for r in descriptors:
  g=descriptor_groups.setdefault(r['firstseq'],{'cycle':r['cycle'],'pointer':r['address'],'count':r['count'],'words':[]})
  assert (g['cycle'],g['pointer'],g['count'])==(r['cycle'],r['address'],r['count'])
  assert r['index']==len(g['words']);g['words'].append(r['word'])
 boundaries=[int(line.split()[1]) for line in (run/'boundaries.tsv').read_text().splitlines()]
 expanded=_group.expand(uploaded,calls,bcalls,descriptor_groups,
     lambda cycle:bisect.bisect_right(boundaries,cycle),immutable,pair,dead,owner_match)
 jobs=expanded['jobs'];groups=expanded['groups'];plain=expanded['plain']
 expected_ctx=expanded['expected_context_ingress'];requests=expanded['requests']
 assert groups and expanded['skipped_calls'],'GROUP path did not actually skip CPU submissions'
 groups_by_first={g['first']:g for g in groups}
 context_stores=rows(run/'context-stores.tsv')
 stored_context_events={r['cycle']:r for r in context_stores}
 assert len(stored_context_events)==len(context_stores)
 assert [(r['ctx_index'],r['ctx_data']) for r in ev if r['ctx_take']]==[(r['index'],r['word']) for r in context_stores], 'physical protected context differs from accepted ingress'
 # GROUP snapshot and actual accepted descriptor pairs are independent witnesses.
 group_takes=[r for r in ev if r['group_take']]
 assert len(group_takes)==len(groups)
 assert sum(bool(r['front_job_take']) for r in ev)==len(requests)
 for g,r in zip(groups,group_takes):
  assert r['cycle']==g['take'] and r['front_job_take'] and not r['job_take']
  assert [r['group_request_'+k] for k in ('seq','vertex','vtx_w0','flags','uofs','vofs','alpha')]==g['fields']
  assert not r['group_active'] and r['context_open'] and r['context_next']==28
 pairs=defaultdict(list);restores=defaultdict(list);protected={};seen_context_stores=set()
 for r in ev:
  if r['ctx_take']:
   physical=stored_context_events[r['cycle']];seen_context_stores.add(r['cycle'])
   assert physical['index']==r['ctx_index'] and physical['word']==r['ctx_data']
   protected[r['ctx_index']]=physical['word']
  if r['group_active']:
   g=groups_by_first[r['group_first_seq']]
   assert r['group_count']==g['count'] and r['group_index']<g['count']
   assert r['group_pointer']==g['pointer']+24*r['group_index']
   assert [protected[i] for i in range(28)]==g['context'], 'protected group context changed'
   assert [r[k] for k in ('flags_rgb','uofs','vofs','alpha')]==g['fields'][3:]
  if r['descriptor_pair_take']:
   assert r['group_active'] and r['group_state']==2
   seq=r['group_first_seq']+r['group_index'];i=r['descriptor_pair_index']
   assert i==len(pairs[seq]) and i<3
   g=groups_by_first[r['group_first_seq']]
   assert [r['descriptor_w0'],r['descriptor_w1']]==g['descriptor_words'][6*r['group_index']+2*i:6*r['group_index']+2*i+2]
   pairs[seq].append(r['cycle'])
  if r['restore_apply']:
   assert r['group_active'] and r['group_state']==5 and not r['lease_granted']
   assert not r['ctx_take'] and r['context_apply']
   seq=r['group_first_seq']+r['group_index'];i=r['restore_index']
   assert i==len(restores[seq]) and i<28
   assert r['context_apply_index']==i and r['context_apply_data']==protected[i]
   restores[seq].append(r['cycle'])
  assert bool(r['context_apply'])==bool(r['ctx_take'] or r['restore_apply'])
  if r['ctx_take']:
   assert (r['context_apply_index'],r['context_apply_data'])==(r['ctx_index'],r['ctx_data'])
 assert seen_context_stores==set(stored_context_events)
 for j in jobs:
  if j['group_first'] is not None:
   assert len(pairs[j['seq']])==3 and len(restores[j['seq']])==28
   assert pairs[j['seq']][-1]<restores[j['seq']][0]
  else:assert j['seq'] not in pairs and j['seq'] not in restores
 accepted_ctx=[(r['ctx_index'],r['ctx_data']) for r in ev if r['ctx_take']]
 assert accepted_ctx==expected_ctx,'accepted context words differ from ordered ingress'
 fields=('seq','vertex','vtx_w0','commands','control','flags','uofs','vofs','cpu_vertex','cpu_commands','alpha')
 taken=[r for r in ev if r['job_take']];assert len(taken)==len(jobs)
 for j,r in zip(jobs,taken):
  assert [r['job_'+f] for f in fields]==j['fields']
  assert r['cycle']>j['upload_end'] and not r['active'] and not r['lease_granted'] and not r['view_active'] and not r['output_pending']
  if j['group_first'] is not None:
   assert r['group_active'] and r['group_first_seq']==j['group_first'] and r['group_index']==j['member']
   assert restores[j['seq']][-1]<r['cycle']
  else:assert not r['group_active'] and r['front_job_take']
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
 snapshots={};store={};snapshot_grants=[];concurrent_legacy_starts=[]
 for r in ev[1:]:
  if prev['group_active'] and not r['group_active']:
   assert r['completed_serial']==prev['group_first_seq']+prev['group_count']-1 and not r['output_pending'], 'GROUP ended before true final completion'
  if r['front_job_take']:
   assert not r['group_active'] and r['context_open'] and r['context_next']==28
  if r['job_take']:
   assert active is None and not r['output_pending'];active=r['job_seq'];store={}
  # Reconstruct BOTH physical M10K write sources. A legacy LOAD-only task
  # must never modify either converted vertices or the survivor index list.
  index_write=r['survivor_accepted_result'] and r['survivor_result_keep']
  if index_write or r['packet_store_we']:
   assert active and active not in snapshots and not r['legacy']
   assert not(index_write and r['packet_store_we'])
   if index_write:
    address=r['survivor_live_count'];value=r['survivor_check_indices']
    assert 0<=address<64 and value<32768
   else:
    address=r['packet_store_addr'];value=r['packet_store_wdata']
    assert 64<=address<480 and value<65536 and not r['packet_snapshot_valid']
   physical=store_cycles[r['cycle']];observed_store_cycles.add(r['cycle'])
   assert physical['writer']==(2 if index_write else 1) and physical['address']==address
   assert physical['word']==value,('physical store differs from pre-edge source',active,r['cycle'])
   assert address not in store,('snapshot location written twice',active,address)
   store[address]=physical['word']
  if r['packet_snapshot_valid'] and not prev['packet_snapshot_valid']:
   assert active and r['output_pending'] and r['output_serial']==active and not r['legacy']
   assert r['packet_state']==10 and r['packet_mask']==r['used_mask'] and r['packet_count']==r['live_count']
   used=r['packet_mask'];live=r['packet_count']
   required=set(range(live))|{64+32*h+slot for h in range(13) for slot in range(32) if used>>slot&1}
   assert set(store)==required,('incomplete immutable snapshot',active,set(store)^required)
   snapshots[active]={'cycle':r['cycle'],'store':store.copy(),'used':used,'live':live,'checked':r['checked_count'],'clipped':r['clip_count'],'culled':r['cull_count'],'success':r['success']}
  if active in snapshots:
   assert not index_write and not r['packet_store_we'],'committed snapshot changed before output completion'
  # State and physical port agreement provides a separate raw-read witness.
  assert bool(r['packet_raw_read_active'])==bool(not r['packet_snapshot_valid'] and r['packet_state'] in (2,3))
  if r['snapshot_detached']:
   assert not r['packet_raw_read_active']
   if r['output_pending']:
    assert active==r['output_serial'] and active in snapshots and r['packet_snapshot_valid']
  if r['output_pending'] and r['packet_state'] in (6,13):
   assert active and r['packet_store_addr'] in store
   assert r['packet_store_rdata']==store[r['packet_store_addr']],('snapshot read differs from actual writes',active,r['cycle'])
  if r['completed_serial']!=prev['completed_serial']:
   assert active==r['completed_serial']==len(completed)+1
   assert prev['output_pending'] and prev['output_serial']==active and not r['output_pending']
   meta=snapshots[active] if active in snapshots else {'success':prev['success'],'live':prev['live_count'],'used':prev['used_mask'],'checked':prev['checked_count'],'clipped':prev['clip_count'],'culled':prev['cull_count']}
   if active in snapshots:assert prev['packet_done'] and not prev['packet_error']
   completed.append({'seq':active,'cycle':r['cycle'],'repair':active in repair_tokens,**{k:meta[k]for k in('success','live','used','checked','clipped','culled')}});active=None
  if r['repair_pending'] and not prev['repair_pending']:
   assert active and active not in repair_tokens and active not in snapshots;repair_tokens[active]=r['repair_token']
  if r['lease_granted'] and not prev['lease_granted']:
   assert not r['context_open'] and not prev['context_open'] and not r['active'] and not r['pending'] and not r['view_active'] and r['stream_raw_ready']
   if active:
    assert r['output_pending'] and r['output_serial']==active and r['snapshot_detached'] and active in snapshots
    assert snapshots[active]['cycle']<r['cycle']
    snapshot_grants.append({'seq':active,'cycle':r['cycle'],'phase':r['phase'],'snapshot_cycle':snapshots[active]['cycle'],'ticket':r['granted_ticket']})
   assert r['completed_serial']<=r['lease_target']<=len(jobs) and r['granted_ticket']==r['lease_ticket'] and r['granted_ticket']
   if r['lease_target']>r['completed_serial']:
    priority_grants.append({k:r[k] for k in ('cycle','phase','lease_target','completed_serial','granted_ticket')})
   lease_grants.append(r['granted_ticket'])
  if r['old_start']:
   assert r['lease_granted'] and not r['active'] and not r['pending']
   if r['output_pending']:
    assert active==r['output_serial'] and active in snapshots and r['snapshot_detached']
    concurrent_legacy_starts.append({'seq':active,'cycle':r['cycle'],'phase':r['phase']})
  if r['matrix_we']:
   assert not r['active'] and not r['pending'] and (r['context_apply'] or r['lease_granted'])
  if r['lease_granted']:
   assert not r['context_open'] and not r['context_next'] and (not r['active'] or r['legacy'])
   if r['output_pending']:assert r['snapshot_detached'] and active in snapshots
  if r['context_apply']:
   assert not r['lease_granted'] and not r['active'] and not r['pending'] and not r['output_pending']
   assert r['context_apply_index']==r['context_next'] and r['context_apply_index']<28
   if r['ctx_take']:
    assert (r['context_open'] if r['ctx_index'] else not r['context_open'] and not r['lease_pending'])
   else:assert r['context_open'] and r['restore_apply']
   if r['context_apply_index']<16:
    assert r['matrix_we'] and r['matrix_addr']==r['context_apply_index'] and r['matrix_data']==r['context_apply_data']
  if r['job_take']:
   assert r['context_open'] and r['context_next']==28 and not r['front_context_pending']
  prev=r
 assert observed_store_cycles==set(store_cycles),'unowned physical store write'
 assert priority_grants,'priority handoff was not exercised'
 assert snapshot_grants and concurrent_legacy_starts,'snapshot-detached raw/output concurrency was not exercised'
 assert active is None and len(completed)==len(jobs) and len(set(lease_grants))==len(lease_grants)
 produced=defaultdict(list)
 for r in words:
  if r['front_state']==7:produced[r['serial']].append(r)
 done={c['seq']:c for c in completed}
 # The same CPU upload portal also writes task-source PSRAM. Decode its
 # actual address/data writes and reject overlap with any accepted task until
 # the hardware's true completed sequence releases that source lifetime.
 source_write_count=0;source_write_live_checks=0;upload_word=None
 retained=[]
 for j in jobs:
  final=(j['group_first']+groups_by_first[j['group_first']]['count']-1) if j['group_first'] is not None else j['seq']
  for start,size in ((j['fields'][1],len(j['raw'])),(j['fields'][3],len(j['commands']))):
   retained.append((j['upload_end'],done[final]['cycle'],start,start+size,j['seq']))
 for g in groups:
  retained.append((g['upload_end'],done[g['first']+g['count']-1]['cycle'],g['pointer'],g['pointer']+24*g['count'],'GROUP'+str(g['first'])))
 for cycle,kind,reg,value in mmio(run):
  if kind!='GPU':continue
  if reg==2:upload_word=value&0x3fffff
  elif reg==15:
   assert upload_word is not None,'source upload data without address'
   address=upload_word*4;upload_word+=1;source_write_count+=1
   for submitted,complete,low,high,owner in retained:
    if submitted<cycle<complete:
     source_write_live_checks+=1
     assert not(low<=address<high),('published GROUP/JOB source overwritten before true completion',owner,cycle,address)
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
  if j['seq'] in snapshots:
   snap=snapshots[j['seq']];stored=snap['store'];i=0;draw=0;seen=set()
   def lane(slot,h):return stored[64+32*h+slot]
   def word(slot,h):return lane(slot,h)|(lane(slot,h+1)<<16)
   while i<len(values):
    op=values[i]>>24;n=values[i]&8191
    if op==0x56:
     slot=values[i+1];assert slot not in seen and snap['used']>>slot&1;seen.add(slot)
     expected=[slot,word(slot,0),word(slot,2),word(slot,4),word(slot,6),word(slot,8),lane(slot,10),word(slot,11)]
     assert values[i+1:i+9]==expected,('emitted vertex differs from frozen store',j['seq'],slot)
    else:
     assert op==0x54 and values[i+1]==stored[draw];draw+=1
    i+=n+1
   assert len(seen)==snap['used'].bit_count() and draw==snap['live']
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
  actions += [(r['cycle'],1,r['context_apply_index'],r['context_apply_data']) for r in accepted_context if r['context_apply']]
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
 assert refctx==owner_contexts
 legacyctx_actual=raw_contexts(run,calls,ev)
 legacyctx={expanded['real_to_original'][i]:ctx for i,ctx in legacyctx_actual.items()}
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
 actions += [(r['cycle'],1,r['context_apply_index'],r['context_apply_data']) for r in ev if r['context_apply']]
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
   if last_legacy_cycle>calls[j['real_call']]['cycle']:
    restored_after_legacy.append({'seq':index,'phase':j['phase'],'CPU_LOAD_call':j['call'],
     'legacy_write_cycle':last_legacy_cycle,'job_take_cycle':cycle,
     'legacy_context_differed':consumed(last_legacy_context)!=consumed(shared)})
 assert restored_after_legacy,'no older queued context restoration after a priority legacy write was exercised'
 # Source lifetime includes CPU-visible publication before every wait. Every
 # job's input copy is bound above; completion is the documented release point.
 phases={}
 for phase in (1,2):
  subset=[j for j in jobs if j['phase']==phase]
  phase_groups=[g for g in groups if jobs[g['first']-1]['phase']==phase]
  singles=sum(j['group_first'] is None for j in subset)
  phases[str(phase)]={'jobs':len(subset),'input_triangles':sum(j['triangles'] for j in subset),'repairs':sum(j['seq'] in repair_tokens for j in subset),
   'groups':len(phase_groups),'single_jobs':singles,'CPU_requests':len(phase_groups)+singles,'group_members':sum(g['count'] for g in phase_groups),
   'skipped_CPU_LOAD_calls':sum(g['count']-1 for g in phase_groups)}
 return {'display_list':{'candidate':observed_dl['counts'],'baseline':baseline_dl['counts'],'entries_exact':True,'proof_jobs':len(proof_records),'repeated_source_jobs_uniquely_owned':sum(len(j['source_candidates'])>1 for j in jobs),'caller_dependent_jobs':sum(r['external_return'] for r in proof_records.values()),'neutral_state_proof_jobs':sum(bool(r['neutral_opcodes']) for r in proof_records.values())},'physical_snapshot_writes':len(physical_stores),'snapshot_jobs':len(snapshots),'early_snapshot_grants':snapshot_grants,'legacy_raw_starts_during_old_output':concurrent_legacy_starts,'source_upload_words':source_write_count,'source_upload_words_during_live_task':source_write_live_checks,'queued_contexts_restored_after_legacy_writes':restored_after_legacy,'priority_grants_before_requested_prefix_completed':priority_grants,'groups':len(groups),'group_members':sum(g['count'] for g in groups),'skipped_CPU_LOAD_calls':expanded['skipped_calls'],'full_context_bundles':len(requests),'abi':'AJ03','status':'PASS_ORDERED_TASK_SOURCE_AND_OWNERSHIP','jobs':len(jobs),'legacy_acquires':len(acquires),'repairs':len(ends),'input_triangles':sum(j['triangles'] for j in jobs),'phases':phases,'original_LOAD_calls':len(bcalls),'actual_CPU_LOAD_calls':len(calls),'exact_raw_contexts':len(refctx),'legacy_raw_contexts':len(legacyctx),
  'checks':['Committed snapshot reconstructed from both M10K write sources, exact read/packet payload checks, no raw reads or snapshot writes after detachment, and real legacy raw work concurrent with old output while completed sequence remains unchanged.','Every actual CPU LOAD plus explicit contiguous GROUP members reproduces all original calls; physical descriptor snapshot equals every accepted descriptor pair, and immutable VTX/TRI ELF bytes equal member-source snapshots.','Independent per-member dead-slot proof, complete atomic context before every request, actual protected context RAM writes and restored values, and all raw inputs equal the same-ELF reference by logical LOAD identity.','Every ordinary input word forwarded exactly; every generated packet stored within owning task; output bytes bind actual FIFO handshakes.','AJ03 priority grants before requested-prefix completion; indivisible full-context/request and restored member ownership; legacy lease acquire/grant/release and repair token ACK/DATA/END checks; matching complete sequences and no outstanding ownership at final boundary.'],
  'source_sha256':{str(p):sha(p) for p in rtls+[elf,app_receipt,Path(__file__).resolve(),_group_path,_dl_path,dl_binding_path]}}
