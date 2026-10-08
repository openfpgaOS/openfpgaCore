#!/usr/bin/env python3
"""Native-FMA functional LOD4 driver proof; synthetic completion, no FPS claim."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP,ROOT,module,sha,write,verify_capture,canonical

HERE=Path(__file__).resolve().parent
CANDIDATE=CAMP/'phase10/load-alias-driver-v1'
OUT=CAMP/'phase10/load-alias-functional-v2'
BASE=CAMP/'phase9/checks-state-f11'
FMA=CAMP/'phase10/qsim-fma26-v1'

def replace(t,a,b):
    assert t.count(a)==1,(a,t.count(a))
    return t.replace(a,b)

def model():
    t=(HERE/'phase10_load_driver_model.h').read_text()
    t=t[t.index('#include "fpu.h"'):]
    start=t.index('static void lm_cbo(');end=t.index('static void lm_finish(',start)
    t=t[:start]+r'''static void lm_cbo(void *ctx,qsim_cpu *cpu,unsigned op,uint32_t addr) {
    (void)cpu;load_model_state *m=&load_model;if(op)return;
    if(addr<0x13f80000u||addr>=0x13f80400u)return;
    if(m->phase==1){fatal(ctx,"LOD4 alias invalidated while active");return;}
    if(m->phase==2) {
        unsigned lines=(m->count+1)/2;
        if((addr&63)||addr>=0x13f80000u+lines*64u) {
            fatal(ctx,"LOD4 bad alias invalidation range");return;
        }
        m->post_mask|=1u<<((addr-0x13f80000u)/64);m->post_checks++;
        if(m->post_mask==((1u<<lines)-1))m->phase=0;
    }
}
''' +t[end:]
    start=t.index('    unsigned rows=');end=t.index('    if(m->status&4)m->success++;',start)
    t=t[:start]+r'''    unsigned rows=(m->result_status&4)?m->count:0;
    for(unsigned i=0;i<rows;i++) {
        uint32_t raw[8],*q=m->pending[i];uint8_t flags=0;
        memcpy(raw,q,5*4);
        raw[5]=(uint16_t)qf_to_i32(q[5],1,&flags)|((uint32_t)(uint16_t)qf_to_i32(q[6],1,&flags)<<16);
        raw[6]=q[7];raw[7]=q[8];
        uint32_t off=m->output+i*32-E->cpu->sdram_base;
        uint8_t *target=E->lint&&E->lint->strict?E->lint->C+off:gp(E,m->output+i*32,32);
        memcpy(target,raw,32);
        if(E->lint)for(unsigned b=0;b<32;b++)lint_gpu_wr(E->lint,off+b);
    }
    m->status=m->result_status|2u|0x100u;
    m->phase=rows?2:0;m->post_mask=0;
''' +t[end:]
    t=replace(t,'m->output=m->regs[0x40/4];','m->output=0x13f80000u;')
    t=replace(t,'    unsigned first=m->regs[0x10/4]>>8&31;','    unsigned first=0;')
    t=replace(t,'!gp(E,src,count*24)||!gp(E,m->output,count*64)','!gp(E,src,count*24)||!gp(E,m->output,count*32)')
    a=t.index('    if(m->pre_count<count)');b=t.index('    int bad=0;',a)
    t=t[:a]+'    m->jobs++;m->vertices+=count;\n'+t[b:]
    t=replace(t,'        else if(m->jobs%17==0)m->result_status=0x400;\n','')
    # Unlike result DMA into CPU memory, completed scratch results can be
    # abandoned after a drained timeout. Only a still-active DMA is unsafe.
    t=replace(t,'if(m->phase)fatal(E,"LOD3 capture ends with unreleased ownership");',
              'if(m->phase==1)fatal(E,"LOD3 capture ends with active DMA");')
    t=t.replace('LOD3','LOD4').replace('lod3','lod4').replace('0x4c4f4403','0x4c4f4404')
    return '/* Functional LOD4 native-FMA model. Completion latency is synthetic. */\n'+t

def prepare():
    OUT.mkdir(exist_ok=False)
    for name in ('app.elf','gfx_pc.c'):shutil.copy2(CANDIDATE/name,OUT/name)
    q=OUT/'functional-qsim';q.mkdir(exist_ok=False)
    for p in (CAMP/'phase5/qsim').iterdir():
        if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,q/p.name)
    (q/'load_model.h').write_text(model())
    p=q/'env.c';t=p.read_text()
    anchor='static int on_mmio_read(void *ctx, qsim_cpu *cpu, uint32_t addr, unsigned size, uint32_t *val)'
    t=replace(t,anchor,'#include "load_model.h"\n\n'+anchor)
    anchor='    qenv *E = ctx;\n    if (addr - ENV_GPU_BASE < 0x40) {'
    assert t.count(anchor)==2
    t=t.replace(anchor,'    qenv *E = ctx;\n    if(addr-0x4b000000u<0x1000)return lm_read(E,addr-0x4b000000u,size,val);\n    if (addr - ENV_GPU_BASE < 0x40) {',1)
    t=replace(t,anchor,'    qenv *E = ctx;\n    if(addr-0x4b000000u<0x1000)return lm_write(E,addr-0x4b000000u,size,val);\n    if (addr - ENV_GPU_BASE < 0x40) {')
    t=replace(t,'    cpu->env.mmio_write = on_mmio_write;','    cpu->env.mmio_write = on_mmio_write;\n    cpu->env.cbo = lm_cbo;')
    p.write_text(t)
    p=q/'env.h';p.write_text(p.read_text()+'\nvoid env_load_report(qenv *E,const char *out);\n')
    p=q/'main.c';t=p.read_text();t=replace(t,'    env_free(&E);',r'''    env_load_report(&E,out);
    const char *alias_stats=getenv("LOD4_STATS_ADDR");
    if(alias_stats) {
        snprintf(path,sizeof path,"%s/lod4-driver-stats.bin",out);
        FILE *stats=fopen(path,"wb");if(!stats)abort();
        uint32_t addr=(uint32_t)strtoul(alias_stats,NULL,0);
        fwrite(qsim_mem_ptr(E.cpu,addr,64),64,1,stats);fclose(stats);
    }
    env_free(&E);''');p.write_text(t)
    for name in ('fpu.c','fpu_fast.h'):shutil.copy2(FMA/name,q/name)
    p=q/'Makefile';p.write_text(p.read_text()+'\nCFLAGS += -DQSIM_RETAINED_FMA26\n')
    with (OUT/'functional-build.log').open('x') as f:
        subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'functional-model.json',dict(private_only=True,functional_only=True,
        elf_sha256=sha(OUT/'app.elf'),simulator_sha256=sha(q/'qsim'),
        helper_sha256=sha(Path(__file__)),source_sha256={p.name:sha(p) for p in q.iterdir() if p.suffix in('.c','.h') or p.name=='Makefile'},
        fma_proof_sha256=sha(FMA/'manifest.json'),
        limits=['Synthetic two-poll completion; alias modeled as device-written cached memory, not real BRAM timing.','No physical FPS or full721frame rendered-image proof.']))

def capture(mode):
    baseline=mode=='baseline';elf=BASE/'app.elf' if baseline else OUT/'app.elf'
    os.environ['LOD4_MODEL_MODE']='absent' if baseline else mode
    os.environ.pop('LOD4_STATS_ADDR',None)
    if not baseline:
        with elf.open('rb') as f:
            symbols={s.name:s['st_value'] for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
        os.environ['LOD4_STATS_ADDR']=hex(symbols['load_driver_stats'])
    m=module('lod4_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
    sys.argv=[str(Path(__file__)),mode,'--elf',str(elf),'--exe',str(OUT/'functional-qsim/qsim'),'--geometry','0','--audio'];m.main()

def analyze(mode):
    before=OUT/'capture-baseline';after=OUT/('capture-'+mode)
    a=verify_capture(before,sha(BASE/'app.elf'));b=verify_capture(after,sha(OUT/'app.elf'))
    assert 'FATAL' not in (before/'run.log').read_text()
    assert 'FATAL' not in (after/'run.log').read_text()
    assert len(a)==len(b)==721
    exact={n:sha(before/n)==sha(after/n) for n in ('scene.csv','geometry.csv','culling.csv')};assert all(exact.values()),exact
    assert [r['gpu_cmd_words'] for r in a]==[r['gpu_cmd_words'] for r in b]
    v=module('lod4_vectors',HERE/'phase7_validate.py');vectors=[]
    for p in sorted((before/'gpuvec').glob('*.vec')):
        target=after/'gpuvec'/p.name;old,new=(v.v.vector(x.read_bytes()) for x in(p,target))
        assert canonical(old)[0]==canonical(new)[0],p.name
        assert all(old[k]==new[k] for k in('depth','fence','uploads','hardware')),p.name
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex()) for i in range(0,len(old['texture']),4) if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        vectors.append(dict(frame=int(p.stem.split('_')[1]),texture_changes=diffs,publication_boundaries_exact=True,baseline_sha256=sha(p),candidate_sha256=sha(target)))
    assert len(vectors)==8
    import struct
    result=dict(passed=True,functional_only=True,mode=mode,all721_scene_geometry_culling_exact=exact,
        all721_gpu_wordcounts_exact=True,all8_sampled_streams_exact=True,
        padding_check=v.padding(vectors,before,after,CANDIDATE/'SM64'),vectors=vectors,
        model=json.loads((after/'lod4-model.json').read_text()),
        driver_stats=list(struct.unpack('<16I',(after/'lod4-driver-stats.bin').read_bytes())),
        source_sha256=sha(OUT/'gfx_pc.c'),elf_sha256=sha(OUT/'app.elf'),
        simulator_sha256=sha(OUT/'functional-qsim/qsim'),qualified_for_deployment=False)
    write(OUT/f'functional-{mode}.json',result)
    print(json.dumps({k:result[k] for k in('passed','mode','model','driver_stats')},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','capture','analyze'])
    p.add_argument('--mode',choices=['baseline','normal','absent','faults','timeout'],default='normal')
    a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='capture':capture(a.mode)
    else:analyze(a.mode)
