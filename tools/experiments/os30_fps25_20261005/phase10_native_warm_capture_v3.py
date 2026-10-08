#!/usr/bin/env python3
"""Passive exact frame360→361 reference/service-state capture; no timer changes."""
import argparse
import json
import os
import shutil
import sys
from phase10_experiment import CAMP, ROOT, module, sha, write

P=CAMP/'phase10'
OUT=P/'native-warm-capture-v3'
BASE=ROOT/'tools/experiments/os30_fps25_20261005/phase10_native_renderer_capture.py'
m=module('warm_capture_base', BASE)
m.OUT=OUT

def once(s, old, new):
    assert s.count(old)==1, old[:100]
    return s.replace(old,new)

STATE=r'''
static void native_service_state(qenv *E,const char *name="service-state.json") {
    FILE *f=fopen((folder+"/snapshot/"+name).c_str(),"w");assert(f);
    fprintf(f,"{\"flips\":%llu,\"logical_us\":%llu,\"ended\":%u,\"mixer_seq\":%llu,"
        "\"brk_base\":%u,\"cur_brk\":%u,\"mmap_bottom\":%u,\"display\":%d,\"draw\":%d,\"ready\":%d,\"swap_kicked\":%d,\"voices\":[",
        (unsigned long long)E->flips,(unsigned long long)(5000000u+(E->flips*1000000ull+29)/30),
        E->sm64_ended,(unsigned long long)E->mixer_seq,E->brk_base,E->cur_brk,E->mmap_bottom,
        E->buf_display,E->buf_draw,E->buf_ready,E->swap_kicked);
    for(unsigned i=0;i<32;++i) {
        const auto &v=E->sm64_voice[i];
        fprintf(f,"%s{\"position\":%llu,\"last_us\":%llu,\"fraction\":%llu,\"count\":%u,\"rate\":%u,\"loop_start\":%u,\"loop_end\":%u,\"active\":%d}",
            i?",":"",(unsigned long long)v.position,(unsigned long long)v.last_us,(unsigned long long)v.fraction,
            v.count,v.rate,v.loop_start,v.loop_end,v.active);
    }
    fprintf(f,"]}\n");assert(!fclose(f));
}
'''
SERVICE=r'''
extern "C" void native_capture_service(qenv *E,qsim_cpu *c,unsigned kind,unsigned index,unsigned fid,
    const uint32_t *args,uint32_t r0,uint32_t r1) {
    if(!native_services)return;
    fprintf(native_services,"%llu,%u,%u,%u,%u",(unsigned long long)E->flips,native_phase,kind,index,fid);
    for(unsigned i=0;i<8;++i)fprintf(native_services,",%u",args[i]);
    fprintf(native_services,",%u,%u,%u,%u",r0,r1,c->pc,c->x[1]);
    const auto *flip=qsim_syms_lookup(&E->syms,"g_flip_token");assert(flip);
    uint32_t token;memcpy(&token,qsim_mem_ptr(c,flip->addr,4),4);
    fprintf(native_services,",%u",token);
    assert(token==E->flips+1); // bounded observed clock mapping, not generic fence arithmetic
    uint32_t address=0,size=0;
    if(!kind) {
        if(index==15) {address=args[1];size=24;}
        if(index==16) {address=args[0];size=24;}
        if(index==69) {address=args[0];size=112;}
        if(index==70) {address=args[0];size=20;}
        if(index==92) {address=args[0];size=32;}
    }
    fprintf(native_services,",%u,%u,",address,size);
    if(size) {auto bytes=(const uint8_t*)qsim_mem_ptr(c,address,size);assert(bytes);for(unsigned i=0;i<size;++i)fprintf(native_services,"%02x",bytes[i]);}
    fprintf(native_services,"\n");fflush(native_services);
}
'''

