#!/usr/bin/env python3
"""Verify old PROJ and fused PROJA against the existing independent projection oracle."""
from pathlib import Path
import collections,hashlib,json,random,shutil,struct,subprocess
HERE=Path(__file__).resolve().parent;KERNEL=HERE.parent/'kernel'
OUT=HERE.parents[3]/'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow/proja-unit-v1'
OUT.mkdir(exist_ok=False);rng=random.Random(0x3051);counts=collections.Counter()
with (OUT/'vectors.bin').open('wb') as f:
    source=(KERNEL/'results/project-rtl/vectors.bin').read_bytes()
    for row in struct.iter_unpack('<18I',source):
        for mode in (0,1):
            flags=rng.getrandbits(2)<<16;d=rng.randrange(65536);c=rng.randrange(65536)
            du,dv=rng.getrandbits(32),rng.getrandbits(32);expected=list(row[11:])
            if mode:
                expected[1]=(expected[1]+du)&0xffffffff;expected[2]=(expected[2]+dv)&0xffffffff
                expected[6]=(expected[5]&65535) if flags&(1<<17) else d
                if flags&(1<<16):expected[5]=(expected[5]&0xff0000)|c
            f.write(struct.pack('<24I',*row[:11],flags,d,c,du,dv,*expected,mode));counts['cases']+=1
tb=(KERNEL/'project_tb.v').read_text().replace('cancel,start,host_we,','cancel,start,host_we,material_mode,')
tb=tb.replace(".material_mode(1'b0)",'.material_mode(material_mode)')
(OUT/'project_tb.v').write_text(tb)
cpp=(KERNEL/'project_test.cpp').read_text().replace('record[18]','record[24]').replace('fread(record,4,18,f)==18','fread(record,4,24,f)==24')
cpp=cpp.replace('            dut.start=1;tick();dut.start=0;',
    '            write(53,record[11]);write(54,record[12]);write(55,record[13]);\n'
    '            write(60,record[14]);write(61,record[15]);dut.material_mode=record[23];\n'
    '            dut.start=1;tick();dut.start=0;',1)
cpp=cpp.replace('record[11+i]','record[16+i]').replace('delay<75','delay<90')
(OUT/'project_test.cpp').write_text(cpp)
shutil.copy2(KERNEL/'gpu_mesh_project.v',OUT/'gpu_mesh_project.v')
command=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','project_tb',
         '--Mdir',str(OUT/'obj'),str(OUT/'project_tb.v'),str(OUT/'gpu_mesh_project.v'),str(OUT/'project_test.cpp'),'-CFLAGS','-std=c++17 -O2']
with (OUT/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
with (OUT/'run.log').open('w') as f:subprocess.run([str(OUT/'obj/Vproject_tb'),str(OUT/'vectors.bin')],stdout=f,stderr=subprocess.STDOUT,check=True)
report=dict(counts=counts,result=(OUT/'run.log').read_text(),sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.glob('*') if p.is_file()})
(OUT/'result.json').write_text(json.dumps(report,indent=2)+'\n');print(report['result'])
