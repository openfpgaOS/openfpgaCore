#!/usr/bin/env python3
"""Passive F5 texture-upload lifetime capture; never modifies guest behavior."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase4/texture-lifetime'
OLD = ROOT / 'docs/measurements/os30-simple-20261005/validation/qsim-observed'
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/validation/capture-phase2-exact-v1-fps'
ELF = ROOT / 'docs/measurements/os30-fps25-20261005/phase2/exact-v1/diagnostic.elf'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, data):
    p.write_text(json.dumps(data, indent=2) + '\n')


OBSERVER = r'''
typedef struct {
    FILE *out; const size_t *frame;
    uint32_t active, entries[2], gt_sym, tmu_sym, count_sym, count;
    uint32_t tex, width, height, bytes, source;
} texture_observer;
static uint32_t texture_word(qsim_cpu *cpu, uint32_t addr) {
    const void *p=qsim_mem_ptr(cpu,addr,4);uint32_t v;if(!p)abort();memcpy(&v,p,4);return v;
}
static void observe_texture(qsim_cpu *cpu) {
    texture_observer *o=cpu->observe_context;
    if(!o->active) {
        uint32_t tmu=texture_word(cpu,o->tmu_sym);if(tmu>1)abort();
        o->tex=texture_word(cpu,o->gt_sym+4*tmu);
        o->source=cpu->x[10];o->width=cpu->x[11];o->height=cpu->x[12];
        if(!o->width || !o->height || o->width>4096 || o->height>4096)abort();
        o->bytes=o->width*o->height*2;
        o->active=1;cpu->observe_pcs[0]=cpu->x[1];cpu->observe_pcs[1]=0xffffffffu;
        return;
    }
    uint32_t addr=texture_word(cpu,o->tex+48);
    uint32_t dims=texture_word(cpu,o->tex+40); /* height is low halfword. */
    uint32_t cur_word=texture_word(cpu,o->tex+36);
    if((cur_word>>16)!=o->width || (dims&0xffffu)!=o->height ||
       texture_word(cpu,o->tex)!=addr)abort();
    const void *pixels=qsim_mem_ptr(cpu,addr,o->bytes);if(!pixels)abort();
    uint32_t h[10]={0x34584554u,(uint32_t)*o->frame,o->count++,o->tex,addr,
        o->width,o->height,o->bytes,o->source,texture_word(cpu,o->count_sym)};
    if(fwrite(h,1,sizeof h,o->out)!=sizeof h || fwrite(pixels,1,o->bytes,o->out)!=o->bytes)abort();
    o->active=0;cpu->observe_pcs[0]=o->entries[0];cpu->observe_pcs[1]=o->entries[1];
}
'''


def prepare():
    assert sha(OLD/'qsim') == '509cfbb58a1e5544f269e41dea58d36db4eff703c3d4ffe26a08579ffb27ef0d'
    OUT.mkdir(parents=True, exist_ok=True)
    q=OUT/'qsim';q.mkdir(exist_ok=False)
    for p in OLD.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    def replace(n,a,b):
        p=q/n;s=p.read_text();assert s.count(a)==1,(n,a);p.write_text(s.replace(a,b,1))
    replace('cpu.h','    /* architectural state */',
        '    uint32_t observe_pcs[2];\n    void (*observe)(struct qsim_cpu *);\n    void *observe_context;\n    /* architectural state */')
    line='#define INSN() do { '+chr(92)+'\n'
    replace('cpu_exec.h',line+'        size_t _i',line+
        '        if (unlikely(cpu->observe != NULL && (e->pc == cpu->observe_pcs[0] || e->pc == cpu->observe_pcs[1]))) cpu->observe(cpu); '+chr(92)+'\n        size_t _i')
    replace('main.c','#include "lint.h"','#include "lint.h"\n'+OBSERVER)
    replace('main.c','    size_t cap_rows = 4096, nrows = 0;',r'''
    size_t cap_rows = 4096, nrows = 0;
    const qsim_sym *upload=qsim_syms_lookup(&E.syms,"gpu_upload_texture");
    const qsim_sym *rgba16=qsim_syms_lookup(&E.syms,"gpu_upload_texture_rgba16");
    const qsim_sym *gt=qsim_syms_lookup(&E.syms,"g_cur_tex");
    const qsim_sym *tmu=qsim_syms_lookup(&E.syms,"g_cur_tmu");
    const qsim_sym *tc=qsim_syms_lookup(&E.syms,"g_tex_count");
    if(!upload || upload->addr!=0x104d84c0u || !rgba16 || rgba16->addr!=0x104d8280u || !gt || !tmu || !tc)abort();
    snprintf(path,sizeof path,"%s/texture-uploads.bin",out);
    texture_observer texture_obs={.out=fopen(path,"wb"),.frame=&nrows,
        .entries={upload->addr,rgba16->addr},.gt_sym=gt->addr,.tmu_sym=tmu->addr,.count_sym=tc->addr};
    if(!texture_obs.out)abort();
    E.cpu->observe_pcs[0]=upload->addr;E.cpu->observe_pcs[1]=rgba16->addr;
    E.cpu->observe=observe_texture;E.cpu->observe_context=&texture_obs;
''')
    replace('main.c','    if(culling_csv)fclose(culling_csv);',
        '    if(culling_csv)fclose(culling_csv);\n    if(texture_obs.active)abort();fclose(texture_obs.out);\n')
    with (OUT/'build.log').open('w') as f:
        subprocess.run(['make','-j3'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'observer.json',dict(parent_sha256=sha(OLD/'qsim'),simulator_sha256=sha(q/'qsim'),
        script_sha256=sha(Path(__file__)),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'}))


def capture():
    p=ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py'
    spec=importlib.util.spec_from_file_location('texture_capture',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.OUT=OUT
    sys.argv=[str(Path(__file__)),'f5','--elf',str(ELF),'--exe',str(OUT/'qsim/qsim'),'--geometry','0','--audio']
    m.main()
    dest=OUT/'capture-f5';verified={}
    files=[BASE/n for n in ['frames.csv','scene.csv','geometry.csv','diagnostic.csv','culling.csv']]
    files+=list(BASE.glob('profile*.tsv'))+list((BASE/'gpuvec').glob('*'))
    for p in files:
        if p.is_file():
            rel=p.relative_to(BASE);assert sha(p)==sha(dest/rel),str(rel);verified[str(rel)]=sha(p)
    write(OUT/'noninterference.json',dict(passed=True,elf_sha256=sha(ELF),identical_results=verified,
        observer_manifest_sha256=sha(OUT/'observer.json'),capture_inputs_sha256=sha(dest/'inputs.json')))


def analyze():
    raw=(OUT/'capture-f5/texture-uploads.bin').read_bytes();pos=0;rows=[];contents={};slots={};peak=0
    while pos<len(raw):
        h=struct.unpack_from('<10I',raw,pos);pos+=40
        magic,frame,ordinal,tex,addr,w,ht,size,source,count=h
        assert magic==0x34584554 and size==w*ht*2 and pos+size<=len(raw)
        pixels=raw[pos:pos+size];pos+=size;digest=hashlib.sha256(pixels).hexdigest()
        previous=contents.get(digest)
        if previous is not None:assert previous==pixels
        contents[digest]=pixels
        slots[addr]=max(slots.get(addr,0),(size+63)&~63);peak=max(peak,sum(slots.values()))
        rows.append(dict(frame=frame,ordinal=ordinal,texture_struct=tex,gpu_address=addr,width=w,height=ht,
            bytes=size,source_address=source,allocated_ids=count,pixel_sha256=digest,seen_same_pixels=previous is not None))
    assert pos==len(raw)
    periods=[]
    for first,last in [(0,721),(360,721)]:
        sample=[r for r in rows if first<=r['frame']<last]
        byframe={f:sum(r['bytes'] for r in sample if r['frame']==f) for f in range(first,last)}
        periods.append(dict(first_frame=first,last_frame_exclusive=last,calls=len(sample),
            uploaded_bytes=sum(r['bytes'] for r in sample),frames_with_uploads=sum(bool(n) for n in byframe.values()),
            max_bytes_in_frame=max(byframe.values()),mean_bytes_per_frame=sum(byframe.values())/(last-first)))
    report=dict(uploads=rows,periods=periods,unique_content_count=len(contents),unique_content_bytes=sum(map(len,contents.values())),
        distinct_sdram_slots=len(slots),sum_max_rounded_bytes_per_distinct_address=peak,
        scope='Exact post-upload RGB565 bytes from original F5 guest, no guest mutation. Covers both texture upload APIs only; excludes alpha-mask/white allocation and unvisited levels. Distinct-address high-water is not an allocator lifetime proof.',
        capture_sha256=sha(OUT/'capture-f5/texture-uploads.bin'),noninterference_sha256=sha(OUT/'noninterference.json'))
    write(OUT/'results.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='uploads'},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','capture','analyze']);a=p.parse_args()
    globals()[a.action]()