def prepare():
    cpp=m.CPP
    cpp=once(cpp,'extern "C" int native_capture_hook',STATE+'\nextern "C" int native_capture_hook')
    cpp=once(cpp,'        native_cpu_dump("cpu-state.bin",c);','        native_cpu_dump("cpu-state.bin",c);native_service_state(E);')
    cpp=once(cpp,'phase,kind,index,a0,a1,a2,a3,r0,r1,pc,ra\\n','frame,phase,kind,index,fid,a0,a1,a2,a3,a4,a5,a6,a7,r0,r1,pc,ra,flip_token,out_address,out_size,out_hex\\n')
    cpp=once(cpp,'    if(native_phase==1 && pc==native_run_return) {', '''    if(native_phase==4 && pc==native_run_pc) {
        assert(E->flips==361);native_cpu_dump("frame361-entry.bin",c);
        native_run_return=c->x[1];
        assert(!qsim_add_hook(c,native_run_return));
        assert(!qsim_add_hook(c,native_end_pc));
        native_phase=1;return 1;
    }
    if(native_phase==1 && pc==native_run_return) {''')
    cpp=once(cpp,'        native_cpu_dump("after-run.bin",c);native_phase=2;return 1;', '''        if(E->flips==360)native_cpu_dump("after-run.bin",c);
        else native_cpu_dump("frame361-after-run.bin",c);
        assert(!qsim_add_hook(c,native_run_pc));native_phase=2;return 1;''')
    cpp=once(cpp,'        native_cpu_dump("before-end.bin",c);native_end_return=c->x[1];', '''        if(E->flips==360)native_cpu_dump("before-end.bin",c);
        else native_cpu_dump("frame361-before-end.bin",c);
        native_end_return=c->x[1];''')
    cpp=once(cpp,'        native_cpu_dump("after-end.bin",c);native_phase=4;', '''        if(E->flips==361) {
            native_cpu_dump("after-end.bin",c);native_phase=4;return 1;
        }
        assert(E->flips==362);native_cpu_dump("frame361-after-end.bin",c);native_phase=5;''')
    cpp=once(cpp,'\\"phase\\":4,','\\"phase\\":5,')
    pos=cpp.index('extern "C" void native_capture_service(')
    cpp=cpp[:pos]+SERVICE
    cpp=once(cpp,'static unsigned native_phase;','static unsigned native_phase;\nstatic unsigned native_call_frame=360;')
    start=cpp.index('    if(native_phase==4 && pc==native_run_pc) {')
    end=cpp.index('    if(native_phase==1 && pc==native_run_return)',start)
    cpp=cpp[:start]+r"""    if(native_phase==4 && pc==native_run_pc) {
        assert(E->flips==native_call_frame+1);++native_call_frame;
        if(native_call_frame==362) {
            native_cpu_dump("frame362-entry.bin",c);native_service_state(E,"service-state-final.json");
            assert(!fclose(native_words));native_words=nullptr;
            assert(!fclose(native_services));native_services=nullptr;
            FILE *f=fopen((folder+"/snapshot/complete.json").c_str(),"w");assert(f);
            fprintf(f,"{\"passed\":true,\"phase\":5,\"end_return_pc\":%u,\"fence\":%u,\"last_entry\":362}\n",native_end_return,tb->fence_reached);fclose(f);
            native_phase=5;return 1;
        }
        assert(native_call_frame==361);native_cpu_dump("frame361-entry.bin",c);
        native_run_return=c->x[1];
        assert(!qsim_add_hook(c,native_run_return));
        assert(!qsim_add_hook(c,native_end_pc));
        native_phase=1;return 1;
    }
"""+cpp[end:]
    cpp=cpp.replace('if(E->flips==360)native_cpu_dump','if(native_call_frame==360)native_cpu_dump')
    start=cpp.index('    if(native_phase==3 && pc==native_end_return) {')
    end=cpp.index('    return 0;\n}',start)
    cpp=cpp[:start]+r"""    if(native_phase==3 && pc==native_end_return) {
        if(native_call_frame==360)native_cpu_dump("after-end.bin",c);
        else native_cpu_dump("frame361-after-end.bin",c);
        native_phase=4;return 1;
    }
"""+cpp[end:]
    m.CPP=cpp
    m.prepare()
    f=OUT/'qsim/env.c';s=f.read_text()
    s=once(s,'extern void native_capture_service(qsim_cpu*,unsigned,unsigned,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t,uint32_t);',
           'extern void native_capture_service(qenv*,qsim_cpu*,unsigned,unsigned,unsigned,const uint32_t*,uint32_t,uint32_t);')
    s=once(s,'    uint32_t a0 = x[10], a1 = x[11], a2 = x[12], a3 = x[13];',
           '    uint32_t a0 = x[10], a1 = x[11], a2 = x[12], a3 = x[13];\n    uint32_t native_args[8];for(unsigned ni=0;ni<8;++ni)native_args[ni]=x[10+ni];')
    s=once(s,'    native_capture_service(cpu,0,(unsigned)i,a0,a1,a2,a3,r0,r1);',
           '    native_capture_service(E,cpu,0,(unsigned)i,0,native_args,r0,r1);')
    s=once(s,'        native_capture_service(cpu,1,cpu->x[17],cpu->x[10],cpu->x[11],cpu->x[12],cpu->x[13],0,0);',
           '        uint32_t native_args[8];for(unsigned ni=0;ni<8;++ni)native_args[ni]=cpu->x[10+ni];')
    s=once(s,'        cpu->pc += 4;',
           '        native_capture_service(E,cpu,1,nr,fid,native_args,cpu->x[10],cpu->x[11]);\n        cpu->pc += 4;')
    f.write_text(s)
    # The inherited all-upload observer remains active between calls too. There
    # is no assumption that only the renderer can submit commands in the gap.
    f=OUT/'model/frozen/coupled.cpp';s=f.read_text().replace('assert(native_phase==1 || native_phase==3);','assert(native_phase>=1 && native_phase<=4);');f.write_text(s)
    shutil.copyfile(__file__,OUT/'warm-recipe.py')
    write(OUT/'warm-prepared.json',dict(parent_recipe_sha256=sha(BASE),elf_sha256=sha(m.APP),
        source_sha256={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()},
        scope=__doc__,timer_semantics_changed=False))

