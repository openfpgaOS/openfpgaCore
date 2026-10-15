"""MTF2 actual raw owner, variable-length immutable packet queue and ordered GPU output audit."""
import csv,difflib,hashlib,json,struct,re
from collections import Counter,defaultdict,deque
from pathlib import Path

def rows(path):
    with path.open() as f:return [{k:int(v,0) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]

def check(run,baseline,fixture,ingress,output):
    binding=json.loads((run.parent/'build.json').read_text());model=Path(binding['model'])
    prepared=json.loads((model/'prepared.json').read_text())['source_sha256']
    packet=model/'frozen/src/fpga/common/geometry_mesh_packet.v'
    queue_rtl=model/'frozen/src/fpga/common/geometry_mesh_queue.v'
    for path in (packet,queue_rtl):assert hashlib.sha256(path.read_bytes()).hexdigest()==prepared[str(path.relative_to(model))]
    assert re.search(r'\bVALIDATED\s*=\s*10\b',packet.read_text())
    assert re.search(r'EMPTY=0,HEADER_PRIME=1,READY=2,DRAIN=3,FAULT=4,HEADER_CAPTURE=5',queue_rtl.read_text())
    e=rows(run/'mesh-events.tsv');w=rows(run/'worker-words.tsv');copies=rows(run/'mesh-copy-words.tsv');overlap=rows(run/'mesh-overlap.tsv')
    assert e and w and copies and overlap
    assert all(not r[k] for r in e for k in ('protocol_error','packet_error','marker_error'))
    assert not e[0]['owner'] and not e[-1]['owner'] and not e[0]['queue_busy'] and not e[-1]['queue_busy']
    raw=(run/'worker-commands.bin').read_bytes()
    assert list(struct.unpack('<%dI'%(len(raw)//4),raw))==[r['word'] for r in w]
    assert all(((a['output_pointer']+1)&4095)==b['output_pointer'] for a,b in zip(w,w[1:]))
    assert all(a['cycle']<b['cycle'] for a,b in zip(w,w[1:]))
    markers=[(i+1,*d) for i,f in enumerate(ingress) for op,d in f['commands'] if op==0x62]
    assert all(len(d)==4 for f in ingress for op,d in f['commands'] if op==0x62)
    task=None;tasks=[];task_by_serial={};fill=None;queued=deque();queues=[];observed=[];previous=None
    max_pending=0;max_used=0;commits=0
    for r in e:
        assert r['queue_state'] in (0,1,2,3,5), 'private queue fault'
        used=(r['committed_ptr']-r['read_ptr'])&1023
        reserved=(r['reserved_end']-r['read_ptr'])&1023 if r['fill_active'] else used
        assert 0<=used<=reserved<=512, 'FIFO physical credits overcommitted'
        max_used=max(max_used,reserved)
        if previous:
            assert ((r['read_ptr']-previous['read_ptr'])&1023)<=1, 'credits skipped an unread word'
        if r['owner'] and (previous is None or not previous['owner']):
            task={'legacy':r['legacy'],'serial':r['descriptor_serial'],'phase':r['phase'],'begin':r['cycle'],
                  'validated':False,'armed':False,'copied':False,'fallback':False,'raw_success':False,'preflight':False}
            if not task['legacy']:
                assert task['serial'] not in task_by_serial, 'raw token reused'
                task_by_serial[task['serial']]=task
        if r['owner']:
            assert task and r['legacy']==task['legacy'] and r['descriptor_serial']==task['serial']
            if not r['legacy'] and not r['pending']:assert r['serial']==task['serial']
            task['raw_success'] |= bool(r['raw_success']);task['preflight'] |= bool(r['preflight']);task['fallback'] |= bool(r['fallback'])
            if not r['legacy'] and r['preflight']:
                assert r['raw_seen'] and r['raw_success'] and r['live']+r['clip']+r['cull']==r['checked']
                assert r['used']&~r['owned']==0
                task['words']=r['used'].bit_count()*9+r['live']*2
            if r['packet_state']==10:task['validated']=True
            if r['arm_pending']:
                assert not r['legacy'] and task['validated']
                task['armed']=True
            if r['copy_start']:
                assert task['armed'] and r['fill_active'] and r['fill_serial']==r['serial']
            if r['copy_done']:
                assert task['armed'] and task['validated'] and r['fill_active']
                assert r['fill_serial']==r['serial'] and r['raw_state']==0 and not r['cull_owner']
                assert r['queue_writes']==r['fill_count'] and r['write_ptr']==r['reserved_end']
                task['copied']=True;task['copy_done_cycle']=r['cycle']
        elif previous is not None and previous['owner']:
            assert task
            if task['armed']:assert task['copied'], 'raw bank released before final packet copy commit'
            task['end']=r['cycle'];tasks.append(task);task=None
        if r['fill_active'] and (previous is None or not previous['fill_active']):
            assert fill is None and previous and previous['queue_reserve']
            source=task_by_serial.get(r['fill_serial']);assert source and source['validated'] and source['armed']
            assert r['queue_writes']==0 and r['write_ptr']==r['committed_ptr']
            fill={'serial':r['fill_serial'],'phase':r['phase'],'begin':r['cycle'],
                  'context':(r['fill_flags'],r['fill_uofs'],r['fill_vofs']),
                  'words':source['words'],'marked':False,'committed':False,
                  'start_ptr':r['write_ptr'],'end_ptr':r['reserved_end']}
            assert 0<fill['words']<=416 and r['fill_count']==fill['words']
            assert ((fill['end_ptr']-fill['start_ptr'])&1023)==fill['words']+5
            queued.append(fill);max_pending=max(max_pending,len(queued))
        if r['fill_active']:
            assert fill and r['fill_serial']==fill['serial']
            assert (r['fill_flags'],r['fill_uofs'],r['fill_vofs'])==fill['context'], 'fill metadata changed'
            assert r['fill_count']==fill['words'] and 0<=r['queue_writes']<=fill['words']
            assert r['reserved_end']==fill['end_ptr'] and r['committed_ptr']==fill['start_ptr']
            assert ((r['write_ptr']-fill['start_ptr'])&1023)==r['fill_header']+r['queue_writes']
        elif previous is not None and previous['fill_active']:
            assert fill and previous['copy_done'] and previous['queue_writes']==fill['words']
            assert r['committed_ptr']==fill['end_ptr'], 'commit did not publish exact complete record'
            fill['committed']=True;fill['commit_cycle']=r['cycle'];commits+=1;fill=None
        if r['queue_state'] in (2,3):
            assert queued and queued[0]['committed'], 'head exposed before record commit'
            head=queued[0]
            assert r['queue_serial']==head['serial']
            assert (r['queue_flags'],r['queue_uofs'],r['queue_vofs'])==head['context'], 'head context changed'
            assert r['queue_count']==head['words'] and 0<=r['queue_reads']<head['words']
            if r['marker_valid'] and r['marker_ready']:
                assert r['queue_state']==2 and not head['marked']
                assert (r['marker_serial'],r['marker_flags'],r['marker_uofs'],r['marker_vofs'])==(head['serial'],*head['context'])
                head['marked']=True;head['marker_cycle']=r['cycle']
                observed.append((r['phase'],r['marker_serial'],r['marker_flags'],r['marker_uofs'],r['marker_vofs']))
            if r['queue_state']==3:assert head['marked']
        if r['packet_done'] and (previous is None or not previous['packet_done']):
            assert queued and previous and previous['queue_state']==3
            head=queued.popleft();assert head['marked']
            assert r['queue_reads']==r['queue_count']==head['words'], 'packet released before last handshake'
            assert r['read_ptr']==head['end_ptr'], 'reclaimed pointer differs from record end'
            head['end']=r['cycle'];queues.append(head)
        previous=r
    assert task is None and fill is None and not queued and observed==markers
    assert len({q['serial'] for q in queues})==len(queues)==len(markers)==commits
    # Compare actual committed private BRAM words against subsequent actual
    # consumer-bridge BRAM words after raw storage has become reusable.
    copied=defaultdict(list)
    for r in copies:
        assert r['index']==len(copied[r['serial']]) and 0<=r['index']<416
        copied[r['serial']].append(r['word'])
    delivered=defaultdict(list);pos=0
    for r in w:
        while pos+1<len(e) and e[pos+1]['cycle']<=r['cycle']:pos+=1
        state=e[pos];assert state['bridge_state'] in (1,2,5)
        if state['bridge_state']==5:
            assert state['queue_state']==3
            delivered[state['queue_serial']].append(r['word'])
    assert dict(copied)==dict(delivered), 'private copied packet differs from consumed GPU stream'
    for q in queues:
        assert len(copied[q['serial']])==q['words']
        t=task_by_serial[q['serial']]
        assert t['copied'] and t['copy_done_cycle']<q['marker_cycle'], 'marker consumed uncommitted packet'
    writes=[]
    for line in (run/'mmio.tsv').read_text().splitlines():
        c,k,a,v=line.split()
        if k=='GEO':writes.append((int(c),int(a)//4,int(v)))
    controls=Counter(v for _,a,v in writes if a==0x26);mesh_tasks=[t for t in tasks if not t['legacy']]
    assert set(controls)<=set((1,2,8,16)) and controls[1]==len(mesh_tasks)
    assert controls[16]==sum(t['armed'] for t in mesh_tasks)==len(queues)
    assert controls[8]==sum(t['validated'] for t in mesh_tasks)
    assert controls[2]==sum(not t['armed'] for t in mesh_tasks)
    # Frozen MTF2 appv4 source-cache/LOAD PCs independently rebound to its disassembly.
    binding=json.loads((run.parent/'build.json').read_text());elf=Path(binding['elf'])
    assert hashlib.sha256(elf.read_bytes()).hexdigest()=='883e2b4cfcd105bddc032d89c4379cd72702621cd1923cacbd0b6596ad02ce9e'
    def raw_jobs(path):
        state={r:0 for r in list(range(4,13))+list(range(64,80))};jobs=[]
        for line in (path/'mmio.tsv').read_text().splitlines():
            cycle,kind,addr,value=line.split();reg=int(addr)//4;value=int(value)
            if kind!='GEO':continue
            if reg in state:state[reg]=value
            if (reg==2 and value&1) or (reg==0x26 and value==1):
                # Disabled lighting never enters phases5..11 and retains raw
                # source RGB. Compare every context word that is consumed.
                effective=tuple((reg,value) for reg,value in state.items()
                                if state[12] or reg not in range(6,11))
                jobs.append({'cycle':int(cycle),'shape':state[4],'context':effective,'full':state.copy()})
        return jobs
    a,b=raw_jobs(baseline),raw_jobs(run);omitted=[];matched=[]
    for tag,i,j,k,l in difflib.SequenceMatcher(None,[x['context'] for x in a],[x['context'] for x in b],autojunk=False).get_opcodes():
        if tag=='equal':matched.extend(zip(range(i,j),range(k,l)));continue
        assert tag=='delete' and j==i+1 and a[i]['shape'] in (4,12), 'unattributed raw task/context mismatch'
        omitted.append(i)
    assert len(a)==652 and len(b)==642 and len(omitted)==10
    assert Counter(a[i]['shape'] for i in omitted)==Counter({4:8,12:2})
    disabled_light_differences=[]
    for ai,bi in matched:
        different=[reg for reg in a[ai]['full'] if a[ai]['full'][reg]!=b[bi]['full'][reg]]
        if different:
            assert a[ai]['full'][12]==b[bi]['full'][12]==0 and set(different)<=set(range(6,11))
            disabled_light_differences.append({'reference_job':ai,'candidate_job':bi,'unused_registers':different})
    # Raw context and START writes must happen outside producer ownership.
    pos=0
    for line in (run/'mmio.tsv').read_text().splitlines():
        cycle,kind,addr,value=line.split();cycle=int(cycle);reg=int(addr)//4;value=int(value)
        if kind!='GEO':continue
        while pos+1<len(e) and e[pos+1]['cycle']<=cycle:pos+=1
        if reg in range(3,13) or reg in range(64,80):
            assert not e[pos]['owner'] and not e[pos]['pending'], 'raw context write while producer owned'
    profile_counts={}
    for phase in (1,2):
        def profile(path):
            with path.open() as f:return {int(r['pc']):int(r['retired']) for r in csv.DictReader(f)}
        candidate=profile(run/f'pc-phase-{phase}.csv');control=profile(baseline/f'pc-phase-{phase}.csv')
        keys={'source_calls':0x104dca32,'vertex_success':0x104dca3a,'last_command_probe':0x104dcb78,
              'source_false_return':0x104dcb7c,'source_fallback_block':0x104cece8,'command_allocate_path':0x104dcc98}
        got={k:candidate.get(pc,0) for k,pc in keys.items()}
        assert got['source_calls']==got['vertex_success']==319
        assert got['last_command_probe']==got['source_false_return']==got['source_fallback_block']==5
        assert all(control.get(pc,0)==0 for pc in keys.values())
        if phase==2:assert got['command_allocate_path']==0
        profile_counts[phase]=got
    reference_bounds=[list(map(int,line.split())) for line in (baseline/'boundaries.tsv').read_text().splitlines()]
    for phase in (1,2):
        skipped=[a[i] for i in omitted if reference_bounds[phase-1][1]<=a[i]['cycle']<reference_bounds[phase][1]]
        assert len(skipped)==profile_counts[phase]['source_fallback_block']==5
        assert Counter(t['shape'] for t in skipped)==Counter({4:4,12:1})
    boundaries=[list(map(int,s.split())) for s in (run/'boundaries.tsv').read_text().splitlines()]
    phases={}
    for phase in (1,2):
        duration=boundaries[phase][1]-boundaries[phase-1][1]
        records=[r for r in overlap if r['phase']==phase];assert sum(r['cycles'] for r in records)==duration
        def count(mask):return sum(r['cycles'] for r in records if r['mask']&mask==mask)
        phases[phase]={'cycles':duration,'raw_active_cycles':count(8),'raster_active_cycles':count(128),
            'raw_with_raster':count(8|128),'cull_FMA_with_raster':count(16|128),
            'CPU_retire_with_raw_and_raster':count(1|8|128),
            'raw_with_older_queued_packet':count(8|1024),'raw_owner_with_older_queued_packet':count(1024),
            'private_copy_words':count(256),'queued_consume_words':count(64),'arm_pending_cycles':count(2048)}
    return {'abi':'MTF2','status':'PASS','mesh_tasks':len(mesh_tasks),'legacy_loads':len(tasks)-len(mesh_tasks),
        'packets':len(queues),'copied_words':len(copies),'ordinary_output_words':len(w),
        'discarded_tasks':controls[2],'packet_payloads_immutable_through_raw_reuse':True,
        'no_GPU_output_before_copy_commit_and_exact_marker':True,'raw_release_only_after_private_copy':True,
        'queue_release_only_after_final_GPU_handshake':True,'maximum_simultaneous_reserved_packets':max_pending,'maximum_reserved_words':max_used,
        'raw_context':{'effective_started_task_contexts_exact':True,'raw_context_writes_only_when_unowned':True,'omitted_raw_job_indices':omitted,'omitted_shapes':[a[i]['shape'] for i in omitted],'unused_disabled_lighting_differences':disabled_light_differences,
           'source_cache_fallback_evidence':profile_counts},
        'packets_still_queued_after_raw_owner_released':sum(task_by_serial[q['serial']]['end']<q['end'] for q in queues),
        'overlap':phases,'overlap_scope':'Actual simultaneous RTL activity, not a prediction of removable cycles or sustained hardware FPS.'}
