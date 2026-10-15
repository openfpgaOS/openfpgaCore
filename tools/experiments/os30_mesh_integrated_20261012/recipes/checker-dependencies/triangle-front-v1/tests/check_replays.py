#!/usr/bin/env python3
"""Independent fresh-F13 replay checks and reordered vertex-import semantics."""
import argparse
from collections import Counter
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import struct

I=Path(__file__).resolve().parents[1]
N=I/'fixture-v1'
COLOR=153600
OLD=I.parent/'gpu-bottleneck-v1/review/check_runs.py'
spec=importlib.util.spec_from_file_location('prior_review',OLD)
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
CFG=dict(line.split('=',1) for line in (N/'input.cfg').read_text().splitlines() if '=' in line)
number=lambda key:int(CFG[key],0)


def commands(path,golden=False):
    raw=path.read_bytes();assert len(raw)%4==0
    words=struct.unpack('<%dI'%(len(raw)//4),raw)
    frames=[];pending=[];pos=0;removed=[]
    while pos<len(words):
        at=pos;header=words[pos];op=header>>24;n=header&0xffffff
        assert pos+n<len(words),(at,n,len(words))
        payload=list(words[pos+1:pos+n+1]);pos+=n+1
        if op==0x11 and 0x10000000<=payload[0]<0x10300000:
            assert golden and len(frames)==1 and not pending
            assert payload==[0x10200000,0x028000f0,0x02800000]
            removed.append({'word':at,'payload':payload});continue
        pending.append([op,payload])
        if op==0x42:
            assert payload[0] in (0,1,2) and payload[1]==number('last_fence')+1+len(frames)
            index,token=payload;base=0x10000000+index*0x100000;normalized=[]
            for code,data in pending:
                data=data.copy()
                if code==0x4a:
                    assert len(data)==17 and data[0]==base
                    data[0]=0x10000000
                if code==0x42:data[0]=0
                normalized.append([code,data])
            frames.append({'buffer':index,'token':token,'commands':normalized,
                           'words':sum(1+len(data)for _,data in normalized)})
            pending=[]
    assert not pending and len(frames)==2
    assert len(removed)==int(golden)
    return frames,removed


prior.commands=commands


def validate(run):
    binding=prior.bind(run);health=prior.health(run)
    observed,_=commands(run/'commands.bin')
    golden,removed=commands(N/'capture/run-reference/snapshot/commands.bin',True)
    initial=(N/'capture/run-reference/snapshot/sdram.bin').read_bytes()
    clear=number('cleared_bufs_address')-0x10000000
    probe=number('import_probe_address')-0x10000000
    assert initial[clear]==3 and initial[0x200000:0x200000+COLOR]==bytes(COLOR)
    assert struct.unpack_from('<I',initial,probe)[0]==2
    prep=read(run/'prepared-memory.json')
    assert prep==dict(cleared_bufs_address=number('cleared_bufs_address'),cleared_bufs_before=3,
                     cleared_bufs_after=7,import_probe_address=number('import_probe_address'),
                     probe_before=2,probe_after=0,third_buffer_zero_bytes_checked=153600)
    history={j:initial[j*0x100000:j*0x100000+COLOR]for j in range(3)}
    frames=[]
    for frame,f in enumerate(observed,1):
        chosen=run/f'frame-{frame}-buffer{f["buffer"]}.bin'
        want=N/f'capture/run-reference/frame_{359+frame:05}.bin'
        assert chosen.read_bytes()==want.read_bytes(),('color/depth oracle',frame)
        history[f['buffer']]=chosen.read_bytes()[:COLOR]
        for j in range(3):
            raw=(run/f'frame-{frame}-buffer{j}.bin').read_bytes()
            assert raw[:COLOR]==history[j] and raw[COLOR:]==chosen.read_bytes()[COLOR:],('buffer history',frame,j)
        frames.append({'frame':frame,'buffer':f['buffer'],'sha256':sha(chosen)})
    registers={}
    for actual,expected in [('entry-regs.bin','entry-1.bin'),('entry-2.bin','entry-2.bin'),('entry-3.bin','entry-3.bin')]:
        a=struct.unpack('<66I',(run/actual).read_bytes())
        b=struct.unpack('<66I',(N/'guest/overlay/goldens'/expected).read_bytes())
        differences={str(j):[a[j],b[j]]for j in range(66)if a[j]!=b[j]}
        assert set(map(int,differences)).issubset(set()if actual=='entry-regs.bin'else{21}),(actual,differences)
        registers[actual]=differences
    pool=N/'capture/run-reference/snapshot/surface-pool-final.bin'
    assert (run/'surface-arena.bin').read_bytes()==pool.read_bytes()
    snap=read(run/'surface-arena.json')
    assert snap['base']==number('surface_base') and snap['bytes']==number('surface_bytes') and not snap['cached_psram']
    return {'binding':binding,'health':health,'frames':frames,'register_differences':registers,
            'all_FCSR_and_FP_registers_exact':True,'final_surface_pool_sha256':sha(pool),
            'untouched_buffer_history_exact':True,'removed_reference_clear':removed},observed,golden


def load_imports(path):
    # Passive successful-import log, one record per consumed0x5E command.
    with path.open() as f:
        rows=list(csv.DictReader(f,delimiter='\t'))
    result=[]
    for row in rows:
        record={k:int(v,0)for k,v in row.items()}
        if 'error'in record:assert record['error']==0
        result.append(record)
    return result


def import_lifetime(run, imports, frames):
    """BIM2 imports use immutable queued snapshots, not the current raw generation."""
    events=load_imports(run/'import-events.tsv')
    banks=load_imports(run/'bank-events.tsv')
    assert events and banks and all(not r[k]for r in events for k in ('error','convert_error','fault'))
    assert not events[-1]['leased'] and not events[-1]['owner'],'copy/read ownership remains at replay end'
    assert not banks[0]['valid'] and not banks[0]['owner'],'expected initially empty snapshot queue'
    assert not banks[-1]['valid'] and not banks[-1]['owner'] and not banks[-1]['copy_state'],'snapshot banks did not retire'
    allocations=0;previous=None;raw_overlap=0
    for state in banks:
        assert state['ready']&~state['valid']==0
        if state['owner']:
            mask=1<<state['read_bank']
            assert state['valid']&mask and state['ready']&mask,'GPU owner lacks ready snapshot'
        if state['copy_state']:
            mask=1<<state['copy_bank']
            assert state['valid']&mask and not state['ready']&mask,'copier bank metadata invalid'
        if previous:
            for bank in range(2):
                mask=1<<bank
                if state['valid']&mask and previous['valid']&mask:
                    assert state[f'token{bank}']==previous[f'token{bank}']
                    assert state[f'count{bank}']==previous[f'count{bank}']
                    assert not previous['ready']&mask or state['ready']&mask,'ready snapshot regressed to filling'
                if state['valid']&mask and not previous['valid']&mask:allocations+=1
        previous=state
    event_at=bank_at=0
    for row in imports:
        while event_at+1<len(events) and events[event_at+1]['cycle']<=row['cycle']:event_at+=1
        while bank_at+1<len(banks) and banks[bank_at+1]['cycle']<=row['cycle']:bank_at+=1
        state=banks[bank_at];event=events[event_at];bank=state['read_bank'];mask=1<<bank
        assert event['done'] and state['owner'],'completion lacks active read owner/done pulse'
        assert state['valid']&mask and state['ready']&mask,'completion read bank is not valid and ready'
        assert state[f'token{bank}']==row['token'],'completion used wrong immutable generation bank'
        assert 0<=row['source']<state[f'count{bank}']<=32,'completion source violates captured slot count'
        raw_overlap+=state['generation']!=row['token']
    op_counts=Counter(op for f in frames for op,_ in f['commands'])
    assert sum(r['done']for r in events)==len(imports)==op_counts[0x5e]
    assert sum(r['release']for r in events)==op_counts[0x5f]==allocations
    assert sum(r['start']for r in events)==op_counts[0x5e]
    return dict(successful_imports=len(imports),release_commands=op_counts[0x5f],snapshot_allocations=allocations,
                no_sticky_or_conversion_errors=True,all_completions_match_immutable_ready_bank=True,
                completions_after_raw_generation_advanced=raw_overlap,bank_tokens_counts_stable_until_release=True,
                final_banks_copy_and_owner_clear=True,events_sha256=sha(run/'import-events.tsv'),
                bank_events_sha256=sha(run/'bank-events.tsv'))


def semantics(frames,imports):
    slots={};at=0;events=[];draws=0;context=None
    for frame in frames:
        frame_events=[]
        for op,data in frame['commands']:
            if op==0x56:
                assert len(data)==8 and 0<=data[0]<32
                slots[data[0]]=data[1:];continue
            if op==0x5d:
                assert len(data)==3 and data[2]>>19==0
                context=data;continue
            if op==0x5e:
                assert len(data)==2 and data[1]>>10==0 and context is not None
                assert at<len(imports),'missing passive import completion'
                record=imports[at];at+=1
                src,dst=data[1]&31,(data[1]>>5)&31
                assert (record['token'],record['source'],record['destination'])==(data[0],src,dst)
                slots[dst]=[record[k]for k in ('clip_x','clip_y','clip_w','tex_u','tex_v','rgb565','depth')]
                continue
            if op==0x5f:
                assert len(data)==1
                continue
            if op==0x54:
                assert len(data)==1 and data[0]>>15==0
                indices=[(data[0]>>(5*k))&31 for k in range(3)]
                assert all(k in slots for k in indices),('undefined vertex',indices)
                frame_events.append([op,data,[slots[k].copy()for k in indices]])
                draws+=1
            else:
                assert op not in (0x53,0x57,0x58,0x59,0x5a,0x5b),('unhandled cache writer',hex(op))
                frame_events.append([op,data])
        events.append(frame_events)
    assert at==len(imports),'unmatched import completion'
    return events,{'indexed_draws':draws,'imports':at,'final_slot_values':slots}


def front_preparation(run):
    initial=(N/'capture/run-reference/snapshot/sdram.bin').read_bytes()
    offset=number('front_probe_address')-0x10000000
    before=struct.unpack_from('<I',initial,offset)[0]
    assert before in (0,2)
    prep=read(run/'prepared-front.json')
    assert prep==dict(probe_address=number('front_probe_address'),probe_before=before,probe_after=0)
    return prep


def front_control(run):
    rows=load_imports(run/'front-events.tsv')
    writes=[]
    for line in(run/'mmio.tsv').read_text().splitlines():
        c,kind,address,value=line.split()
        if kind=='GEO':writes.append((int(c),int(address)//4,int(value)))
    position=0;registers={};starts=[];pending_starts=[];active=None;completed=[];previous=None
    for row in rows:
        while position<len(writes) and writes[position][0]<row['cycle']:
            cycle,address,value=writes[position];position+=1
            if address in (0x12,0x13)or 0x200<=address<0x240:registers[address]=value
            if address==0x14 and value&1:
                batch=dict(cycle=cycle,token=registers[0x12],count=registers[0x13]&127,mode=registers[0x13]>>8)
                assert 1<=batch['count']<=64 and 0<=batch['mode']<=3
                batch['jobs']=[registers[0x200+i]for i in range(batch['count'])]
                assert all(job<32768 for job in batch['jobs'])
                starts.append(batch);pending_starts.append(batch)
        assert not row['error'],'TFP protocol error'
        assert row['owner']==int(row['state']not in (0,1))
        assert row['keep_mask']&row['fallback_mask']==0
        if row['state']:
            assert not row['producer_active'],'raw producer overlaps reserved preflight'
            if active is None:
                assert row['state']==1 and pending_starts
                active=pending_starts.pop(0);active['cull_results']={}
            assert row['generation']==row['token']==active['token']
            assert row['count']==active['count'] and row['mode']==active['mode']
            assert 0<=row['job']<active['count']
            assert (row['keep_mask']|row['fallback_mask'])>>active['count']==0
            if row['state']in (4,5,6):assert row['indices']==active['jobs'][row['job']]
            if row['state']==6 and row['c_out']:
                assert row['job']not in active['cull_results']
                active['cull_results'][row['job']]=dict(reject=row['c_reject'],bad=row['c_bad'],clip=row['c_clip'])
        elif active is not None:
            assert row['done'] and previous['state']==8
            assert set(active['cull_results'])==set(range(active['count']))
            clips=0
            for job,cull in active['cull_results'].items():
                keep=(row['keep_mask']>>job)&1;fallback=(row['fallback_mask']>>job)&1
                if cull['bad']:assert fallback and not keep
                elif cull['reject']:
                    assert not keep and not fallback
                    clips+=cull['reject']==1
                else:
                    assert keep^fallback
                    if cull['clip']:assert fallback
            assert row['clip_reject_count']==clips
            assert row['fallback']==bool(row['fallback_mask'])
            active.update(done_cycle=row['cycle'],keep=row['keep_mask'],fallback=row['fallback_mask'],clip_reject_count=clips)
            completed.append(active);active=None
        previous=row
    assert active is None and not pending_starts and len(starts)==len(completed)
    all_starts=sum(a==0x14 and v&1 for c,a,v in writes)
    assert all_starts==len(starts)
    initial=(N/'capture/run-reference/snapshot/sdram.bin').read_bytes()
    off=number('front_stats_address')-0x10000000
    before=struct.unpack_from('<16I',initial,off);after=struct.unpack('<16I',(run/'front-stats.bin').read_bytes())
    delta=[(a-b)&0xffffffff for a,b in zip(after,before)]
    assert delta[0]==len(starts) and delta[1]==sum(b['count']for b in starts)
    assert delta[7]==sum(bool(b['fallback'])for b in completed) and delta[13]==0
    assert delta[12]>=len(starts)
    return dict(started_batches=len(starts),triangles=sum(b['count']for b in starts),fallback_batches=delta[7],committed_batches=delta[2],committed_triangles=delta[3],stats_delta=delta,all_jobs_correlate_to_MMIO=True,all_results_correlate_to_cull_outputs=True,raw_generation_stable_and_exclusive=True,final_owner_idle=True,trace_sha256=sha(run/'front-events.tsv'))


def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['baseline','imported','front'])
    p.add_argument('run',type=Path);p.add_argument('report',type=Path)
    p.add_argument('--baseline',type=Path);p.add_argument('--imports',type=Path)
    p.add_argument('--fixture',type=Path,default=N);p.add_argument('--elf',type=Path)
    a=p.parse_args()
    global CFG
    globals()['N']=a.fixture.resolve()
    CFG=dict(line.split('=',1)for line in(N/'input.cfg').read_text().splitlines()if '='in line)
    fixture_manifest=read(N/'prepared.json')
    if a.elf:assert sha(a.elf.resolve())==fixture_manifest['elf_sha256']
    run=a.run.resolve();result,observed,golden=validate(run)
    if a.kind=='baseline':
        assert [f['commands']for f in observed]==[f['commands']for f in golden]
        result['status']='PASS_TFP_APP_BASELINE_FUNCTIONAL_ORACLE'
    else:
        assert a.baseline and a.imports
        base_frames,_=commands(a.baseline/'commands.bin')
        original,original_detail=semantics(base_frames,[])
        imported_rows=load_imports(a.imports)
        candidate,detail=semantics(observed,imported_rows)
        lifetime=import_lifetime(run,imported_rows,observed)
        assert candidate==original,'indexed vertex payload/material/draw semantic mismatch'
        assert detail['final_slot_values']==original_detail['final_slot_values']
        def geometry(run):
            return [line.split()[2:]for line in(run/'mmio.tsv').read_text().splitlines()
                    if line.split()[1]=='GEO' and int(line.split()[2]) not in (60,72,76,80) and not 2048<=int(line.split()[2])<2304]
        assert geometry(run)==geometry(a.baseline),'raw LOAD command or context changed'
        result.update(status='PASS_TFP_APP_IMPORT_FUNCTIONAL_AND_DRAW_SEMANTICS',semantic_comparison=detail,
                      geometry_nonclaim_mmio_exact=True,import_log_sha256=sha(a.imports),import_lifetime=lifetime)
    result['front_preparation']=front_preparation(run)
    if a.kind=='front':result['front_control']=front_control(run)
    result['normalization']='Only framebuffer bases/flip indices, known timestamp register, and prepared one-time zero clear. New opcodes are compared via actual imported payloads at every indexed draw.'
    result['evidence_sha256']={str(x):sha(x)for x in run.iterdir()if x.is_file()}
    result['fixture_manifest_sha256']=sha(N/'prepared.json');result['fixture']=str(N)
    result['fixture_elf_sha256']=fixture_manifest['elf_sha256']
    result['checker_sha256']=sha(Path(__file__));result['shared_check_sha256']=sha(OLD)
    result['limitations']=['Two prepared F13 intervals with fixed synthetic scan/audio; not sustained hardware FPS or full Pocket OS.',
                           'TFP1 replaces eligible current-generation culling and lazy canonical readback; two prepared intervals only.']
    assert not a.report.exists();a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k not in ('evidence_sha256','semantic_comparison')},indent=2))


if __name__=='__main__':main()
