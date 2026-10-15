#!/usr/bin/env python3
"""Bind each v4 omitted LOAD to actual immutable source and exhausted cache probes."""
import bisect,csv,hashlib,json,struct
from pathlib import Path
V=Path(__file__).resolve().parent
N=V.parent
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rows(p):
    with p.open() as f:return [{k:int(v) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]
def raw_jobs(p):
    state={r:0 for r in list(range(3,13))+list(range(64,80))+[0x24]};out=[]
    for line in (p/'mmio.tsv').read_text().splitlines():
        c,k,a,v=line.split();r=int(a)//4;v=int(v)
        if k!='GEO':continue
        if r in state:state[r]=v
        if (r==2 and v&1) or (r==0x26 and v==1):out.append(dict(cycle=int(c),mesh=r==0x26,state=state.copy()))
    return out
def main():
    report=V/'source-proof-v4.json';assert not report.exists()
    elf=N/'work-app-v4/.obj/sm64-mesh-integrated-v4-perfdiag/app.elf'
    assert sha(elf)=='883e2b4cfcd105bddc032d89c4379cd72702621cd1923cacbd0b6596ad02ce9e'
    sdpath=V/'fixture-v4/capture/run-reference/snapshot/sdram.bin';sd=sdpath.read_bytes()
    assert not any(sd[0x111697f0-0x10000000:0x1116a7f0-0x10000000])
    data=elf.read_bytes();assert data[:6]==b'\x7fELF\x01\x01'
    phoff=struct.unpack_from('<I',data,28)[0];phsize,phnum=struct.unpack_from('<HH',data,42)
    segments=[]
    for i in range(phnum):
        t,off,va,pa,fs,ms,flags,align=struct.unpack_from('<8I',data,phoff+i*phsize)
        if t==1:segments.append((va,va+fs,off))
    def immutable(a,n):
        matches=[data[o+a-lo:o+a-lo+n] for lo,hi,o in segments if lo<=a and a+n<=hi]
        assert len(matches)==1 and matches[0]==sd[a-0x10000000:a-0x10000000+n]
        return matches[0]
    bindings={str(Path(__file__).resolve()):sha(__file__),str(elf):sha(elf),str(sdpath):sha(sdpath)}
    traces=[];jobs=[];passivity=[]
    for observed,original in [('source-proof-control-v4','control-v4'),('source-proof-candidate-v7-appv4','candidate-v7-appv4')]:
        a=V/observed/'run';b=V/original/'run';same=[]
        for p in b.iterdir():
            if not p.is_file() or p.name in ('manifest.json','process.json'):continue
            assert p.read_bytes()==(a/p.name).read_bytes(),p.name
            same.append(p.name);bindings[str(p)]=sha(p);bindings[str(a/p.name)]=sha(a/p.name)
        for p in (a/'source-proof.tsv',a/'source-windows.tsv',a/'process.json',a/'manifest.json',a.parent/'build.json'):
            bindings[str(p)]=sha(p)
        build=json.loads((a.parent/'build.json').read_text())
        for p,h in build['inputs_sha256'].items():assert sha(p)==h;bindings[p]=h
        assert json.loads((a/'process.json').read_text())['returncode']==0
        passivity.append(dict(observed=observed,original=original,all_preexisting_outputs_byte_identical=sorted(same)))
        traces.append(rows(a/'source-proof.tsv'));jobs.append(raw_jobs(b))
    loads=[[r for r in trace if r['pc']==0x104ce880] for trace in traces]
    args=lambda r:tuple(r[f'r{i}'] for i in (10,11,12,13))
    assert len(loads[0])==len(loads[1])==708
    assert [args(r) for r in loads[0]]==[args(r) for r in loads[1]]
    mapped=[]
    for trace,load,jj in zip(traces,loads,jobs):
        cc=[r['cycle'] for r in load];bycall={}
        for ji,j in enumerate(jj):
            i=bisect.bisect_right(cc,j['cycle'])-1
            assert i>=0 and i not in bycall
            assert j['state'][4]==load[i]['r10'] # actual shape matches original call
            bycall[i]=(ji,j)
        mapped.append(bycall)
    assert set(mapped[1])<=set(mapped[0])
    missing=sorted(set(mapped[0])-set(mapped[1]));assert missing==[281,635]
    for i,(ji,j) in mapped[1].items():
        _,ref=mapped[0][i]
        for r in list(range(4,13))+list(range(64,80)):
            if r in range(6,11) and j['state'][12]==ref['state'][12]==0:continue
            assert ref['state'][r]==j['state'][r],(i,r)
    trace=traces[1];cc=[r['cycle'] for r in loads[1]]
    calls=[r for r in trace if r['pc']==0x104dc900];assert len(calls)==638
    failures=[r for r in trace if r['pc']==0x104dcb7c];assert len(failures)==2
    windows=rows(V/'source-proof-candidate-v7-appv4/run/source-windows.tsv')
    assert len(windows)==32
    table=[None]*512;out=[];inserts=hits=0
    for call in calls:
        i=bisect.bisect_right(cc,call['cycle'])-1;load=loads[1][i]
        vertices,count,command,commands=(call[f'r{r}'] for r in (10,11,12,13))
        assert (vertices,count)==(load['r12'],load['r10'])
        vtx=immutable(command-8,8);w0,w1=struct.unpack('<II',vtx)
        assert w0>>24==1 and (w0>>12)&255==count and ((w0>>1)&127)==count and w1==vertices
        vertex_bytes=immutable(vertices,count*24);command_bytes=immutable(command,commands*8)
        h=((command>>3)^(command>>13)^(commands*33))&511
        indices=[(h+p)&511 for p in range(16)]
        slot=next((s for s in indices if table[s] is None or table[s][:2]==(command,commands)),None)
        if slot is None:
            assert i in missing and i not in mapped[1]
            end=cc[i+1] if i+1<len(cc) else 1<<63
            scope=[r for r in trace if call['cycle']<=r['cycle']<end]
            assert [r['pc'] for r in scope]==[0x104dc900,0x104dca3a,0x104dcb78,0x104dcb7c,0x104cece8]
            fail=scope[3];window=[r for r in windows if r['cycle']==fail['cycle']]
            assert [r['slot'] for r in window]==indices
            for r in window:
                assert table[r['slot']]==(r['source'],r['offset_count']>>24,r['offset_count']&0xffffff)
                assert r['source'] and (r['source'],r['offset_count']>>24)!=(command,commands)
            # Retired terminal branch used the nonzero final occupied entry;
            # s9 is the retained immutable command pointer in this exact ELF.
            assert scope[2]['r16']==window[-1]['source'] and scope[2]['r25']==command
            ji,j=mapped[0][i]
            out.append(dict(phase=call['phase'],load_call_index=i,reference_raw_job_index=ji,
                original_vtx_address=command-8,vertices=vertices,count=count,commands=command,command_count=commands,
                first_probe=h,probe_slots=indices,probe_window=window,reference_start_cycle=j['cycle'],
                candidate_false_return_cycle=fail['cycle'],vertex_sha256=hashlib.sha256(vertex_bytes).hexdigest(),
                commands_sha256=hashlib.sha256(command_bytes).hexdigest()))
        else:
            assert i in mapped[1] and mapped[1][i][1]['mesh']
            offset=mapped[1][i][1]['state'][0x24]
            if table[slot] is None:table[slot]=(command,commands,offset);inserts+=1
            else:assert table[slot]==(command,commands,offset);hits+=1
    assert len(out)==2 and inserts==275 and hits==361
    for phase in (1,2):
        with (V/f'candidate-v7-appv4/run/pc-phase-{phase}.csv').open() as f:
            pcs={int(r['pc']):int(r['retired']) for r in csv.DictReader(f)}
        assert pcs.get(0x104dce02,0)==0 and pcs.get(0x104d7ea4,0)==0 # no allocation fallback or recycle
    result=dict(status='PASS_EXACT_PER_CALL_SOURCE_AND_CACHE_EXHAUSTION_PROOF',
        elf_sha256=sha(elf),passivity=passivity,load_calls=708,all_original_LOAD_arguments_equal_in_order=True,
        mesh_source_calls=638,cache_insertions=inserts,cache_hits=hits,cache_exhaustion_failures=out,
        omitted_reference_jobs=[r['reference_raw_job_index'] for r in out],
        each_actual_START_effective_context_exact=True,no_raw_START_in_failed_call=True,
        full_cache_history_reconstructed_and_failed_windows_match_actual_CPU_cache=True,
        source_pool_recycle_calls=0,allocation_capacity_fallback_calls=0,inputs_sha256=bindings,
        scope='Read-only replay proof; all original cycles and outputs unchanged. No performance credit or app/core mutation.')
    report.write_text(json.dumps(result,indent=2)+'\n');print(report,sha(report))
if __name__=='__main__':main()
