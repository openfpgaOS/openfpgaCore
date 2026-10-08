#!/usr/bin/env python3
"""Conditional fixed-producer replay, with real GPU fences and backpressure."""
from pathlib import Path
import argparse, concurrent.futures, difflib, hashlib, json, re, shutil, struct, subprocess
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-batch-memory-20261008'
M=P/'cache-observer-v1';O=P/'timed-replay-v1';T=P/'publish-trace-v1'
def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def once(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b,1)

def prepare():
    O.mkdir(exist_ok=False)
    old=(M/'frozen/replay.cpp').read_text();new=old
    new=once(new,'static uint64_t cycles, busy_cycles;',r'''static uint64_t cycles, busy_cycles;
struct PublishEvent { unsigned kind, value; uint64_t cycle; };
static std::vector<PublishEvent> schedule;
static size_t schedule_pos;
static uint64_t producer_origin, producer_delay, producer_wait, producer_idle;
static uint64_t fence_wait, ring_wait, transport_late, publish_target;
static const char *schedule_path;
static double producer_scale=1.0;
''')
    anchor='static int run_vector(const char *vec_path, const char *out_path) {'
    helper=r'''static void producer_until(uint64_t target) {
    while(cycles<target) {
        producer_wait++;
        producer_idle+=!tb->busy;
        tick();
    }
}
static void producer_before(unsigned words) {
    if(!schedule_path)return;
    while(schedule_pos<schedule.size()) {
        auto e=schedule[schedule_pos++];
        uint64_t target=producer_origin+uint64_t(e.cycle*producer_scale)+producer_delay;
        if(e.kind==4) {
            if(e.value!=words)throw std::runtime_error("Schedule batch length mismatch");
            publish_target=target;
            producer_until(target>words ? target-words : 0);
            return;
        }
        if(e.kind!=5 && e.kind!=7)throw std::runtime_error("Bad producer event");
        producer_until(target);
        uint64_t start=cycles;
        while(int32_t(tb->fence_reached-e.value)<0) {
            if(cycles-start>50000000)throw std::runtime_error("Scheduled fence timeout");
            tick();
        }
        fence_wait+=cycles-start;producer_delay+=cycles-start;
    }
    throw std::runtime_error("Missing scheduled publication");
}
static void producer_after(void) {
    if(schedule_path && cycles>publish_target+1) {
        uint64_t late=cycles-publish_target-1;
        producer_delay+=late;transport_late+=late;
    }
}

'''
    new=once(new,anchor,helper+anchor)
    new=once(new,'    size_t pos = 0;',r'''    producer_origin=cycles;producer_delay=producer_wait=producer_idle=0;
    fence_wait=ring_wait=transport_late=0;schedule_pos=0;
    size_t pos = 0;''')
    new=once(new,'            uint64_t reserve_start=cycles;',
        '            producer_before(n);\n            uint64_t reserve_start=cycles;')
    new=once(new,'            for (uint32_t i = 0; i < n; i++) mmio_write(1, rd32());',
        '            ring_wait+=cycles-reserve_start;\n            for (uint32_t i = 0; i < n; i++) mmio_write(1, rd32());')
    new=once(new,'            mmio_write(0, 8);   // publish',
        '            mmio_write(0, 8);   // publish\n            producer_after();')
    new=once(new,'    observe_report();',r'''    if(schedule_path && schedule_pos!=schedule.size())throw std::runtime_error("Unused schedule events");
    printf("PRODUCER wait=%llu idle_wait=%llu fence_wait=%llu ring_wait=%llu transport_late=%llu delay=%llu\n",
        (unsigned long long)producer_wait,(unsigned long long)producer_idle,
        (unsigned long long)fence_wait,(unsigned long long)ring_wait,
        (unsigned long long)transport_late,(unsigned long long)producer_delay);
    observe_report();''')
    new=once(new,'    tb = new Vtb_gpu;',r'''    for(int a=1;a<argc;++a) {
        if(!strncmp(argv[a],"+schedule=",10))schedule_path=argv[a]+10;
        if(!strncmp(argv[a],"+producer_scale=",16))producer_scale=atof(argv[a]+16);
    }
    if(schedule_path) {
        FILE *f=fopen(schedule_path,"r");if(!f)throw std::runtime_error("Missing schedule");
        unsigned kind,value;unsigned long long cycle;
        while(fscanf(f,"%u %llu %u",&kind,&cycle,&value)==3)schedule.push_back({kind,value,uint64_t(cycle)});
        fclose(f);if(schedule.empty() || producer_scale<=0)throw std::runtime_error("Invalid schedule");
    }
    tb = new Vtb_gpu;''')
    (O/'replay.cpp').write_text(new)
    (O/'source.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='replay.cpp',tofile='replay.cpp')))
    obj=M/'obj';objects=[obj/x for x in ['verilated.o','verilated_dpi.o','verilated_threads.o','Vtb_gpu__ALL.a']]
    cmd=['g++','-std=c++17','-O2','-I'+str(obj),'-I'+str(M/'frozen'),'-I/usr/share/verilator/include',
         '-I/usr/share/verilator/include/vltstd',str(O/'replay.cpp'),*[str(x) for x in objects],'-pthread','-latomic','-o',str(O/'timed_gpu')]
    with (O/'build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    put(O/'prepared.json',dict(passed=True,command=cmd,recipe_sha256=sha(Path(__file__)),
        binary_sha256=sha(O/'timed_gpu'),parent_binary_sha256=sha(obj/'Vtb_gpu'),
        link_objects={str(x.relative_to(R)):sha(x) for x in objects},
        limits=['CPU arrival times are scalar model estimates. This is sensitivity analysis, not coupled CPU/GPU or measured FPS.',
                'No CPU/audio/scanout memory traffic. GPU fences and ring backpressure delay all subsequent producer events.',
                'Ring words are staged contiguously immediately before each measured publication; per-word CPU issue timing and polling instruction overhead are approximated.',
                'Raw sampled envelope includes initial/next-frame clears; timings are not exact device frame boundaries.',
                'Timed run checks complete color/fence output; nontrivial depth is qualified separately by the original selected replay.']))

