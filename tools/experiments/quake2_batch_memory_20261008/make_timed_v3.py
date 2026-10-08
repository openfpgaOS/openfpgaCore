#!/usr/bin/env python3
"""Derive a frame-aligned sensitivity replay; preserve v1/v2 evidence."""
from pathlib import Path
T=Path(__file__).resolve().parent
source=(T/'timed_replay_v2.py').read_text().replace("O=P/'timed-replay-v2'", "O=P/'timed-replay-v3'")
source=source.replace('static const char *schedule_path;', 'static bool producer_ended;\nstatic const char *schedule_path;')
source=source.replace('    producer_origin=cycles;producer_delay=producer_wait=producer_idle=0;',
    '    producer_ended=false;producer_origin=cycles;producer_delay=producer_wait=producer_idle=0;')
anchor="    new=once(new,'    observe_report();',r'''"
addition='''    new=once(new,'        } else if (tag == GV_TAG_DUMP) {',r\'''        } else if (tag == GV_TAG_DUMP) {
            if(schedule_path && !producer_ended) {
                while(schedule_pos<schedule.size()) {
                    auto e=schedule[schedule_pos++];
                    producer_until(producer_origin+uint64_t(e.cycle*producer_scale)+producer_delay);
                    if(e.kind==8) {producer_ended=true;break;}
                    if(e.kind!=5 && e.kind!=7)throw std::runtime_error("Bad frame-tail event");
                    uint64_t start=cycles;
                    while(int32_t(tb->fence_reached-e.value)<0) {
                        if(cycles-start>50000000)throw std::runtime_error("Frame-tail fence timeout");
                        tick();
                    }
                    fence_wait+=cycles-start;producer_delay+=cycles-start;
                }
                if(!producer_ended)throw std::runtime_error("Missing frame end");
            }\''')
'''
assert source.count(anchor)==1;source=source.replace(anchor,addition+anchor,1)
a=source.index('def vector(');b=source.index('\ndef run(',a)
vector=r'''def vector(label,frame,dest):
    source=T/label/'gpuvec'/f'frame_{frame:05}.vec';data=source.read_bytes();p=0;parts=[];pubs=[]
    trace=json.loads((T/f'{label}-frame-{frame:05}.json').read_text());all_events=trace['events']
    all_pubs=[e for e in all_events if e['kind']==4]
    own_pubs=[e for e in all_pubs if e['frame']==frame]
    assert own_pubs and len(own_pubs)<len(all_pubs)
    assert own_pubs==all_pubs[:len(own_pubs)]
    zaddr=zstride=zstep=0;framebuffer=None;count=0;commands=[]
    while p<len(data):
        start=p;tag=data[p:p+4];p+=4;keep=count<len(own_pubs)
        if tag in [b'MEM1',b'TEX1']:
            addr,n=struct.unpack_from('<II',data,p);p+=8+((n+3)&~3)
            if tag==b'MEM1' and addr<0x300000:
                assert addr+n<=0x300000;keep=False
        elif tag==b'CMDS':
            n=struct.unpack_from('<I',data,p)[0];p+=4
            words=list(struct.unpack_from('<'+'I'*n,data,p));p+=4*n
            assert words==all_pubs[count]['words'];pubs.append(words)
            if keep:
                i=0
                while i<n:
                    op=words[i]>>24;length=words[i]&8191
                    if op==0x4a and length==17:zaddr,zstride,zstep=words[i+12:i+15]
                    commands.append(words[i:i+length+1]);i+=length+1
            count+=1
        elif tag==b'TLUT':p+=32768
        elif tag==b'PALB':p+=4
        elif tag==b'MMIO':p+=8
        elif tag==b'DUMP':
            addr,n=struct.unpack_from('<II',data,p);p+=8
            if addr<0x300000 and n==153600:framebuffer=addr
            keep=False
        elif tag==b'RAND':p+=12
        elif tag in [b'FLSH',b'FENC',b'HASH']:keep=keep and tag==b'FLSH'
        elif tag==b'END.':assert p==len(data);keep=False
        else:raise AssertionError((tag,p))
        assert p<=len(data)
        if keep:parts.append(data[start:p])
    assert pubs==[e['words'] for e in all_pubs]
    assert zaddr and zstride and zstep and framebuffer is not None
    # End before every next-frame command; dump complete rendered Z and color.
    parts.extend([b'DUMP'+struct.pack('<II',zaddr,zstride*240),
                  b'DUMP'+struct.pack('<II',framebuffer,153600),b'FENC',b'END.'])
    dest.write_bytes(b''.join(parts))
    global_data=(T/(label+'.trace')).read_bytes();pos=0;flips={}
    while pos<len(global_data):
        kind,a,fr,cycle,aux=struct.unpack_from('<IIQQQ',global_data,pos);pos+=32
        if kind==4:pos+=4*a
        elif kind==1:flips[fr]=cycle
    origin,end=flips[frame-1],flips[frame]
    selected=[e for e in all_events if e['frame']==frame and e['kind'] in [4,5,7]]
    assert all(origin<=e['cycle']<=end for e in selected)
    text=''.join(f"{e['kind']} {e['cycle']-origin} {e['a']}\n" for e in selected)
    dest.with_suffix('.schedule').write_text(text+f'8 {end-origin} 0\n')
    put(dest.with_suffix('.inputs.json'),dict(raw_sha256=sha(source),vector_sha256=sha(dest),
        schedule_sha256=sha(dest.with_suffix('.schedule')),trace_sha256=sha(T/(label+'.trace')),
        original_batches_retained=len(own_pubs),next_frame_batches_removed=len(all_pubs)-len(own_pubs),
        flattened_commands=commands,zaddr=zaddr,zstride=zstride,zstep=zstep,
        framebuffer=framebuffer,cpu_frame_start_cycle=origin,cpu_frame_end_cycle=end,
        cpu_frame_model_cycles=end-origin,all_current_frame_batches_exact=True))
'''
source=source[:a]+vector+source[b:]
source=source.replace("'Raw sampled envelope includes initial/next-frame clears; timings are not exact device frame boundaries.'",
    "'Current-frame commands only; next-frame capture suffix removed, and previous/current modeled flips define the sensitivity window. Initial cache/reset state is synthetic, not continuous device execution.'")
source=source.replace("'Timed run checks complete color/fence output; nontrivial depth is qualified separately by the original selected replay.'",
    "'Timed run compares complete color and rendered depth before any next-frame clear; no CPU HUD snapshots injected.'")
dest=T/'timed_replay_v3.py';assert not dest.exists();dest.write_text(source)