def build():m.build()

def run():
    r=module('warm_capture_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');r.OUT=OUT
    os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
    sys.argv=[__file__,'reference','--elf',str(m.APP),'--start','360','--frames','3'];r.main()
    d=OUT/'run-reference';assert json.loads((d/'snapshot/complete.json').read_text())['passed']
    for frame in (360,361,362):
        assert(d/f'frame_{frame:05}.bin').read_bytes()==(m.OLD/f'run-warm/frame_{frame:05}.bin').read_bytes()
    old=P/'native-renderer-capture-v1/run-reference/snapshot'
    for n in ['cpu-state.bin','sdram.bin','bram.bin','cram1.bin','gpu-init-writes.bin','geometry-registers.bin','state.json','after-run.bin','before-end.bin','after-end.bin']:
        assert(d/'snapshot'/n).read_bytes()==(old/n).read_bytes(),n
    write(OUT/'result.json',dict(passed=True,entry_frame=360,reference_frames_exact=[360,361,362],
        capture_ends_at_entry_frame=362,initial_snapshot_and_first_call_architecture_exact=True,
        snapshot_sha256={f.name:sha(f)for f in(d/'snapshot').iterdir()if f.is_file()},
        limits=['Passive reference only; no native execution.','Timer and functional mixer semantics remain unchanged.','Third reference frame is needed to observe entry362; commands/game/audio are captured from entry360 through entry362.']))
    write(OUT/'manifest.json',dict(passed=True,files={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()and f.name!='manifest.json'and'obj'not in f.parts and'build'not in f.parts},
        evidence_sha256={str(m.OLD.relative_to(ROOT))+'/manifest.json':sha(m.OLD/'manifest.json'),str(old.parent.parent.relative_to(ROOT))+'/manifest.json':sha(old.parent.parent/'manifest.json')}))
    print(sha(OUT/'manifest.json'))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','build','run']);args=parser.parse_args();globals()[args.action]()
