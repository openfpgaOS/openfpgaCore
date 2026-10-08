#!/usr/bin/env python3
"""Exact PROJ/PROJA with shortened shared division, real samples and boundaries."""
import argparse,collections,hashlib,json,random,shutil,struct,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];BASE=HERE.parent
for part in ['engine','kernel']:sys.path.insert(0,str(BASE/part))
from isa import Program
from emulator import run
from project_reference import project

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);frozen=out/'frozen';frozen.mkdir()
 for source in [*BASE.glob('engine/*.v'),*BASE.glob('kernel/gpu_mesh*.v'),*HERE.glob('gpu_mesh*.v'),BASE/'engine/engine_test.cpp',BASE/'engine/emulator.py',BASE/'engine/isa.py',BASE/'kernel/project_reference.py',Path(__file__)]:shutil.copy2(source,frozen/source.name)
 rows=list(struct.iter_unpack('<18I',(BASE/'kernel/results/project-rtl/vectors.bin').read_bytes()));original=len(rows);rng=random.Random(0x474751)
 ws={0,1,255,256,257,0x7fffffff,0x80000000,0xffffffff}
 for bit in range(32):ws.update(((1<<bit)+d)&0xffffffff for d in [-1,0,1])
 for w in sorted(ws):
  for _ in range(4):
   vertex=[rng.getrandbits(32) for _ in range(7)];vertex[3]=w;vp=[rng.getrandbits(32) for _ in range(4)];rows.append(tuple(vertex+vp+project(vertex,vp)))
 counts=collections.Counter();hist=collections.Counter()
 with (out/'vectors.bin').open('wb') as f:
  for row in rows:
   for mode in [0,1]:
    flags=rng.randrange(4)<<16;cd=rng.randrange(65536);cc=rng.randrange(65536);du=rng.getrandbits(32);dv=rng.getrandbits(32)
    expected=list(row[11:]);pairs=[(64+i,v) for i,v in enumerate(row[:7])]+[(49+i,v) for i,v in enumerate(row[7:11])]+[(53,flags),(54,cd),(55,cc),(60,du),(61,dv)]
    if mode:
     expected[1]=(expected[1]+du)&0xffffffff;expected[2]=(expected[2]+dv)&0xffffffff
     expected[6]=(expected[5]&65535)if flags&(1<<17)else cd
     if flags&(1<<16):expected[5]=(expected[5]&0xff0000)|cc
    p=Program().const(200,64).const(201,232).emit('PROJA' if mode else 'PROJ',a=200,b=201)
    for i in range(232,239):p.emit('OUT',a=i)
    # Preserve all source/context words and prove divider/multiplier ownership returns.
    for i,v in pairs:p.emit('OUT',a=i);expected.append(v)
    p.const(202,0x76543210).const(203,17).emit('MUL',204,202,203).emit('OUT',a=204).emit('DIVU',204,202,203).emit('OUT',a=204).emit('HALT')
    expected.extend([(0x76543210*17)&0xffffffff,0x76543210//17]);code=p.finish();scratch=[0]*256
    for addr,v in pairs:scratch[addr]=v
    result=run(code,scratch=scratch);assert result['fault']==0 and result['output']==expected
    f.write(struct.pack('<4I',len(code),len(expected),result['cycles'],len(pairs)));f.write(struct.pack('<%dI'%len(code),*code));f.write(struct.pack('<%dI'%len(expected),*expected))
    for pair in pairs:f.write(struct.pack('<2I',*pair))
    signed_w=row[3]-(1<<32)if row[3]&0x80000000 else row[3];k=max(256,signed_w).bit_length()-1;hist[k]+=1;counts['programs']+=1;counts['words']+=len(expected);counts['cycles_saved']+=k
 cpp=(frozen/'engine_test.cpp').read_text();old='            reset();bank.fill(0);upload(code);';assert cpp.count(old)==1
 cpp=cpp.replace(old,'''            reset();bank.fill(0);
            for(unsigned i=0;i<h[3];i++){uint32_t pair[2];require(fread(pair,4,2,f)==2,"scratch pair read");dut.scratch_we=1;dut.scratch_addr=pair[0];dut.scratch_data=pair[1];tick();}
            dut.scratch_we=0;upload(code);''')
 # The last fixture leaves valid scratch/context. Exercise cancellation through
 # the PROJA divide and all writeback states, then a fresh ordinary instruction.
 cancellation=Program().const(200,64).const(201,232).const(67,256).emit('PROJA',a=200,b=201).emit('OUT',a=232).emit('HALT').finish()
 old='const std::vector<uint32_t> cancellation={0x0100007f,0x01010003,0x21020001,0x17020000,0x18000200,0};';assert old in cpp
 cpp=cpp.replace(old,'const std::vector<uint32_t> cancellation={'+','.join(hex(x)for x in cancellation)+'};').replace('delay<90','delay<150')
 (frozen/'project_engine_test.cpp').write_text(cpp)
 command=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','gpu_mesh_engine','--Mdir',str(out/'obj'),*[str(p)for p in frozen.glob('*.v')],str(frozen/'project_engine_test.cpp'),'-CFLAGS','-std=c++17 -O2']
 with(out/'build.log').open('w')as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
 with(out/'run.log').open('w')as f:subprocess.run([str(out/'obj/Vgpu_mesh_engine'),str(out/'vectors.bin')],stdout=f,stderr=subprocess.STDOUT,check=True)
 # Separately prove the recurrence for every 32-bit denominator bit position,
 # including values above signed-W range which PROJ itself clamps to256.
 for den in [*range(256,65536),*[max(256,rng.getrandbits(32))for _ in range(65536)],*[max(256,x)for x in ws]]:
  k=den.bit_length()-1;r=1<<(k-1);q=0
  for _ in range(39-k):r<<=1;q<<=1;take=r>=den;q|=take;r-=den if take else 0
  assert q==(1<<38)//den and r==(1<<38)%den
 result=dict(passed=True,original_projection_cases=original*2,boundary_cases=(len(rows)-original)*2,counts=dict(counts),skipped_bits_histogram=dict(hist),result=(out/'run.log').read_text(),sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest()for p in frozen.iterdir()if p.is_file()});(out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
