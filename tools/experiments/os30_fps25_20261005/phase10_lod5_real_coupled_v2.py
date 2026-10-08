#!/usr/bin/env python3
"""Actual LOD5 client, full16MiB CRAM, actual LOAD+GPU+memory; no source remap."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
HERE=Path(__file__).resolve().parent;P=CAMP/'phase10'
OUT=P/'lod5-real-coupled-v2';OLD=P/'span-nextchunk-coupled-v1/combined'
RTL=P/'lod5-components-v1/frozen';FUNC=P/'load-cram-functional-v1'
def rep(t,a,b):
 assert t.count(a)==1,(a[:100],t.count(a));return t.replace(a,b)
def prepare():
 OUT.mkdir(exist_ok=False);F=OUT/'model/frozen';Q=OUT/'qsim'
 shutil.copytree(OLD/'model/frozen',F);shutil.copytree(OLD/'qsim',Q,ignore=shutil.ignore_patterns('build','qsim'))
 for n in ('phase10_load_alias.v','geometry_raw_load.v','phase10_raw_vtx_dma.v','phase10_cram1_read_arbiter.v'):
  target='geometry_cram1_read_arbiter.v'if n=='phase10_cram1_read_arbiter.v'else n
  shutil.copy2(RTL/n,F/'common'/target)
 shutil.copy2(P/'cram1-share-full-v1/frozen/cram_chip_model.v',F/'cram_chip_model.v')
 p=F/'texture_memory.sv';p.write_text(rep(p.read_text(),'.bd_word_addr(21\'b0)',".bd_word_addr(22'b0)"))
 p=F/'tb_gpu.v';t=p.read_text();t=rep(t,'geometry_cram1_read_arbiter source_arbiter(\n .clk(clk),.reset_n(reset_n),','geometry_cram1_read_arbiter source_arbiter(\n .clk(clk),.reset_n(reset_n),.s0_reset(1\'b0),')
 t=rep(t,'''always @(posedge clk) if(reset_n&&gd_cram_route&&c1_up_wr&&c1_up_addr>=22'h40000)
 $fatal(1,"Texture upload overlaps reserved geometry pool");''','''always @(posedge clk) if(reset_n&&c1_up_wr&&c1_up_addr>=22'h3f0000&&view_block)
 $fatal(1,"Source upload overlaps active LOAD");
always @(posedge clk) if(reset_n&&c1_arvalid&&c1_araddr[23:0]>=24'hfc0000)
 $fatal(1,"Texture fetch overlaps reserved source pool");''')
 p.write_text(t)
 # Before actual RTL handoff, run the same functional LOD5 path, including
 # real source uploads and CPU cache table population, never absent warmup.
 shutil.copy2(FUNC/'functional-qsim/load_model.h',Q/'load_model.h')
 p=Q/'env.c';t=p.read_text();t=rep(t,'static int on_mmio_read(', '#include "load_model.h"\nstatic void lod5_cbo(void *ctx,qsim_cpu *cpu,unsigned op,uint32_t addr) {if(!coupled_active())lm_cbo(ctx,cpu,op,addr);}\n\nstatic int on_mmio_read(')
 t=rep(t,'        *val=coupled_active()?coupled_gd_read(addr-0x4b000000u):0;', '        if(!coupled_active())return lm_read(E,addr-0x4b000000u,size,val);\n        *val=coupled_gd_read(addr-0x4b000000u);')
 t=rep(t,'        if(coupled_active())coupled_gd_write(addr-0x4b000000u,val);','        if(!coupled_active())return lm_write(E,addr-0x4b000000u,size,val);\n        coupled_gd_write(addr-0x4b000000u,val);')
 t=rep(t,'    cpu->env.mmio_write = on_mmio_write;','    cpu->env.mmio_write = on_mmio_write;\n    cpu->env.cbo=lod5_cbo;');p.write_text(t)
 p=F/'coupled.cpp';t=p.read_text().replace('#include "source_pool.h"\n','').replace('static const SourceRange *source_selected;\n','')
 t=rep(t,'    assert(!(address&3) && address+4<=0x200000);\n    const auto &m=tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;\n    return uint32_t(m[address>>1])|(uint32_t(m[(address>>1)+1])<<16);','''    assert(!(address&3) && address+4<=0x1000000);
    const auto &m=address&0x800000 ? tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem1 : tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    unsigned h=(address&0x7fffff)>>1;
    return uint32_t(m[h])|(uint32_t(m[h+1])<<16);''')
 t=rep(t,'    source_cram_route=getenv("CRAM_VERTEX_ROUTE")&&atoi(getenv("CRAM_VERTEX_ROUTE"));','    source_cram_route=true; // Physical LOD5 always reads CRAM byte offsets.')
 a=t.index('    uint32_t used=gpu_fast_tex_used(E->gpu);');b=t.index('    reg_write(2,gpu_fast_tex_addr(E->gpu));',a)
 t=t[:a]+'''    uint32_t used=gpu_fast_tex_used(E->gpu);assert(used<=0x1000000 && !(used&1));
    const uint8_t *tex=gpu_fast_tex_data(E->gpu);
    for(unsigned a=0;a<used;a+=2) {
        auto &cm=a&0x800000 ? tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem1 : tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
        cm[(a&0x7fffff)>>1]=uint16_t(tex[a])|(uint16_t(tex[a+1])<<8);
    }
''' +t[b:]
 t=t.replace('    // Warmup advertised no device, so every CPU slot remains materialized.','    // Warmup used the real client with functional LOD5, so all source uploads\n    // and cache entries exist. CPU slots remain materialized after each LOAD.')
 a=t.index('    if(off==12) {');b=t.index('    if(off==16)',a)
 t=t[:a]+'    if(off==12)source_original=value; // Actual CRAM offset, never remapped.\n'+t[b:]
 a=t.index('        assert(source_selected&&source_count');b=t.index('\n    }\n    tick();tb->gd_reg_wr=0;',a)
 t=t[:a]+'''        assert(source_count&&source_original>=0xfc0000&&source_original+24*source_count<=0x1000000);
        const auto *cache=qsim_syms_lookup(&env->syms,"g_cram_vertex_source");assert(cache);
        const uint32_t *entries=(const uint32_t *)(memory()+cache->addr-env->cpu->sdram_base);
        uint32_t original_address=0;
        for(unsigned j=0;j<1024;++j)if(entries[j*2]&&entries[j*2+1]==(source_original|(source_count<<24))) {
            assert(!original_address);original_address=entries[j*2];
        }
        assert(original_address);
        for(unsigned i=0;i<source_count*6;++i) {
            uint32_t original;memcpy(&original,memory()+(original_address-env->cpu->sdram_base)+4*i,4);
            assert(original==cram_word(source_original+4*i));
        }''' +t[b:]
 t=rep(t,'static uint32_t source_original,source_count;','static uint32_t source_original,source_count,source_light;')
 t=rep(t,'frame,operation,start,finish,cycles,status,accepted,live,reason,words\\n','frame,operation,start,finish,cycles,status,accepted,live,reason,words,count,lit\\n')
 t=rep(t,'%llu,%u,%llu,%llu,%llu,%u,0,0,0,0\\n','%llu,%u,%llu,%llu,%llu,%u,0,0,0,0,%u,%u\\n')
 t=rep(t,'(unsigned long long)cycles,(unsigned long long)(cycles-gd_start_cycle),value);','(unsigned long long)cycles,(unsigned long long)(cycles-gd_start_cycle),value,source_count,source_light);')
 t=rep(t,'    if(off==16)source_count=value&63u;','    if(off==16)source_count=value&63u;\n    if(off==48)source_light=value&1u;')
 p.write_text(t)
 (F/'source_pool.h').unlink()
 cmd=[x.replace(str(OLD),str(OUT))for x in json.loads((OLD/'build-command.json').read_text())]
 write(OUT/'build-command.json',cmd)
 write(OUT/'prepared.json',dict(private_only=True,source_remap=False,source_preload='Only actual functional warmup bytes, copied at idle handoff; subsequent uploads traverse actual MMIO/controller.',parent=str(OLD),sources={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},helper_sha256=sha(Path(__file__))))
def build():
 with(OUT/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((OUT/'build-command.json').read_text())
 with(OUT/'model/build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run(label,start=360,frames=8):
 m=module('lod5_real_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
 elf=P/'load-cram-driver-v1/app.elf'if label.startswith('candidate')else CAMP/'phase9/checks-state-f11/app.elf'
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),label,'--elf',str(elf),'--start',str(start),'--frames',str(frames)];m.main()
 assert 'FATAL'not in(OUT/('run-'+label)/'run.log').read_text()
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);p.add_argument('--label',choices=['candidate','baseline','candidate-cold','baseline-cold'],default='candidate');p.add_argument('--start',type=int,default=360);p.add_argument('--frames',type=int,default=8);a=p.parse_args();run(a.label,a.start,a.frames)if a.action=='run'else globals()[a.action]()
