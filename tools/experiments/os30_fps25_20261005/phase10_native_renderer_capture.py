#!/usr/bin/env python3
"""Capture one exact PGO renderer invocation for cold actual-CPU replay."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'renderer-pgo-coupled-v1';OUT=P/'native-renderer-capture-v1'
APP=P/'renderer-pgo-v1/use/app.elf'
CPP=r'''
// Private untimed snapshot observer. No renderer/data mutation.
static unsigned native_phase;
static uint32_t native_run_pc,native_run_return,native_end_pc,native_end_return;
static FILE *native_services,*native_words;
static std::vector<uint32_t> native_initial_gpu_writes;
static uint32_t native_geometry_regs[0x140/4];
static void native_binary(const char *name,const void *data,size_t bytes) {
    std::string path=folder+"/snapshot/"+name;
    FILE *f=fopen(path.c_str(),"wb");assert(f);
    assert(fwrite(data,1,bytes,f)==bytes);assert(!fclose(f));
}
static void native_cpu_dump(const char *name,qsim_cpu *c) {
    std::vector<uint32_t> state(c->x,c->x+32);
    state.insert(state.end(),c->f,c->f+32);
    uint32_t tail[]={c->pc,qsim_fflags(c)|(uint32_t(c->frm)<<5),c->mstatus,c->mie,c->mip,c->mtvec,
        c->mscratch,c->mepc,c->mcause,c->mtval,c->csr_7ff,c->resv_valid,c->resv_addr};
    state.insert(state.end(),std::begin(tail),std::end(tail));
    native_binary(name,state.data(),state.size()*4);
}
extern "C" int native_capture_hook(qenv *E,qsim_cpu *c,uint32_t pc) {
    if(!active || E->flips<360)return 0;
    if(!native_phase && pc==native_run_pc) {
        coupled_sync();
        assert(E->flips==360 && !c->resv_valid && !tb->busy && !gd_operation && !gd_response_active);
        assert(!tb->overlap_upload_words && !tb->overlap_output_words && !texture_inflight);
        std::filesystem::create_directory(folder+"/snapshot");
        native_cpu_dump("cpu-state.bin",c);
        native_binary("sdram.bin",c->sdram,c->sdram_size);
        native_binary("bram.bin",c->bram,c->bram_size);
        std::vector<uint32_t> cram(0x1000000/4);
        for(unsigned i=0;i<cram.size();++i)cram[i]=cram_word(i*4);
        native_binary("cram1.bin",cram.data(),0x1000000);
        native_binary("gpu-init-writes.bin",native_initial_gpu_writes.data(),native_initial_gpu_writes.size()*4);
        native_binary("geometry-registers.bin",native_geometry_regs,sizeof native_geometry_regs);
        native_run_return=c->x[1];
        const auto *end=qsim_syms_lookup(&E->syms,"gfx_end_frame");assert(end);native_end_pc=end->addr;
        assert(!qsim_add_hook(c,native_run_return));
        assert(!qsim_add_hook(c,native_end_pc));
        FILE *f=fopen((folder+"/snapshot/state.json").c_str(),"w");assert(f);
        fprintf(f,"{\"frame\":360,\"gfx_run\":%u,\"gfx_end_frame\":%u,\"commands\":%u,\"return_pc\":%u,"
            "\"sdram_base\":%u,\"sdram_bytes\":%u,\"bram_bytes\":%u,\"ring_byte_pointer\":%u,\"ring_offset\":%u,"
            "\"fence\":%u,\"display_index\":%d,\"pending_index\":%d,\"draw_index\":%d,\"env_ready_index\":%d,"
            "\"width\":%u,\"height\":%u,\"stride\":%u,\"color_mode\":%u,\"depth_address\":%u,"
            "\"vblanks\":%llu,\"presents\":%llu,\"cycle\":%llu,\"cpu_model_cycle\":%llu,\"gpu_regs\":[",
            native_run_pc,native_end_pc,c->x[10],native_run_return,c->sdram_base,c->sdram_size,c->bram_size,
            (reg_read(4)+ring_offset)&16383,ring_offset,tb->fence_reached,display_idx,pending_idx,E->buf_draw,E->buf_ready,
            E->mode_w,E->mode_h,E->mode_stride,E->mode_color,depth_address,
            (unsigned long long)vblanks,(unsigned long long)presents,(unsigned long long)cycles,(unsigned long long)c->model->cyc);
        for(unsigned i=0;i<16;++i)fprintf(f,"%s%u",i?",":"",reg_read(i));
        fprintf(f,"],\"globals\":{");
        const char *globals[]={"load_alias_present","load_alias_dirty","load_alias_controls_valid","_gpu_wrptr","_gpu_known_rdptr","_gpu_cmd_pos","g_zbuf"};
        bool comma=false;
        for(auto name:globals)if(const auto *s=qsim_syms_lookup(&E->syms,name)){
            uint32_t value;memcpy(&value,qsim_mem_ptr(c,s->addr,4),4);
            fprintf(f,"%s\"%s\":{\"address\":%u,\"value\":%u}",comma?",":"",name,s->addr,value);comma=true;
        }
        fprintf(f,"}}\n");fclose(f);
        native_services=fopen((folder+"/snapshot/services.csv").c_str(),"w");assert(native_services);
        fprintf(native_services,"phase,kind,index,a0,a1,a2,a3,r0,r1,pc,ra\n");
        native_words=fopen((folder+"/snapshot/commands.bin").c_str(),"wb");assert(native_words);
        native_phase=1;return 1;
    }
    if(native_phase==1 && pc==native_run_return) {
        native_cpu_dump("after-run.bin",c);native_phase=2;return 1;
    }
    if(native_phase==2 && pc==native_end_pc) {
        native_cpu_dump("before-end.bin",c);native_end_return=c->x[1];
        assert(!qsim_add_hook(c,native_end_return));native_phase=3;return 1;
    }
    if(native_phase==3 && pc==native_end_return) {
        native_cpu_dump("after-end.bin",c);native_phase=4;
        assert(!fclose(native_words));native_words=nullptr;
        assert(!fclose(native_services));native_services=nullptr;
        FILE *f=fopen((folder+"/snapshot/complete.json").c_str(),"w");assert(f);
        fprintf(f,"{\"passed\":true,\"phase\":4,\"end_return_pc\":%u,\"fence\":%u}\n",native_end_return,tb->fence_reached);fclose(f);
        return 1;
    }
    return 0;
}
extern "C" void native_capture_service(qsim_cpu *c,unsigned kind,unsigned index,
    uint32_t a0,uint32_t a1,uint32_t a2,uint32_t a3,uint32_t r0,uint32_t r1) {
    if(!native_services)return;
    fprintf(native_services,"%u,%u,%u,%u,%u,%u,%u,%u,%u,%u,%u\n",native_phase,kind,index,a0,a1,a2,a3,r0,r1,c->pc,c->x[1]);
    fflush(native_services);
}
'''

def prepare():
    assert sha(APP)=='fff132a5ee34d625a279fbf612e4abe54faa122fe519b386130f5ee230dfe63b'
    OUT.mkdir(exist_ok=False)
    shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
    shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    f=OUT/'model/frozen/coupled.cpp';s=f.read_text().replace('#include <algorithm>','#include <algorithm>\n#include <filesystem>')
    # Definitions are inserted after coupled_sync and before render/start hooks.
    marker='extern "C" void coupled_render_enter() {';assert s.count(marker)==1
    s=s.replace(marker,CPP+'\n'+marker)
    # Init MMIO sequence is copied, not inferred from write-only readbacks.
    s=s.replace('static void reg_write(unsigned r,uint32_t v) {','static std::vector<uint32_t> native_initial_gpu_writes;\nstatic void reg_write(unsigned r,uint32_t v) {\n    if(initializing){native_initial_gpu_writes.push_back(r*4);native_initial_gpu_writes.push_back(v);}')
    s=s.replace('static std::vector<uint32_t> native_initial_gpu_writes;\nstatic uint32_t native_geometry_regs','static uint32_t native_geometry_regs')
    s=s.replace('initializing=false;active=true;snapshot("start",0);','initializing=false;active=true;snapshot("start",0);\n    native_run_pc=coupled_render_pc;assert(!qsim_add_hook(E->cpu,native_run_pc));')
    s=s.replace('extern "C" void coupled_gd_write(uint32_t off,uint32_t value) {','extern "C" void coupled_gd_write(uint32_t off,uint32_t value) {\n    assert(off<sizeof native_geometry_regs);native_geometry_regs[off/4]=value;')
    s=s.replace('if(off==4)upload_words++;','if(off==4){upload_words++;if(native_words){assert(native_phase==1 || native_phase==3);assert(fwrite(&value,4,1,native_words)==1);}}')
    f.write_text(s)
    f=OUT/'qsim/env.c';s=f.read_text();s=s.replace('#include "coupled.h"','#include "coupled.h"\nextern int native_capture_hook(qenv*,qsim_cpu*,uint32_t);\nextern void native_capture_service(qsim_cpu*,unsigned,unsigned,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);')
    s=s.replace('if (i < 0 || i >= ENV_NSVC) {','if (i < 0 || i >= ENV_NSVC) {\n        if(native_capture_hook(E,cpu,pc)){bob_unhook(cpu,pc);return QSIM_TRAP_RESUME;}')
    s=s.replace('    x[10] = r0;\n    x[11] = r1;','    native_capture_service(cpu,0,(unsigned)i,a0,a1,a2,a3,r0,r1);\n    x[10] = r0;\n    x[11] = r1;')
    s=s.replace('if (cause == QSIM_CAUSE_ECALL_M) {','if (cause == QSIM_CAUSE_ECALL_M) {\n        native_capture_service(cpu,1,cpu->x[17],cpu->x[10],cpu->x[11],cpu->x[12],cpu->x[13],0,0);')
    f.write_text(s)
    command=json.loads((OLD/'model/build.json').read_text())['command'];base=str(Path(command[command.index('--Mdir')+1]).parents[1])
    command=[x.replace(base,str(OUT))for x in command];write(OUT/'build-command.json',command)
    shutil.copy2(__file__,OUT/'recipe.py')
    write(OUT/'prepared.json',dict(parent_manifest_sha256=sha(OLD/'manifest.json'),elf_sha256=sha(APP),source_sha256={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()},scope=__doc__))

def build():
    with(OUT/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
    command=json.loads((OUT/'build-command.json').read_text())
    with(OUT/'model/build.log').open('x')as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(OUT/'model/build.json',dict(command=command,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))

def run():
    m=module('native_capture_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
    os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
    sys.argv=[__file__,'reference','--elf',str(APP),'--start','360','--frames','2'];m.main()
    d=OUT/'run-reference';assert json.loads((d/'snapshot/complete.json').read_text())['passed']
    for frame in (360,361):assert(d/f'frame_{frame:05}.bin').read_bytes()==(OLD/f'run-warm/frame_{frame:05}.bin').read_bytes()
    write(OUT/'result.json',dict(passed=True,entry_frame=360,reference_frames_exact=[360,361],snapshot_sha256={f.name:sha(f)for f in(d/'snapshot').iterdir()if f.is_file()},limits=['Architectural snapshot/observed services, not native execution.','Two reference frames allow first gfx_end_frame return to be observed. Only frame360 is replay input.']))
    write(OUT/'manifest.json',dict(passed=True,files={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()and f.name!='manifest.json'and'obj'not in f.parts and'build'not in f.parts},evidence_sha256={str(OLD.relative_to(ROOT))+'/manifest.json':sha(OLD/'manifest.json')}))
    print(sha(OUT/'manifest.json'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run']);a=p.parse_args();globals()[a.action]()
