#!/usr/bin/env python3
"""Exact queued-front native oracle, output semantics and queue lifetime checks."""
import argparse, csv, hashlib, importlib.util, json, struct
from collections import Counter
from pathlib import Path
Q=Path(__file__).resolve().parent
T=Q.parent/'triangle-front-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
spec=importlib.util.spec_from_file_location('exact_prior',T/'tests/check_replays.py')
ref=importlib.util.module_from_spec(spec);spec.loader.exec_module(ref)
def health(run):
    d=read(run/'result.json');assert d['status']==0 and d['fence']==363
    assert all(d[k]==0 for k in ('write_errors','scan_errors','cram_errors'))
    assert all(x['errors']==0 for x in d['readers'].values())
    rows=[list(map(int,x.split()))for x in (run/'boundaries.tsv').read_text().splitlines()]
    assert [r[0]for r in rows]==[1,2,3] and [r[4]for r in rows]==[361,362,363]
    assert all(r[5]==0 for r in rows)
    durations=[rows[i+1][1]-rows[i][1]for i in (0,1)]
    assert rows[-1][1]==d['finished_cycle']==d['complete_cycle']
    first=read(run/'first-word.json');gpu=read(run/'gpu-profile.json')
    assert not any(gpu['pending_reads'])
    latency={}
    for key,w in first['windows'].items():
        assert w['protocol_errors']==w['audio_overlap_cycles']==0
        assert w['scan_latency']['over_deadline']==w['audio_latency']['over_deadline']==w['audio_queue_latency']['over_deadline']==0
        assert w['scan_latency']['max']<2862 and w['audio_latency']['max']<1843
        latency[key]={k:w[k]['max']for k in ('scan_latency','audio_latency','audio_queue_latency')}
    for key,w in gpu['windows'].items():
        assert w['protocol_errors']==0
        assert sum(w['partition'])==sum(w['states'])==sum(w['cache_states'])==sum(w['texture_states'])==w['cycles']
        assert sum(w['partition_cache'])==sum(w['partition_texture'])==w['cycles']
        assert sum(w['partition_read_activity'])==5*w['cycles']
        assert sum(w['poll_partition'])==w['metrics']['cpu_ring_poll_context']
        for i in range(len(w['partition'])):
            assert sum(w['partition_cache'][32*i:32*(i+1)])==w['partition'][i]
            assert sum(w['partition_texture'][8*i:8*(i+1)])==w['partition'][i]
        if key!='0':assert w['cycles']==durations[int(key)-1]
    assert sum(gpu['windows'][str(i)]['metrics']['gpu_busy']for i in (1,2))==d['gpu_busy_cycles']
    assert sum(gpu['windows'][str(i)]['metrics']['cpu_retire']for i in (1,2))==d['retired']
    ingress,_=ref.commands(run/'commands.bin')
    output=run/'worker-commands.bin'
    parsed,_=ref.commands(output if output.exists() else run/'commands.bin')
    counts=Counter(op for f in parsed for op,_ in f['commands'])
    observed=[sum(w['command_entries'][i]for k,w in gpu['windows'].items()if k!='0')for i in range(256)]
    assert observed==[counts[i]for i in range(256)],'decoded command count mismatches upload stream'
    assert [r[3]for r in rows]==[0,ingress[0]['words'],sum(f['words']for f in ingress)]
    snap=read(run/'surface-arena.json');assert snap['arena_pending_state_checks_pass'] and snap['host_clock_edges_added']==0
    return dict(cycles=durations,latency_maxima=latency,gpu_counters_reconcile=True,commands=sum(counts.values()),command_words=d['upload_words'])


