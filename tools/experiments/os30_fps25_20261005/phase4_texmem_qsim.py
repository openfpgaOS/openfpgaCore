#!/usr/bin/env python3
"""Isolated functional CRAM1 capture extension, not a CPU/GPU timing model."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/texmem-capture'
BASE=ROOT/'docs/measurements/os30-simple-20261005/validation/qsim-observed'
REF=ROOT/'docs/measurements/os30-fps25-20261005/validation/capture-phase2-exact-v1-fps'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()


def prepare():
    assert sha(BASE/'qsim')=='509cfbb58a1e5544f269e41dea58d36db4eff703c3d4ffe26a08579ffb27ef0d'
    OUT.mkdir(parents=True,exist_ok=True);q=OUT/'qsim';q.mkdir(exist_ok=False)
    for p in BASE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    def replace(n,a,b):
        p=q/n;s=p.read_text();assert s.count(a)==1,(n,a);p.write_text(s.replace(a,b))
    replace('gpu.c','    uint32_t palookup_base;         /* 26 bits */',
        '''    /* Private functional fast-texture model. Timing belongs to RTL replay. */
    uint8_t fast_tex[1u << 24];
    uint32_t fast_tex_addr, fast_tex_used, fast_tex_route;
    uint32_t palookup_base;         /* 26 bits */''')
    replace('gpu.c','/* gpu_tex_cache.v: addr[3:0] byte in line, addr[13:4] set, addr[25:14] tag.',
        '''static inline uint8_t tex_rd(gpu_t *g, uint32_t a) {
    return g->fast_tex_route ? g->fast_tex[a & 0x00ffffffu] : mem_rd(g,a);
}
static inline uint8_t tex_peek(gpu_t *g, uint32_t a) {
    return g->fast_tex_route ? g->fast_tex[a & 0x00ffffffu] : mem_peek(g,a);
}
/* gpu_tex_cache.v: addr[3:0] byte in line, addr[13:4] set, addr[25:14] tag.''')
    replace('gpu.c','        return mem_rd(g, a);','        return tex_rd(g, a);')
    replace('gpu.c','l->data[a & 0xF] != mem_peek(g, a)','l->data[a & 0xF] != tex_peek(g, a)')
    replace('gpu.c','l->data[i] = mem_rd(g, line + (uint32_t)i);','l->data[i] = tex_rd(g, line + (uint32_t)i);')
    replace('gpu.c','    case 0x0C: g->dma_src = ADDR26(value); break;',
        '''    case 0x08: g->fast_tex_addr = value & 0x003fffffu; break;
    case 0x38: g->fast_tex_route = value & 1u; break;
    case 0x3C: {
        uint32_t a=g->fast_tex_addr*4u;
        for(unsigned i=0;i<4;i++)g->fast_tex[a+i]=(uint8_t)(value>>(8*i));
        if(a+4>g->fast_tex_used)g->fast_tex_used=a+4;
        g->fast_tex_addr=(g->fast_tex_addr+1)&0x003fffffu;
        break;
    }
    case 0x0C: g->dma_src = ADDR26(value); break;''')
    replace('gpu.c','uint32_t gpu_fence_reached(const gpu_t *g) { return g->fence_reached; }',
        '''const uint8_t *gpu_fast_tex_data(const gpu_t *g) { return g->fast_tex; }
uint32_t gpu_fast_tex_used(const gpu_t *g) { return g->fast_tex_used; }
uint32_t gpu_fast_tex_route(const gpu_t *g) { return g->fast_tex_route; }
uint32_t gpu_fast_tex_addr(const gpu_t *g) { return g->fast_tex_addr; }
uint32_t gpu_fence_reached(const gpu_t *g) { return g->fence_reached; }''')
    replace('gpu.h','uint32_t gpu_fence_reached(const gpu_t *g);',
        '''const uint8_t *gpu_fast_tex_data(const gpu_t *g);
uint32_t gpu_fast_tex_used(const gpu_t *g);
uint32_t gpu_fast_tex_route(const gpu_t *g);
uint32_t gpu_fast_tex_addr(const gpu_t *g);
uint32_t gpu_fence_reached(const gpu_t *g);''')
    replace('env.c','    E->vec_n = 0;\n    E->vec_frame = E->flips;',
        '''    /* Existing texture residency is testbench preload; subsequent MMIO
     * writes retain CPU ordering and are replayed through the real upload path. */
    uint32_t ft_used=gpu_fast_tex_used(E->gpu);
    if(ft_used || gpu_fast_tex_route(E->gpu)) {
        if(ft_used) {
            vec_u32(E,GV_TAG('T','E','X','1'));vec_u32(E,0);vec_u32(E,ft_used);
            fwrite(gpu_fast_tex_data(E->gpu),1,ft_used,E->vec_f);
        }
        vec_u32(E,GV_TAG('M','M','I','O'));vec_u32(E,0x08);vec_u32(E,gpu_fast_tex_addr(E->gpu));
        vec_u32(E,GV_TAG('M','M','I','O'));vec_u32(E,0x38);vec_u32(E,gpu_fast_tex_route(E->gpu));
    }
    E->vec_n = 0;
    E->vec_frame = E->flips;''')
    # Exactly this switch belongs to the vector recording adapter.
    replace('env.c','    switch (off) {\n    case 0x04:',
        '''    switch (off) {
    case 0x08: case 0x38: case 0x3C:
        vec_u32(E,GV_TAG('M','M','I','O'));vec_u32(E,off);vec_u32(E,val);
        break;
    case 0x04:''')
    with (OUT/'build.log').open('w') as f:
        subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    (OUT/'manifest.json').write_text(json.dumps(dict(parent_simulator_sha256=sha(BASE/'qsim'),simulator_sha256=sha(q/'qsim'),
        script_sha256=sha(Path(__file__)),sources={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'},
        scope=__doc__,limits='Upload busy returns idle: functional/capture only. Actual async-write timing/concurrent fills are validated in the separate real-controller RTL harness.'),indent=2)+'\n')


def capture(label,elf):
    p=ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py'
    spec=importlib.util.spec_from_file_location('texmem_capture',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.OUT=OUT
    sys.argv=[str(Path(__file__)),label,'--elf',str(elf),'--exe',str(OUT/'qsim/qsim'),'--geometry','0','--audio']
    m.main()
    if label=='f5-control':
        dest=OUT/('capture-'+label);identical={}
        files=[REF/n for n in ['frames.csv','scene.csv','geometry.csv','diagnostic.csv','culling.csv']]
        files+=list(REF.glob('profile*.tsv'))+list((REF/'gpuvec').glob('*'))
        for p in files:
            if p.is_file():
                rel=p.relative_to(REF);assert sha(p)==sha(dest/rel),str(rel);identical[str(rel)]=sha(p)
        (OUT/'f5-noninterference.json').write_text(json.dumps(dict(passed=True,elf_sha256=sha(elf),identical_results=identical),indent=2)+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','capture']);p.add_argument('--label');p.add_argument('--elf',type=Path);a=p.parse_args()
    if a.action=='prepare':prepare()
    else:
        assert a.label and a.elf
        capture(a.label,a.elf)
