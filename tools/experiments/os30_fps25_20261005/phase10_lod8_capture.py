#!/usr/bin/env python3
"""Native-FMA functional proof of the private persistent dense36 COW client."""
import argparse,os,shutil,subprocess
from pathlib import Path
from phase10_experiment import CAMP,module,sha,write
H=Path(__file__).resolve().parent;P=CAMP/'phase10'
OUT=P/'lod8-cow-functional-v1';CAND=P/'lod8-cow-driver-v1'
PARENT=P/'lod5-recycle-functional-v1'
def rep(t,a,b):
    assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def prepare():
    OUT.mkdir(exist_ok=False);q=OUT/'functional-qsim';q.mkdir()
    for n in ('app.elf','gfx_pc.c'):shutil.copy2(CAND/n,OUT/n)
    for p in(PARENT/'functional-qsim').iterdir():
        if p.suffix in('.c','.h')or p.name=='Makefile':shutil.copy2(p,q/p.name)
    p=q/'load_model.h';t=p.read_text()
    t=rep(t,'uint32_t delay,result_status,post_mask,pre[32];','uint32_t delay,result_status,post_mask,post_expected,first,valid_slots,pre[32];')
    t=rep(t,'uint64_t polls,cbos,pre_checks,post_checks,timeout_injections;','uint64_t polls,cbos,pre_checks,post_checks,timeout_injections,alias_reads;')
    start=t.index('static void lm_cbo(');end=t.index('static void lm_finish(',start)
    t=t[:start]+'''static void lm_cbo(void *ctx,qsim_cpu *cpu,unsigned op,uint32_t addr) {
    (void)cpu;load_model_state *m=&load_model;if(op)return;
    if(addr<0x13f80000u||addr>=0x13f80800u)return;
    if(m->phase==1){fatal(ctx,"LOD8 alias invalidated while active");abort();}
    if(m->phase==2) {
        uint32_t bit=1u<<((addr-0x13f80000u)/64u);
        if((addr&63)||!(m->post_expected&bit)) {
            fatal(ctx,"LOD8 bad alias invalidation range");abort();
        }
        m->post_mask|=bit;m->post_checks++;
        if(m->post_mask==m->post_expected)m->phase=0;
    }
}
/* Called only for CPU alias accesses, including the executor's fast path.
 * Source bytes are immutable between LOADs; any CPU write is a COW defect. */
void lod8_alias_access(void *ctx,uint32_t off,unsigned size,unsigned store) {
    load_model_state *m=&load_model;
    if(store){fatal(ctx,"LOD8 CPU write to read-only alias");abort();}
    if(m->phase==1){fatal(ctx,"LOD8 alias read while LOAD active");abort();}
    if(off+size>0x3f80480u){fatal(ctx,"LOD8 canonical alias read outside slots");abort();}
    if(m->phase==2) {
        uint32_t mask=1u<<((off-0x3f80000u)/64u);
        mask|=1u<<((off+size-1-0x3f80000u)/64u);
        if(mask&m->post_expected&~m->post_mask){fatal(ctx,"LOD8 alias read before post-invalidate");abort();}
    }
    unsigned first=(off-0x3f80000u)/36u,last=(off+size-1-0x3f80000u)/36u;
    if(!(m->valid_slots&(1u<<first))||!(m->valid_slots&(1u<<last))) {
        fatal(ctx,"LOD8 selected an invalid persistent slot");abort();
    }
    m->alias_reads++;
}
'''+t[end:]
    start=t.index('    for(unsigned i=0;i<rows;i++) {');end=t.index('    m->status=m->result_status',start)
    t=t[:start]+'''    const uint32_t changed=(m->count==32?0xffffffffu:((1u<<m->count)-1u))<<m->first;
    m->valid_slots&=~changed;
    if(rows)m->valid_slots|=changed;
    for(unsigned i=0;i<m->count;i++) {
        uint32_t off=m->output+(m->first+i)*36-E->cpu->sdram_base;
        uint8_t *target=E->lint&&E->lint->strict?E->lint->C+off:gp(E,E->cpu->sdram_base+off,36);
        if(rows)memcpy(target,m->pending[i],36);else memset(target,0,36);
        if(E->lint)for(unsigned b=0;b<36;b++)lint_gpu_wr(E->lint,off+b);
    }
    unsigned first_line=(m->first*36)/64,last_line=((m->first+m->count)*36+63)/64;
    m->post_expected=((1u<<last_line)-1u)^((1u<<first_line)-1u);
'''+t[end:]
    t=rep(t,'unsigned first=0;','unsigned first=(m->regs[0x10/4]>>8)&31;m->first=first;')
    t=rep(t,'!gp(E,m->output,count*32)','!gp(E,m->output+first*36,count*36)')
    t=t.replace('0x4c4f4405','0x4c4f4408')
    # Keep environment/output names compatible with existing capture/analyzer.
    t=t.replace('Functional LOD5 native-FMA','Functional LOD8 native-FMA')
    p.write_text(t)
    p=q/'cpu.h';t=p.read_text();t+='\nvoid lod8_alias_access(void *ctx,uint32_t offset,unsigned size,unsigned store);\n';p.write_text(t)
    p=q/'cpu_exec.h';t=p.read_text()
    for prefix,args,call in [('LINT_CA','o, sz, st','st'),('LINT_UC_RD','o, sz','0'),('LINT_UC_WR','o, sz','1')]:
        lines=t.splitlines(True);found=0
        for i,line in enumerate(lines):
            begin='#define '+prefix+'('+args+')'
            if line.startswith(begin):
                expr=line[len(begin):].strip();found+=1
                lines[i]=begin+' (((uint32_t)((o)-0x03f80000u)<2048u ? lod8_alias_access(cpu->env.ctx,(o),(sz),('+call+')) : (void)0), '+expr+')\n'
        assert found==2;(t:=''.join(lines))
    p.write_text(t)
    with(OUT/'build.log').open('x')as f:subprocess.run(['make','-j4'],cwd=q,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'prepared.json',dict(elf_sha256=sha(OUT/'app.elf'),parent_manifest_sha256=sha(PARENT/'manifest.json'),sources={p.name:sha(p)for p in q.iterdir()if p.suffix in('.c','.h')or p.name=='Makefile'},simulator_sha256=sha(q/'qsim'),helper_sha256=sha(Path(__file__)),limits='Native-FMA functional math and persistent ownership only; synthetic2-poll latency. CPU alias writes, active reads, invalid slots and reads before rounded CBO trap. No RTL or physical FPS claim.'))
def action(name,mode):
    m=module('lod8_capture',H/'phase10_load_cram_capture.py');m.OUT=OUT;m.CAND=CAND
    if name=='capture' and mode!='baseline':os.environ['LOD5_RECYCLE_STATS_ADDR']=hex(m.symbols(CAND/'app.elf')['cram_vertex_recycle_stats'])
    getattr(m,name)(mode)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','capture','analyze']);p.add_argument('--mode',choices=['baseline','normal','faults','absent','timeout','fullpool'],default='normal');a=p.parse_args();prepare()if a.action=='prepare'else action(a.action,a.mode)