def vector(label,frame,dest):
    source=T/label/'gpuvec'/f'frame_{frame:05}.vec';data=source.read_bytes();p=0;out=[];pubs=[];removed=0
    while p<len(data):
        start=p;tag=data[p:p+4];p+=4;keep=True
        if tag in [b'MEM1',b'TEX1']:
            a,n=struct.unpack_from('<II',data,p);p+=8+((n+3)&~3)
            if tag==b'MEM1' and a<0x300000:
                assert a+n<=0x300000;keep=False;removed+=n
        elif tag==b'CMDS':
            n=struct.unpack_from('<I',data,p)[0];p+=4;pubs.append(list(struct.unpack_from('<'+'I'*n,data,p)));p+=4*n
        elif tag==b'TLUT':p+=32768
        elif tag==b'PALB':p+=4
        elif tag in [b'MMIO',b'DUMP']:p+=8
        elif tag==b'RAND':p+=12
        elif tag in [b'FLSH',b'FENC',b'HASH']:pass
        elif tag==b'END.':assert p==len(data)
        else:raise AssertionError((tag,p))
        assert p<=len(data)
        if keep:out.append(data[start:p])
    trace=json.loads((T/f'{label}-frame-{frame:05}.json').read_text());events=trace['events']
    assert pubs==[e['words'] for e in events if e['kind']==4]
    dest.write_bytes(b''.join(out));origin=trace['summary']['capture_start']
    selected=[e for e in events if e['kind'] in [4,5,7]]
    assert selected[-1]['kind']==4
    dest.with_suffix('.schedule').write_text(''.join(f"{e['kind']} {e['cycle']-origin} {e['a']}\n" for e in selected))
    put(dest.with_suffix('.inputs.json'),dict(raw_sha256=sha(source),vector_sha256=sha(dest),
        schedule_sha256=sha(dest.with_suffix('.schedule')),trace_sha256=sha(T/f'{label}-frame-{frame:05}.json'),
        all_command_batches_exact=True,removed_cpu_framebuffer_bytes=removed))

def run(label):
    prep=json.loads((O/'prepared.json').read_text());assert sha(Path(__file__))==prep['recipe_sha256']
    assert sha(O/'timed_gpu')==prep['binary_sha256'];D=O/label;D.mkdir(exist_ok=False)
    for f in [20,80,180,300]:vector(label,f,D/f'frame_{f:05}.vec')
    def one(args):
        frame,scale=args;stem=D/f'frame_{frame:05}-scale-{scale}';vec=D/f'frame_{frame:05}.vec'
        cmd=[str(O/'timed_gpu'),'+fast_tex=1','+tagged_tex=1','+memory_traffic=0']
        if scale:cmd+=['+schedule='+str(vec.with_suffix('.schedule')),'+producer_scale='+str(scale)]
        cmd+=[str(vec),str(stem.with_suffix('.bin'))]
        with stem.with_suffix('.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=300)
        output=stem.with_suffix('.bin');log=stem.with_suffix('.log').read_text();stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',log)}
        result=dict(passed=True,frame=frame,scale=scale,command=cmd,output_sha256=sha(output),stats=stats)
        put(stem.with_suffix('.json'),result);return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(one,[(f,s) for f in [20,80,180,300] for s in [0,1.0,0.5]]))
    for f in [20,80,180,300]:
        group=[x for x in rows if x['frame']==f];assert len({x['output_sha256'] for x in group})==1,f
    put(D/'result.json',dict(passed=True,frames=rows,all_schedules_full_outputs_identical=True,limits=prep['limits']))
    print(json.dumps([dict(frame=x['frame'],scale=x['scale'],cycles=x['stats']['cycles'],
        idle_wait=x['stats']['idle_wait'],fence_wait=x['stats']['fence_wait']) for x in rows],indent=2))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run']);a.add_argument('--label',default='thorough_combined6');n=a.parse_args()
    if n.action=='prepare':prepare()
    else:run(n.label)
