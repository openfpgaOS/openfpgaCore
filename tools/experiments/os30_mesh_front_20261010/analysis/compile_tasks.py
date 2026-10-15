#!/usr/bin/env python3
"""Own exact immutable task inputs and verify source-bound command decoding.

This establishes packet/lifetime/order semantics, not arithmetic eligibility or
speed. Per-instance transform/material snapshots are joined by passive tracing.
"""
from pathlib import Path
from dataclasses import dataclass
import collections,ctypes,hashlib,json,random,struct,subprocess
H=Path(__file__).resolve().parent;P=H.parents[1];Q=P/'queued-front-v1'
SRC=Q/'work/sm64-v2/src/sm64/sm64/src/pc/gfx/gfx_pc.c'
S=Q/'fixture-v2/capture/run-reference/snapshot'
BASE=0x10000000
sha=lambda b:hashlib.sha256(b).hexdigest()
events=json.loads((H/'coverage-frame360-events.json').read_text())
spans=json.loads((H/'coverage-frame360-spans.json').read_text())['tight']
memory=(S/'sdram.bin').read_bytes()
def take(m,a,n):
    if not (BASE<=a<=BASE+len(m)-n):raise ValueError('address bounds')
    return bytes(m[a-BASE:a-BASE+n])
def tri_decode(a,b):
    op=a>>24
    if op not in (5,6):return []
    return [tuple(((w>>shift)&255)//2 for shift in (16,8,0)) for w in ((a,) if op==5 else (a,b))]
@dataclass(frozen=True)
class Task:
    start:int
    next:int
    first:int
    count:int
    source:int
    commands:bytes
    vertices:bytes
    triangles:tuple
def compile_task(m,start,end):
    if start&7 or end<=start or (end-start)&7:raise ValueError('span bounds')
    assert json.loads((H/'layout-v2.json').read_text())['fields']['Vtx_size']==24
    body=take(m,start,end-start);a,b=struct.unpack_from('<II',body)
    if a>>24!=1:raise ValueError('must begin LOAD')
    count=a>>12&255;first=(a>>1&127)-count
    if not count or first<0 or first>=32 or count>32-first:raise ValueError('LOAD shape')
    if b&3:raise ValueError('vertex alignment')
    vertices=take(m,b,count*24);triangles=[]
    for off in range(8,len(body),8):
        x,y=struct.unpack_from('<II',body,off);ts=tri_decode(x,y)
        if not ts:raise ValueError('state/list/LOAD boundary')
        # Validate the whole command before appending either TRI2 member.
        if any(not first<=v<first+count for t in ts for v in t):raise ValueError('incoming slot reference')
        triangles.extend(ts)
    if not triangles:raise ValueError('empty task')
    return Task(start,end,first,count,b,body,vertices,tuple(triangles))
eligible=[s for s in spans if s['raw_eligible']]
tasks=[compile_task(memory,s['start'],s['end']) for s in eligible]
assert len(tasks)==319
# The exact current app's standalone decoder is separately compiled. No copied
# reimplementation is used as the second command-decoding oracle.
src=SRC.read_text();begin=src.index('static unsigned gfx_geometry_decode(')
end=src.index('\nstatic void gfx_geometry_submit(',begin)
decoder=src[begin:end]
c=H/'task_decoder.c';lib=H/'task_decoder.so'
c.write_text('#include <stdint.h>\n#define F3DEX_GBI_2 1\n#define F3DEX_GBI 1\n#define G_TRI1 5\n#define G_TRI2 6\ntypedef struct { struct { uint32_t w0,w1; } words; } Gfx;\n'+decoder+'\nunsigned task_decode(uint32_t a,uint32_t b,unsigned *out){ Gfx c={{a,b}};return gfx_geometry_decode(&c,out);}\n')
cmd=['gcc','-shared','-fPIC','-O2',str(c),'-o',str(lib)]
r=subprocess.run(cmd,capture_output=True,text=True);(H/'task-decoder-build.log').write_text(r.stdout+r.stderr);r.check_returncode()
d=ctypes.CDLL(str(lib)).task_decode;d.argtypes=[ctypes.c_uint32,ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint)];d.restype=ctypes.c_uint
checks=0
def check_decode(a,b):
    global checks
    out=(ctypes.c_uint*6)();n=d(a,b,out);expected=tri_decode(a,b)
    assert n==len(expected) and list(out)[:3*n]==[v for t in expected for v in t];checks+=1
for task in tasks:
    for off in range(8,len(task.commands),8):check_decode(*struct.unpack_from('<II',task.commands,off))
rng=random.Random(0x719cb115)
for n in range(20000):check_decode((rng.choice([1,5,6,0xde,0xdf,0xda])<<24)|rng.randrange(1<<24),rng.randrange(1<<32))
# Linearized source command order includes original list markers. Expanding
# immutable task records must reproduce every observed command exactly.
out=[];plan=[];j=0
task_by_start=collections.defaultdict(collections.deque)
for task in tasks:task_by_start[task.start].append(task)
while j<len(events):
    e=events[j];q=task_by_start.get(e['address'])
    if e['opcode']==1 and q:
        task=q.popleft();n=len(task.commands)//8
        assert [(x['address'],x['w0'],x['w1']) for x in events[j:j+n]]==[(task.start+off,*struct.unpack_from('<II',task.commands,off)) for off in range(0,len(task.commands),8)]
        plan.append({'kind':'task','task_id':len([p for p in plan if p['kind']=='task']),'start':task.start,'next':task.next,'command_sha256':sha(task.commands),'vertex_sha256':sha(task.vertices)})
        out.extend((task.start+off,*struct.unpack_from('<II',task.commands,off)) for off in range(0,len(task.commands),8));j+=n
    else:
        plan.append({'kind':'original','address':e['address'],'w0':e['w0'],'w1':e['w1']});out.append((e['address'],e['w0'],e['w1']));j+=1
assert out==[(e['address'],e['w0'],e['w1']) for e in events]
assert not any(task_by_start.values())
# Detached bytes survive producer mutation and reuse. Only this private buffer
# is modified; the original snapshot/ELF and every earlier artifact are readonly.
mutable=bytearray(memory);owned=compile_task(mutable,tasks[0].start,tasks[0].next)
original=(owned.commands,owned.vertices,owned.triangles)
mutable[owned.start-BASE:owned.next-BASE]=b'\xff'*len(owned.commands)
mutable[owned.source-BASE:owned.source-BASE+len(owned.vertices)]=b'\xa5'*len(owned.vertices)
assert (owned.commands,owned.vertices,owned.triangles)==original
negative={}
def bad(name,words):
    m=bytearray(memory);at=tasks[0].start
    b=b''.join(struct.pack('<II',*w) for w in words);m[at-BASE:at-BASE+len(b)]=b
    try:compile_task(m,at,at+len(b))
    except ValueError as e:negative[name]=str(e)
    else:raise AssertionError(name)
w0,w1=struct.unpack_from('<II',tasks[0].commands)
bad('state_boundary',[(w0,w1),(0xd9000000,0)])
bad('list_call_boundary',[(w0,w1),(0xde000000,tasks[0].start)])
bad('second_load_boundary',[(w0,w1),(w0,w1)])
bad('zero_vertices',[(0x01000000,w1),(0x05000000,0)])
bad('destination_overflow',[(0x01020002,w1),(0x05000000,0)])
bad('empty',[(w0,w1)])
bad('tri2_second_half_outside_owned',[(0x01003006,w1),(0x06000204,0x003e3e3e)])
vectors=[]
for i,(task,span) in enumerate(zip(tasks,eligible)):
    vectors.append({'id':i,'span_start':task.start,'next_pointer':task.next,'source_address':task.source,'count':task.count,'destination':task.first,'commands_u32':list(struct.unpack('<'+'I'*(len(task.commands)//4),task.commands)),'source_vertex_bytes_hex':task.vertices.hex(),'reference_indices':[list(t) for t in task.triangles],'geometry_mode':span['geometry_mode'],'raw_eligibility_only':True,'instance_context_pending':'Exact matrix/light/prepared material/viewport and transformed exceptions must be joined from passive native trace; no math eligibility claim.'})
(H/'task-vectors-frame360.json').write_text(json.dumps({'format':'mesh-input-v2','vertex_stride_bytes':24,'frame':360,'vectors':vectors},indent=2)+'\n')
(H/'task-plan-frame360.json').write_text(json.dumps(plan,separators=(',',':'))+'\n')
inputs=[Path(__file__).resolve(),H/'layout-v2.json',SRC,S/'sdram.bin',H/'coverage-frame360-spans.json',H/'coverage-frame360-events.json',c,lib,H/'task-vectors-frame360.json',H/'task-plan-frame360.json']
result={'status':'PASS_IMMUTABLE_TASK_PACKET_AND_SOURCE_DECODER','tasks':len(tasks),'triangles':sum(len(t.triangles) for t in tasks),'vertices':sum(t.count for t in tasks),'decode_checks':checks,'flattened_command_order_checks':len(events),'immutable_mutation_checks':3,'negative_cases':negative,'compile_command':cmd,'extracted_decoder_sha256':sha(decoder.encode()),'inputs_sha256':{str(p):sha(p.read_bytes()) for p in inputs},'limitations':['Arithmetic, materials, viewport state and transformed clipping/equal-W coverage await task-local passive trace.','Expansion preserves the exact flattened original CPU command sequence including state/list markers; this is not a GPU packet or cycle-accurate execution proof.','Task data are immutable owned byte strings in this model. A real DMA implementation needs explicit asset lifetime or owned pool retention until completion.']}
(H/'task-packet-proof.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['status','tasks','triangles','decode_checks','flattened_command_order_checks','negative_cases']},indent=2))
