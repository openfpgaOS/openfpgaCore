#!/usr/bin/env python3
"""Replay complete application mesh/fallback/ordinary command traces.

The independent baseline resolves each job from the MEM image at its submission
position, then uses integer vertex/clip/packet references. It also cross-checks
the uploaded ISA program output. Real captures retain mutable-page snapshots,
ordinary draw/material/clear commands and completion fences. GPU/physical SDRAM
cycles are measured; functional CPU trace time is not coupled CPU RTL timing.
"""
import argparse,collections,json,re,shutil,struct,subprocess,sys
from pathlib import Path
from check_gpu_replay import vector_records,SOURCE,sha
from check_whole_gpu import packets,commands
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
sys.path.insert(0,str(HERE.parent/'client'));sys.path.insert(0,str(HERE.parent/'kernel'));sys.path.insert(0,str(HERE.parent/'engine'))
from check_mesh_program import expected_stream
from reference import decode_inputs,prepared_packet
from emulator import run

def prepare(source,dest):
    memory=bytearray(1<<26);present=bytearray(1<<26);baseline=bytearray();mesh=bytearray();program=[0]*768
    counts=collections.Counter();output_ranges=[];output_offset=0;code_sha=[]
    depth_bases=set()
    def track_depth(material):
        assert len(material)==17
        if material[12]==640 and material[13]==2:depth_bases.add(material[11]&0x3ffffff)
    def read_words(address,count):
        physical=address&0x3ffffff;size=count*4
        assert address%4==0 and physical+size<=len(memory)
        assert all(present[physical:physical+size]),('missing DMA snapshot',hex(address),count)
        return list(struct.unpack_from('<%dI'%count,memory,physical))
    for tag,record in vector_records(source.read_bytes()):
        counts['records_'+tag.decode()]+=1
        if tag==b'HASH':
            # Retain the source framebuffer dump and add explicit depth dumps,
            # so depth is compared byte-for-byte as well as in the full hash.
            for address in sorted(depth_bases):
                extra=b'DUMP'+struct.pack('<II',address,153600);baseline+=extra;mesh+=extra
                output_ranges.append(dict(kind='depth_dump',address=address,offset=output_offset,length=153600));output_offset+=153600
        mesh+=record
        if tag==b'MEM1':
            address,length=struct.unpack_from('<II',record,4);address&=0x3ffffff
            assert address+length<=len(memory);memory[address:address+length]=record[12:12+length];present[address:address+length]=b'\1'*length
        if tag==b'RAND':raise AssertionError('RAND reference image update not implemented')
        if tag!=b'CMDS':
            baseline+=record
            if tag==b'DUMP':
                address,length=struct.unpack_from('<II',record,4);output_ranges.append(dict(kind='dump',address=address,offset=output_offset,length=length));output_offset+=length
            if tag==b'FENC':output_ranges.append(dict(kind='fence',offset=output_offset,length=4));output_offset+=4
            if tag==b'HASH':output_ranges.append(dict(kind='hash',offset=output_offset,length=8));output_offset+=8
            continue
        n=struct.unpack_from('<I',record,4)[0];words=list(struct.unpack_from('<%dI'%n,record,8));converted=[]
        for packet in packets(words):
            opcode=packet[0]>>24;counts[f'opcode_{opcode:02x}']+=1
            if opcode==0x63:
                entry=packet[1];assert entry+len(packet)-2<=768
                program[entry:entry+len(packet)-2]=packet[2:];code_sha.append(sha_bytes(struct.pack('<%dI'%(len(packet)-2),*packet[2:])))
                continue
            if opcode!=0x62:
                if opcode==0x4a:track_depth(packet[1:])
                converted+=packet;continue
            assert len(packet)==8
            _,va,vw,ia,iwmeta,sa,sw,entry=packet;iw=iwmeta&65535;nt=iwmeta>>16 or iw
            raw=read_words(va,vw);indices=read_words(ia,iw);state=read_words(sa,sw)
            bank=[0]*256;bank[:vw]=raw;bank[192:192+iw]=indices;bank[128:128+sw]=state
            scratch=[0]*256;scratch[62]=vw//4;scratch[63]=nt
            if entry==0:
                assert sw==64 and vw%4==0 and iw==(nt+1)//2
                track_depth(state[24:41])
                triangles=[(indices[i//2]>>(16*(i%2)))&32767 for i in range(nt)]
                job=dict(header=[0,va,ia,vw//4,nt,0,0,0],vertices=raw,indices=triangles)
                expected,_,draws=expected_stream(job,state);counts['whole_jobs']+=1;counts['whole_vertices']+=vw//4;counts['whole_triangles']+=nt
            else:
                assert vw==24
                vertices,clip,options=decode_inputs(raw);expected=prepared_packet(vertices,clip,**options);draws=bool(expected);counts['projected_fallback_jobs']+=1
            actual=run(program,bank=bank,scratch=scratch,entry=entry,compact=True)
            assert actual['fault']==0 and actual['output']==expected,('reference/ISA disagreement',dict(counts),entry,len(expected),len(actual['output']))
            counts['emulated_engine_cycles']+=actual['cycles'];counts['prepared_draws']+=draws;counts['prepared_words']+=len(expected)
            converted+=expected
        baseline+=commands(converted)
    (dest/'baseline.vec').write_bytes(baseline);(dest/'mesh.vec').write_bytes(mesh)
    return dict(counts=dict(counts),source_sha256=sha(source),baseline_sha256=sha(dest/'baseline.vec'),mesh_sha256=sha(dest/'mesh.vec'),upload_sha256=code_sha,output_ranges=output_ranges)

def sha_bytes(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--capture',type=Path,required=True)
    ap.add_argument('--build-dir',type=Path,required=True);ap.add_argument('--frame',type=int,action='append');ap.add_argument('--traffic',type=int,default=1)
    a=ap.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=True);results=[]
    frozen=out/'generator';frozen.mkdir(exist_ok=True)
    for p in [Path(__file__),HERE/'check_gpu_replay.py',HERE/'check_whole_gpu.py',*HERE.parent.glob('client/*.py'),*HERE.parent.glob('engine/*.py'),*HERE.parent.glob('kernel/*.py')]:shutil.copy2(p,frozen/p.name)
    exes=dict(baseline=SOURCE/'obj/Vtb_gpu',mesh=a.build_dir.resolve()/'obj/Vtb_gpu')
    for frame in a.frame or [360]:
        dest=out/f'frame-{frame}';dest.mkdir(exist_ok=False);source=a.capture/f'frame_{frame:05d}.vec';info=prepare(source,dest);cycles={}
        (dest/'inputs.json').write_text(json.dumps(info,indent=2)+'\n')
        for label,exe in exes.items():
            with (dest/(label+'.log')).open('w') as f:subprocess.run([str(exe),str(dest/(label+'.vec')),str(dest/(label+'.bin')),f'+memory_traffic={a.traffic}'],stdout=f,stderr=subprocess.STDOUT,check=True,timeout=1200)
            text=(dest/(label+'.log')).read_text();cycles[label]=int(re.search(r'RESULT cycles=(\d+)',text)[1])
        before=(dest/'baseline.bin').read_bytes();after=(dest/'mesh.bin').read_bytes()
        assert before==after,(frame,'full output mismatch',len(before),len(after),sum(x!=y for x,y in zip(before,after)))
        counters={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'MESH (.+)',(dest/'mesh.log').read_text())[1])}
        row=dict(frame=frame,traffic=a.traffic,exact_memory_color_and_fence=True,cycles=cycles,counters=counters,output_sha256=sha(dest/'mesh.bin'),**info)
        (dest/'result.json').write_text(json.dumps(row,indent=2)+'\n');results.append(row);print(json.dumps({k:v for k,v in row.items() if k not in ['output_ranges']}),flush=True)
    (out/'result.json').write_text(json.dumps(dict(passed=True,scope=__doc__,build_dir=str(a.build_dir.resolve()),executable_sha256={k:sha(p) for k,p in exes.items()},results=results),indent=2)+'\n')
if __name__=='__main__':main()
