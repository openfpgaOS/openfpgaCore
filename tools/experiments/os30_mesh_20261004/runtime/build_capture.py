#!/usr/bin/env python3
"""Functional app capture with real mesh capability, program and DMA inputs.

This interpreter records CPU work; raster timing comes from the RTL replay.
FENC records retain CPU completion observations before mutable data reuse.
"""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
p=argparse.ArgumentParser();p.add_argument('--label',default='runtime-qsim-v2');a=p.parse_args()
OUT=ROOT/'docs/measurements/os30-mesh-20261004'/a.label
BASE=ROOT/'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow/qsim'
OUT.mkdir(exist_ok=False)
for p in BASE.iterdir():
    if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,OUT/p.name)
def replace(s,a,b):
    assert s.count(a)==1,(a[:80],s.count(a))
    return s.replace(a,b,1)
p=OUT/'gpu.c';s=p.read_text()
s=replace(s,'| (getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)',
    '| (getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)\n'
    '             | (getenv("SM64_WORKER_CAP") ? 0x800u : 0)\n'
    '             | (getenv("SM64_ATTRIBUTES_CAP") ? 0x1000u : 0)\n'
    '             | (getenv("SM64_MESH_CAP") ? 0x2000u : 0)')
p.write_text(s)
p=OUT/'env.c';s=p.read_text()
s=replace(s,'0x2913775bu',
    '(getenv("SM64_MESH_CAP") ? (0x2913775bu & ~((1u<<20)|(1u<<22)|(1u<<26)|(1u<<29))) : 0x2913775bu)')
s=replace(s,'static void env_gpu_cmd(qenv *E, uint32_t hdr, const uint32_t *p)',
    'static uint32_t mesh_program_capture[770],mesh_program_count;\n'
    'static void env_gpu_cmd(qenv *E, uint32_t hdr, const uint32_t *p)')
s=replace(s,'    uint8_t b[4];\n    b[0] = (uint8_t)hdr;',
    '''    if(op==0x63) {
        if(n>769)abort();
        mesh_program_capture[0]=hdr;memcpy(mesh_program_capture+1,p,n*4);
        mesh_program_count=n+1;
    }
    uint8_t b[4];
    b[0] = (uint8_t)hdr;''')
s=replace(s,'    E->vec_n = 0;\n    E->vec_frame = E->flips;',
    '''    if(mesh_program_count) {
        vec_u32(E,GV_TAG('C','M','D','S'));vec_u32(E,mesh_program_count);
        fwrite(mesh_program_capture,4,mesh_program_count,E->vec_f);
    }
    E->vec_n = 0;
    E->vec_frame = E->flips;''')
s=s.replace('        *val = size == 4 ? v : (v >> ((off & 3) * 8));',
    '''        if((off&~3u)==0x18 && E->vec_f && getenv("SM64_MESH_CAP"))
            vec_u32(E,GV_TAG('F','E','N','C'));
        *val = size == 4 ? v : (v >> ((off & 3) * 8));''',1)
p.write_text(s)
p=OUT/'cache.c';s=p.read_text();s=replace(s,'p->dc_sets = 1024;',
    'p->dc_sets = getenv("MESH_DC64") ? 512 : 1024;');p.write_text(s)
p=OUT/'main.c';s=p.read_text().replace('                mesh_capture(&E,out,fr->seq);','');p.write_text(s)
with (OUT/'build.log').open('w') as log:
    subprocess.run(['make','-j3'],cwd=OUT,stdout=log,stderr=subprocess.STDOUT,check=True)
(OUT/'sources.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
    for p in OUT.iterdir() if p.is_file() and p.suffix in ('.c','.h')},indent=2)+'\n')
print(OUT/'qsim')