def rows(path):
    with path.open() as f:
        return [{k:int(v,0) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]


def queue_checks(run, ingress):
    states=rows(run/'queue-events.tsv')
    banks=rows(run/'bank-events.tsv')
    assert states and banks
    assert all(not r['error'] for r in states),'queued-front protocol error'
    assert not banks[-1]['valid'] and not banks[-1]['owner'] and not banks[-1]['copy_state']
    previous=None; allocations=0; advanced=0
    for b in banks:
        assert b['ready'] & ~b['valid']==0
        if b['owner']:
            mask=1<<b['read_bank'];assert b['ready']&mask
            advanced+=b['generation']!=b['token%d'%b['read_bank']]
        if previous:
            for i in range(2):
                mask=1<<i
                if b['valid']&previous['valid']&mask:
                    assert b['token%d'%i]==previous['token%d'%i]
                    assert b['count%d'%i]==previous['count%d'%i]
                    assert not previous['ready']&mask or b['ready']&mask
                if b['valid']&mask and not previous['valid']&mask:allocations+=1
        previous=b
    counts=Counter(op for frame in ingress for op,_ in frame['commands'])
    assert counts[0x61]==allocations
    return dict(runs=counts[0x60],released_generations=counts[0x61],
                snapshot_allocations=allocations,snapshot_observations_after_raw_advanced=advanced,
                protocol_errors=0,final_snapshot_banks_empty=True)


def resolved_semantics(frames):
    # QGF may prepare sticky state for a run that is later completely culled.
    # Compare every draw with its effective state; preserve all side effects.
    slots={};material=None;obj=None;events=[];draws=0
    for frame in frames:
        out=[]
        for op,data in frame['commands']:
            if op==0x56:
                assert len(data)==8 and 0<=data[0]<32
                slots[data[0]]=data[1:];continue
            if op==0x4a:
                assert len(data)==17;material=data;continue
            if op==0x50:
                assert len(data)==26;obj=data;continue
            if op==0x54:
                assert len(data)==1 and data[0]<32768 and material is not None and obj is not None
                indices=[(data[0]>>(5*k))&31 for k in range(3)]
                assert all(k in slots for k in indices)
                out.append([op,[slots[k]for k in indices],material,obj]);draws+=1
            elif op==0x4e:
                assert material is not None
                out.append([op,data,material])
            else:
                assert op in (0x11,0x42,0x02),('unmodelled side effect',hex(op))
                out.append([op,data])
        events.append(out)
    return events,dict(indexed_draws=draws)


def main():
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['baseline','queued'])
    p.add_argument('run',type=Path);p.add_argument('report',type=Path)
    p.add_argument('--fixture',type=Path,default=Q/'fixture-v1')
    p.add_argument('--baseline',type=Path)
    a=p.parse_args();run=a.run.resolve()
    ref.N=a.fixture.resolve()
    ref.CFG=dict(line.split('=',1)for line in(ref.N/'input.cfg').read_text().splitlines()if '='in line)
    ref.prior.health=health
    result,ingress,golden=ref.validate(run)
    result['preparation']=ref.front_preparation(run)
    if a.kind=='baseline':
        assert [f['commands']for f in ingress]==[f['commands']for f in golden]
    else:
        assert a.baseline
        baseline,_=ref.commands(a.baseline/'commands.bin')
        output,_=ref.commands(run/'worker-commands.bin')
        original,original_detail=resolved_semantics(baseline)
        candidate,detail=resolved_semantics(output)
        assert candidate==original,'resolved draw vertices/material/state/order mismatch'
        result['semantics']=dict(indexed_draws=detail['indexed_draws'],all_draws_and_state_exact=True)
        result['queue']=queue_checks(run,ingress)
        def geometry(path):
            return [line.split()[2:]for line in(path/'mmio.tsv').read_text().splitlines()
                    if line.split()[1]=='GEO' and int(line.split()[2])!=60
                    and not 0x80<=int(line.split()[2])<0xc0]
        assert geometry(run)==geometry(a.baseline),'raw LOAD/context commands changed'
        result['raw_load_commands_exact']=True
    result['status']='PASS_QGF1_'+a.kind.upper()
    result['evidence_sha256']={str(x):sha(x)for x in run.iterdir()if x.is_file()}
    result['checker_sources_sha256']={str(x):sha(x)for x in [Path(__file__),T/'tests/check_replays.py',ref.OLD]}
    result['fixture_manifest_sha256']=sha(ref.N/'prepared.json')
    result['limitations']=['Two prepared native intervals with synthetic display/audio and bounded services; not hardware FPS, FPGA fit or timing.']
    assert not a.report.exists();a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k not in ('evidence_sha256',)},indent=2))

if __name__=='__main__':main()
