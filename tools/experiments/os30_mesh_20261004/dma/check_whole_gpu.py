#!/usr/bin/env python3
"""Supported raw SM64 mesh/material/texture fixtures through real GPU+SDRAM.

Compare the complete DMA/shader/clip/project/setup pipeline to independently
prepared packets with identical captured textures and initial memory. Unsupported
jobs are skipped on both sides; this is not the original complete game frame or
CPU/game FPS. Program upload, DMA contention and all emitted raster work count.
"""
import argparse,collections,hashlib,json,re,shutil,struct,subprocess,sys
from pathlib import Path
from check_gpu_replay import build,command_record,SOURCE,sha
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'client'))
from mesh_program import build as microcode
from mesh_transform import OUT,read
from check_mesh_program import expected_stream

def memory_record(address,data):
    return b'MEM1'+struct.pack('<II',address,len(data))+data+b'\0'*((-len(data))&3)

def packets(words):
    pos=0
    while pos<len(words):
        n=1+(words[pos]&8191);assert pos+n<=len(words)
        yield words[pos:pos+n];pos+=n

def commands(words):
    chunk=[];result=bytearray()
    for packet in packets(words):
        assert len(packet)<=4095
        if len(chunk)+len(packet)>4095:result+=command_record(chunk);chunk=[]
        chunk+=packet
    if chunk:result+=command_record(chunk)
    return result

