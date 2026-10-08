#!/usr/bin/env python3
"""Functional LOD3 MMIO replay; no hardware latency/FPS inference."""
import argparse
import csv
import os
from pathlib import Path
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write,verify_capture,canonical

HERE=Path(__file__).resolve().parent
OUT=CAMP/'phase10/load-driver-v1'
SOURCE=CAMP/'phase5/qsim'

def prepare():
    q=OUT/'functional-qsim';q.mkdir(exist_ok=False)
    for p in SOURCE.iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':(q/p.name).write_bytes(p.read_bytes())
    def change(name,old,new):
        p=q/name;s=p.read_text();assert s.count(old)==1,(name,old);p.write_text(s.replace(old,new))
    (q/'load_model.h').write_bytes((HERE/'phase10_load_driver_model.h').read_bytes())
    anchor='static int on_mmio_read(void *ctx, qsim_cpu *cpu, uint32_t addr, unsigned size, uint32_t *val)'
    change('env.c',anchor,'#include "load_model.h"\n\n'+anchor)
    anchor='''    qenv *E = ctx;
    if (addr - ENV_GPU_BASE < 0x40) {'''
    p=q/'env.c';s=p.read_text();assert s.count(anchor)==2
    s=s.replace(anchor,'''    qenv *E = ctx;
    if(addr-0x4b000000u<0x1000)return lm_read(E,addr-0x4b000000u,size,val);
    if (addr - ENV_GPU_BASE < 0x40) {''',1)
    pos=s.index('static int on_mmio_write(')
    before,after=s[:pos],s[pos:];assert after.count(anchor)==1
    after=after.replace(anchor,'''    qenv *E = ctx;
    if(addr-0x4b000000u<0x1000)return lm_write(E,addr-0x4b000000u,size,val);
    if (addr - ENV_GPU_BASE < 0x40) {''')
    p.write_text(before+after)
    anchor='    cpu->env.mmio_write = on_mmio_write;'
    change('env.c',anchor,anchor+'\n    cpu->env.cbo = lm_cbo;')
    p=q/'env.h';p.write_text(p.read_text()+'\nvoid env_load_report(qenv *E,const char *out);\n')
    with (OUT/'app.elf').open('rb') as f:
        symbols={s.name:s['st_value'] for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
    anchor='    env_free(&E);'
    change('main.c',anchor,f'''    env_load_report(&E,out);
    snprintf(path,sizeof path,"%s/lod3-driver-stats.bin",out);
    FILE *lodstats=fopen(path,"wb");if(!lodstats)abort();
    fwrite(qsim_mem_ptr(E.cpu,{symbols['load_driver_stats']}u,64),64,1,lodstats);fclose(lodstats);
'''+anchor)
    with (OUT/'functional-build.log').open('x') as log:subprocess.run(['make','-j4'],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
    write(OUT/'functional-model.json',dict(elf_sha256=sha(OUT/'app.elf'),helper_sha256=sha(Path(__file__)),simulator_sha256=sha(q/'qsim'),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'},functional_only=True,arithmetic='qsim exact IEEE follows F11 native instruction DAG; retained-A FMA RTL separately validated',latency='Two status reads, or injected delay for timeout; no DMA or bus timing model'))

def capture(mode):
    os.environ['LOD3_MODEL_MODE']=mode
    m=module('lod3_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),mode,'--elf',str(OUT/'app.elf'),'--exe',str(OUT/'functional-qsim/qsim'),'--geometry','0','--audio'];m.main()

def analyze(mode):
    before=CAMP/'phase9/checks-state-f11/capture-normal';after=OUT/('capture-'+mode)
    a=verify_capture(before,sha(before/'sm64.elf'));b=verify_capture(after,sha(OUT/'app.elf'))
    assert len(a)==len(b)==721
    exact={n:sha(before/n)==sha(after/n) for n in ('scene.csv','geometry.csv','culling.csv')};assert all(exact.values()),exact
    assert [r['gpu_cmd_words'] for r in a]==[r['gpu_cmd_words'] for r in b]
    v=module('lod3_vector_validation',HERE/'phase7_validate.py');changes=[]
    for path in sorted((before/'gpuvec').glob('*.vec')):
        target=after/'gpuvec'/path.name;old,new=(v.v.vector(p.read_bytes()) for p in (path,target))
        assert canonical(old)[0]==canonical(new)[0],path.name
        assert all(old[k]==new[k] for k in ('depth','fence','uploads','hardware')),path.name
        assert len(old['texture'])==len(new['texture'])
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex()) for i in range(0,len(old['texture']),4) if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]),texture_changes=diffs,publication_boundaries_exact=True,baseline_sha256=sha(path),candidate_sha256=sha(target)))
    assert len(changes)==8
    import json,struct
    result=dict(passed=True,mode=mode,functional_only=True,all721_scene_geometry_culling_exact=exact,all721_wordcounts_exact=True,all8_ordered_gpu_mmio_publication_streams_exact=True,
        padding_check=v.padding(changes,before,after,OUT/'SM64'),vectors=changes,model=json.loads((after/'lod3-model.json').read_text()),driver_stats=list(struct.unpack('<16I',(after/'lod3-driver-stats.bin').read_bytes())),
        capture_sha256=sha(after/'inputs.json'),elf_sha256=sha(OUT/'app.elf'),source_sha256=sha(OUT/'gfx_pc.c'),simulator_sha256=sha(OUT/'functional-qsim/qsim'),qualified_for_deployment=False,
        scope='Functional driver/eligibility/fallback/ownership sequence only; modeled busy delay is synthetic. No FPS, DMA bandwidth, cache miss latency, or real MMIO cost inference.')
    write(OUT/f'functional-{mode}.json',result)
    print(json.dumps({k:result[k] for k in ('passed','mode','model','driver_stats')},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',choices=['normal','absent','faults','timeout'],default='normal');a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='capture':capture(a.mode)
    else:analyze(a.mode)
