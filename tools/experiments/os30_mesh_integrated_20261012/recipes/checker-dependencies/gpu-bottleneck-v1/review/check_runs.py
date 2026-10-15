#!/usr/bin/env python3
"""Independent passive identity and fresh normal-app functional comparison."""
import argparse,hashlib,json,struct
from collections import Counter
from pathlib import Path
G=Path(__file__).resolve().parents[1];P=G.parent
N=G/'fixture-normal-v1';COLOR=153600
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
def verify(inventory):
    for p,h in inventory.items():assert sha(Path(p))==h,p

def bind(run):
    b=read(run.parent/'build.json');m=Path(b['model'])
    verify(b['inputs_sha256'])
    verify({str(m/p):h for p,h in read(m/'prepared.json')['source_sha256'].items()})
    d=read(run/'process.json');assert d['returncode']==0 and d['executable_sha256']==sha(run.parent/'native_profiled')
    assert d['inputs_sha256']==b['inputs_sha256']
    verify({str(run/p):h for p,h in read(run/'manifest.json')['outputs_sha256'].items()})
    return dict(bound_files=len(b['inputs_sha256']),model_sources=len(read(m/'prepared.json')['source_sha256']))

def commands(path,golden=False):
    data=path.read_bytes();assert len(data)%4==0
    words=struct.unpack('<%dI'%(len(data)//4),data)
    frames=[];pending=[];pos=0;removed=[]
    while pos<len(words):
        at=pos;h=words[pos];op=h>>24;n=h&0xffffff;assert pos+n<len(words)
        payload=list(words[pos+1:pos+n+1]);pos+=n+1
        if op==0x11 and 0x10000000<=payload[0]<0x10300000:
            assert golden and at==23036 and payload==[0x10200000,0x028000f0,0x02800000],(at,payload)
            removed.append(dict(word=at,payload=payload));continue
        pending.append([op,payload])
        if op==0x42:
            assert payload[0]in (0,1,2) and payload[1]==362+len(frames)
            idx,token=payload;base=0x10000000+idx*0x100000;normalized=[]
            for code,p in pending:
                p=p.copy()
                if code==0x4a:assert len(p)==17 and p[0]==base;p[0]=0x10000000
                if code==0x42:p[0]=0
                normalized.append([code,p])
            frames.append(dict(buffer=idx,token=token,commands=normalized,words=sum(1+len(p)for _,p in normalized)))
            pending=[]
    assert not pending and len(frames)==2
    assert len(removed)==int(golden)
    return frames,removed

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
    parsed,_=commands(run/'commands.bin')
    counts=Counter(op for f in parsed for op,_ in f['commands'])
    observed=[sum(w['command_entries'][i]for k,w in gpu['windows'].items()if k!='0')for i in range(256)]
    assert observed==[counts[i]for i in range(256)],'decoded command count mismatches upload stream'
    assert [r[3]for r in rows]==[0,parsed[0]['words'],sum(f['words']for f in parsed)]
    snap=read(run/'surface-arena.json');assert snap['arena_pending_state_checks_pass'] and snap['host_clock_edges_added']==0
    return dict(cycles=durations,latency_maxima=latency,gpu_counters_reconcile=True,commands=sum(counts.values()),command_words=d['upload_words'])

def experimental(run):
    base=P/'first-word-v1/prepared-base/run';exact=[]
    for p in base.iterdir():
        if not p.is_file()or p.name in ('manifest.json','process.json'):continue
        assert (run/p.name).read_bytes()==p.read_bytes(),p.name;exact.append(p.name)
    return dict(status='PASS_PASSIVE_OBSERVER_IDENTICAL_BASELINE',binding=bind(run),health=health(run),exact_existing_outputs=exact)

def normal(run):
    binding=bind(run);h=health(run)
    observed,_=commands(run/'commands.bin');gold,removed=commands(N/'capture/run-reference/snapshot/commands.bin',True)
    assert [f['commands']for f in observed]==[f['commands']for f in gold]
    initial=(N/'capture/run-reference/snapshot/sdram.bin').read_bytes()
    assert initial[0xfd7fd4]==3 and initial[0x200000:0x200000+COLOR]==bytes(COLOR)
    history={i:initial[i*0x100000:i*0x100000+COLOR]for i in range(3)}
    frames=[]
    for i,f in enumerate(observed,1):
        chosen=run/f'frame-{i}-buffer{f["buffer"]}.bin';want=N/f'capture/run-reference/frame_{359+i:05}.bin'
        assert chosen.read_bytes()==want.read_bytes(),('logical color/depth',i)
        history[f['buffer']]=chosen.read_bytes()[:COLOR]
        for j in range(3):
            raw=(run/f'frame-{i}-buffer{j}.bin').read_bytes()
            assert raw[:COLOR]==history[j] and raw[COLOR:]==chosen.read_bytes()[COLOR:],('physical buffer',i,j)
        frames.append(dict(frame=i,buffer=f['buffer'],sha256=sha(chosen)))
    differences={}
    for actual,expected in [('entry-regs.bin','entry-1.bin'),('entry-2.bin','entry-2.bin'),('entry-3.bin','entry-3.bin')]:
        a=struct.unpack('<66I',(run/actual).read_bytes());b=struct.unpack('<66I',(N/'guest/overlay/goldens'/expected).read_bytes())
        diff={str(i):[a[i],b[i]]for i in range(66)if a[i]!=b[i]}
        assert set(map(int,diff)).issubset(set()if actual=='entry-regs.bin'else{21}),(actual,diff)
        differences[actual]=diff
    pool=G/'review/normal-final-pool-reference/run-reference/snapshot/surface-pool-final.bin'
    assert (run/'surface-arena.bin').read_bytes()==pool.read_bytes()
    snap=read(run/'surface-arena.json');assert snap['base']==0x11007000 and snap['bytes']==110400 and not snap['cached_psram']
    return dict(status='PASS_NORMAL_DEPLOYED_F12_FUNCTIONAL_OUTPUTS',binding=binding,health=h,frames=frames,
        command_normalization='Only typed SET_TRI_STATE bases and FLIP indices. Functional golden alone removes one explicit pre-initialized buffer2 zero-clear.',
        removed_golden_clear=removed,register_differences=differences,final_surface_pool_exact=True,
        final_surface_pool_sha256=sha(pool),untouched_color_buffer_history_exact=True,
        limitations=['Two bounded deterministic frames, synthetic traffic; not full Pocket OS/PCM/IRQ/APF or sustained hardware FPS.'])

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('kind',choices=['experimental','normal']);p.add_argument('run',type=Path);p.add_argument('report',type=Path);a=p.parse_args()
    result=globals()[a.kind](a.run.resolve());result['output_evidence_sha256']={str(p):sha(p)for p in a.run.iterdir()if p.is_file()}
    result['checker_sha256']=sha(Path(__file__))
    assert not a.report.exists();a.report.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items()if k!='output_evidence_sha256'},indent=2))
