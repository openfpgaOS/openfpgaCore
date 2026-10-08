#!/usr/bin/env python3
"""Whole mesh program through actual pruned RTL with real material captures.

Each supported job is compared against independent integer transform/clip/
projection/prepared-packet reference. Includes randomized output backpressure,
every final bank word, and exact non-stalled instruction cycles. This excludes
the scene's fallback draws and raster/DMA timing, so it is not game FPS proof.
"""
from pathlib import Path
import argparse,collections,hashlib,json,shutil,struct,subprocess
from mesh_program import build,HERE
from mesh_transform import OUT
from check_mesh_program import expected_stream
from material_cases import cases
from emulator import run
from check_microcode import records

def main():
    p=argparse.ArgumentParser();p.add_argument('--label',default='whole-mesh-rtl-v1');p.add_argument('--limit',type=int,default=0)
    p.add_argument('--projected',action='store_true')
    a=p.parse_args();dest=OUT/a.label;dest.mkdir(exist_ok=False);frozen=dest/'frozen';frozen.mkdir()
    program=build();code=program.finish();counts=collections.Counter();details=[]
    with (dest/'vectors.bin').open('wb') as f:
        for frame,ordinal,status,job,bank,state in cases():
            counts[f'material_status_{status}']+=1
            if status:continue
            expected,vertices,draws=expected_stream(job,state)
            scratch=[0]*256;scratch[62],scratch[63]=job['header'][3:5]
            result=run(code,bank=bank,scratch=scratch,compact=True)
            assert result['output']==expected and not result['fault'],(frame,ordinal)
            assert result['scratch'][63]==0
            f.write(struct.pack('<4I',len(code),len(expected),result['cycles'],0))
            for values in (code,expected,bank,scratch,result['bank']):
                f.write(struct.pack('<%dI'%len(values),*values))
            counts['jobs']+=1;counts['vertices']+=len(vertices);counts['triangles']+=len(job['indices'])
            counts['words']+=len(expected);counts['draws']+=draws;counts['cycles']+=result['cycles']
            details.append(dict(frame=frame,ordinal=ordinal,sequence=job['header'][0],cycles=result['cycles'],draws=draws))
            if a.limit and counts['jobs']>=a.limit:break
        if a.projected:
            entry=program.labels['projected_fallback']
            for path in sorted((HERE.parent/'kernel/results/reference').glob('*/kernel.bin')):
                for ordinal,(inputs,expected) in enumerate(records(path)):
                    bank=[0]*256;bank[:24]=inputs;scratch=[0xa55a0000|i for i in range(256)]
                    result=run(code,bank=bank,scratch=scratch,entry=entry,compact=True)
                    assert not result['fault'] and result['output']==expected,(path.name,ordinal)
                    f.write(struct.pack('<4I',len(code),len(expected),result['cycles'],entry))
                    for values in (code,expected,bank,scratch,result['bank']):
                        f.write(struct.pack('<%dI'%len(values),*values))
                    counts['projected_cases']+=1;counts['projected_words']+=len(expected)
                    counts['projected_cycles']+=result['cycles']
    cpp=(HERE.parent/'engine/engine_test.cpp').read_text()
    old='            reset();bank.fill(0);upload(code);'
    new='''            reset();
            require(fread(bank.data(),4,256,f)==256,"bank read");
            std::array<uint32_t,256> scratch,final_bank;
            require(fread(scratch.data(),4,256,f)==256,"scratch read");
            require(fread(final_bank.data(),4,256,f)==256,"expected bank read");
            for(unsigned i=0;i<256;i++) {
                dut.scratch_we=1;dut.scratch_addr=i;dut.scratch_data=scratch[i];tick();
            }
            dut.scratch_we=0;upload(code);'''
    assert cpp.count(old)==1;cpp=cpp.replace(old,new)
    cpp=cpp.replace('unsigned limit,bool stalls){','unsigned limit,bool stalls,unsigned entry=0){')
    cpp=cpp.replace('    dut.entry=0;dut.start=1;','    dut.entry=entry;dut.start=1;')
    cpp=cpp.replace('execute(expected,h[2]+10000,true);','execute(expected,h[2]+10000,true,h[3]);')
    cpp=cpp.replace('            words+=expected.size();++count;',
                    '            require(bank==final_bank,"final bank differs");\n            words+=expected.size();++count;')
    (frozen/'whole_mesh_test.cpp').write_text(cpp)
    sources=[*HERE.parent.glob('engine/gpu_mesh*.v'),*HERE.parent.glob('kernel/gpu_mesh*.v'),
             HERE.parent/'dma/gpu_mesh_vertex.v',HERE.parent/'dma/gpu_mesh_attributes.v']
    for source in sources:shutil.copy2(source,frozen/source.name)
    for source in (Path(__file__),HERE/'mesh_program.py',HERE/'mesh_transform.py',HERE/'check_mesh_program.py',
                   HERE/'material_cases.py',HERE.parent/'engine/isa.py',HERE.parent/'engine/emulator.py',HERE.parent/'kernel/microcode.py'):
        shutil.copy2(source,frozen/source.name)
    command=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','gpu_mesh_engine',
             '-GCOMPACT_LAYOUT=1','-GEXTENDED_ISA=0','-GENABLE_DOT=0','--Mdir',str(dest/'obj'),
             *[str(x) for x in frozen.glob('*.v')],str(frozen/'whole_mesh_test.cpp'),'-CFLAGS','-std=c++17 -O2']
    with (dest/'build.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (dest/'run.log').open('w') as log:subprocess.run([str(dest/'obj/Vgpu_mesh_engine'),str(dest/'vectors.bin')],stdout=log,stderr=subprocess.STDOUT,check=True)
    report=dict(scope=__doc__,counts=dict(counts),program_words=len(code),details=details,
                result=(dest/'run.log').read_text(),sources={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in frozen.iterdir()})
    (dest/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('sources','details')},indent=2))
if __name__=='__main__':main()