def load_frame(capture,frame):
    stem=f'{frame:05d}';mesh_path=capture/f'mesh-{stem}.bin';material_path=capture/f'material-{stem}.bin';texture_path=capture/f'textures-{stem}.bin'
    jobs=read(mesh_path)[1];raw=material_path.read_bytes();words=struct.unpack('<%dI'%(len(raw)//4),raw)
    assert words[:2]==(0x31544d4d,len(jobs));assert len(words)==2+65*len(jobs)
    supported=[];status=collections.Counter()
    for i,j in enumerate(jobs):
        code=words[2+i*65];state=list(words[3+i*65:67+i*65]);status[code]+=1
        if code==0:
            bank=[0]*256;bank[:len(j['vertices'])]=j['vertices'];bank[128:192]=state
            for n in range(0,len(j['indices']),2):bank[192+n//2]=j['indices'][n]|((j['indices'][n+1] if n+1<len(j['indices']) else 0)<<16)
            expected,_,draws=expected_stream(j,state)
            supported.append(dict(index=i,job=j,state=state,bank=bank,expected=expected,draws=draws))
    tex=texture_path.read_bytes();magic,count=struct.unpack_from('<II',tex);assert magic==0x31584d54;pos=8;textures=[]
    for i in range(count):
        address,length=struct.unpack_from('<II',tex,pos);pos+=8;data=tex[pos:pos+length];pos+=length
        assert len(data)==length;textures.append((address,data))
    assert pos==len(tex)
    return supported,textures,dict(status),{p.name:sha(p) for p in [mesh_path,material_path,texture_path]}

def make_vectors(capture,frame,dest,program,group_jobs=16):
    jobs,textures,status,hashes=load_frame(capture,frame);assert jobs
    base=0x03e00000;end=base+len(jobs)*1024;assert end<=0x03f00000
    ranges=[]
    for row in jobs:
        s=row['state'];assert s[25]==640 and s[26]==2 and s[36]==640 and s[37]==2
        ranges.extend([(s[24]&0x3ffffff,153600),(s[35]&0x3ffffff,153600)])
    ranges=sorted(set(ranges));memory=bytearray()
    for address,length in ranges:
        assert end<=address or base>=address+length
        memory+=memory_record(address,bytes(length))
    tex_ranges=[]
    for address,data in textures:
        physical=address&0x3ffffff;length=len(data);assert physical+length<=0x4000000
        assert end<=physical or base>=physical+length
        for start,size in ranges:assert start+size<=physical or physical+length<=start
        for start,size in tex_ranges:assert start+size<=physical or physical+length<=start
        tex_ranges.append((physical,length));memory+=memory_record(address,data)
    backing=b''.join(struct.pack('<256I',*row['bank']) for row in jobs)
    memory+=memory_record(base,backing)
    baseline=bytearray(memory);mesh=bytearray(memory)
    mesh+=command_record([0x63000000|len(program)+1,0,*program])
    baseline_words=[];mesh_words=[];fences=[]
    for i,row in enumerate(jobs):
        j=row['job'];nv,nt=j['header'][3:5];address=base+i*1024
        baseline_words+=row['expected']
        mesh_words += [0x62000007,address,nv*4,address+768,(nt<<16)|((nt+1)//2),address+512,64,0]
        if (group_jobs and i%group_jobs==group_jobs-1) or i+1==len(jobs):
            token=(frame<<16)|(i+1);fences.append(token)
            baseline+=commands(baseline_words+[0x02000001,token])+b'FENC'
            mesh+=commands(mesh_words+[0x02000001,token])+b'FENC'
            baseline_words=[];mesh_words=[]
    for address,length in ranges:
        record=b'DUMP'+struct.pack('<II',address,length);baseline+=record;mesh+=record
    baseline+=b'HASHEND.';mesh+=b'HASHEND.'
    (dest/'baseline.vec').write_bytes(baseline);(dest/'mesh.vec').write_bytes(mesh)
    return dict(jobs=len(jobs),vertices=sum(row['job']['header'][3] for row in jobs),triangles=sum(row['job']['header'][4] for row in jobs),
                draw_packets=sum(row['draws'] for row in jobs),expected_packet_words=sum(len(row['expected']) for row in jobs),
                skipped_jobs=sum(v for k,v in status.items() if k),material_status=status,textures=len(textures),
                texture_bytes=sum(len(data) for _,data in textures),dump_ranges=ranges,fences=fences,input_address=base,input_bytes=len(backing),
                jobs_per_fence=group_jobs or len(jobs),
                fixture_sha256=hashes,vector_sha256={k:sha(dest/(k+'.vec')) for k in ['baseline','mesh']})

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--capture',type=Path,default=OUT/'material-textures-360-8');ap.add_argument('--frame',action='append',type=int)
    ap.add_argument('--traffic',action='append',type=int);ap.add_argument('--build-dir',type=Path)
    ap.add_argument('--group-jobs',type=int,default=16,help='Jobs between completion fences;0 queues the whole supported frame.')
    ap.add_argument('--fifo-bits',type=int,default=8,choices=[8,9,10],help='Frontend output FIFO size for a fresh build.')
    a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);builddir=a.build_dir.resolve() if a.build_dir else out/'build'
    if not a.build_dir:build(builddir,compact=1,enable_dot=0,extended_isa=0,fifo_bits=a.fifo_bits)
    program=microcode().finish();(out/'program.bin').write_bytes(struct.pack('<%dI'%len(program),*program))
    generator=out/'generator';generator.mkdir(exist_ok=True)
    for p in [Path(__file__),HERE/'check_gpu_replay.py',*HERE.parent.glob('client/*.py'),
              *HERE.parent.glob('engine/*.py'),*HERE.parent.glob('kernel/*.py')]:shutil.copy2(p,generator/p.name)
    results=[];exe=builddir/'obj/Vtb_gpu'
    for frame in a.frame or [360]:
        for traffic in a.traffic or [1]:
            dest=out/f'frame-{frame}-m{traffic}';dest.mkdir(exist_ok=False)
            info=make_vectors(a.capture,frame,dest,program,a.group_jobs);cycles={}
            for label,executable in [('baseline',SOURCE/'obj/Vtb_gpu'),('mesh',exe)]:
                with (dest/(label+'.log')).open('w') as f:
                    subprocess.run([str(executable),str(dest/(label+'.vec')),str(dest/(label+'.bin')),f'+memory_traffic={traffic}'],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1200)
                log=(dest/(label+'.log')).read_text();cycles[label]=int(re.search(r'RESULT cycles=(\d+)',log)[1])
            before=(dest/'baseline.bin').read_bytes();after=(dest/'mesh.bin').read_bytes()
            assert before==after,(frame,traffic,'memory/fence differs',len(before),len(after),sum(x!=y for x,y in zip(before,after)))
            actual_fences=list(struct.unpack_from('<%dI'%len(info['fences']),after));assert actual_fences==info['fences']
            offset=4*len(actual_fences);changed=[]
            for address,length in info['dump_ranges']:
                changed.append(dict(address=address,nonzero_bytes=sum(x!=0 for x in after[offset:offset+length])))
                offset+=length
            assert any(row['nonzero_bytes'] for row in changed), 'fixture generated no memory writes'
            counters={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'MESH (.+)',(dest/'mesh.log').read_text())[1])}
            row=dict(frame=frame,traffic=traffic,exact_memory_and_fence=True,cycles=cycles,counters=counters,
                     baseline_is_precomputed=True,dump_changes=changed,output_sha256=sha(dest/'mesh.bin'),**info)
            (dest/'result.json').write_text(json.dumps(row,indent=2)+'\n');results.append(row);print(json.dumps(row),flush=True)
    report=dict(passed=True,scope=__doc__,program_words=len(program),program_sha256=sha(out/'program.bin'),
                build_dir=str(builddir),executable_sha256={k:sha(v) for k,v in dict(baseline=SOURCE/'obj/Vtb_gpu',mesh=exe).items()},results=results)
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main()
