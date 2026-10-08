#!/usr/bin/env python3
"""Private TEX1/MMIO parser and actual GPU upload/fill concurrency trial."""
import argparse,importlib.util,json,shutil,subprocess
from pathlib import Path
S=importlib.util.spec_from_file_location('resident',Path(__file__).with_name('run.py'))
R=importlib.util.module_from_spec(S);S.loader.exec_module(R)
OUT=R.OUT/'extended'

HOST=r'''
static bool tagged_tex=false,concurrent_upload=false;
static std::vector<uint32_t> resident_words(0x200000/4,0);
static uint32_t upload_pointer=0;
static uint64_t upload_words=0,upload_cycles=0,upload_max=0;
static uint64_t upload_while_fill=0,fill_while_upload=0;
static uint64_t concurrent_start=0,concurrent_end=0;
static unsigned concurrent_count=0;
static bool concurrent_started=false,concurrent_done=false,concurrent_word_pending=false;
static uint64_t concurrent_word_start=0;
static const unsigned CONCURRENT_WORDS=256;
static const uint32_t CONCURRENT_BASE=0x1f0000;
static uint32_t upload_pattern(unsigned i){return 0x57ea1600u^(i*0x1020101u);}
static uint32_t cram_word(uint32_t a){
    if((a&3)||a+4>0x200000)throw std::runtime_error("CRAM model range/alignment");
    auto &mem=tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    return uint32_t(mem[a>>1])|(uint32_t(mem[(a>>1)+1])<<16);
}
static void cram_preload_word(uint32_t a,uint32_t w){
    if((a&3)||a+4>0x200000)throw std::runtime_error("CRAM preload range/alignment");
    auto &mem=tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;
    mem[a>>1]=w&65535;mem[(a>>1)+1]=w>>16;resident_words[a>>2]=w;
}
static void verify_uploaded_word(uint32_t a,uint32_t w){
    if(cram_word(a)!=w)throw std::runtime_error("GPU MMIO upload readback mismatch");
    resident_words[a>>2]=w;
}
'''

UPLOAD=r'''
static void drain_upload(){
    uint64_t start=cycles;
    while(mmio_read(15)&1u){
        if(cycles-start>1000000)throw std::runtime_error("MMIO upload timeout");
        tick();
    }
}
static void live_mmio(uint32_t off,uint32_t val){
    if(off!=0x08&&off!=0x38&&off!=0x3c)throw std::runtime_error("Unexpected captured MMIO register");
    if(off==0x08){drain_upload();upload_pointer=val&0x3fffff;mmio_write(2,val);}
    else if(off==0x38){
        drain_upload();
        if(tb->busy||texture_inflight)throw std::runtime_error("Captured route change while GPU busy");
        mmio_write(14,val);
    }else{
        drain_upload();
        uint32_t a=upload_pointer*4;
        if(a+4>0x200000)throw std::runtime_error("Live upload exceeds model safe range");
        uint64_t start=cycles;
        if(texture_inflight)++upload_while_fill;
        mmio_write(15,val);drain_upload();
        uint64_t duration=cycles-start;upload_cycles+=duration;upload_max=std::max(upload_max,duration);++upload_words;
        verify_uploaded_word(a,val);upload_pointer=(upload_pointer+1)&0x3fffff;
    }
}
// Called only while the host is otherwise waiting for a published batch.
// Real MMIO strobes and status readback; no controller backdoor for uploads.
static void concurrency_step(){
    if(!concurrent_upload||concurrent_done)return;
    if(concurrent_word_pending&&!(mmio_read(15)&1u)){
        uint64_t duration=cycles-concurrent_word_start;
        upload_cycles+=duration;upload_max=std::max(upload_max,duration);++upload_words;
        verify_uploaded_word(CONCURRENT_BASE+4*(concurrent_count-1),upload_pattern(concurrent_count-1));
        concurrent_word_pending=false;
        if(concurrent_count==CONCURRENT_WORDS){concurrent_done=true;concurrent_end=cycles;return;}
    }
    if(!concurrent_started){
        if(!texture_inflight)return;
        concurrent_started=true;concurrent_start=cycles;
        mmio_write(2,CONCURRENT_BASE/4);
    }
    if(!concurrent_word_pending){
        if(texture_inflight)++upload_while_fill;
        concurrent_word_start=cycles;
        mmio_write(15,upload_pattern(concurrent_count++));
        concurrent_word_pending=true;
    }
}
static void upload_report(){
    if(concurrent_upload&&(!concurrent_done||!upload_while_fill||!fill_while_upload))
        throw std::runtime_error("Concurrent test did not exercise both overlap directions");
    printf("UPLOAD words=%llu upload_cycles=%llu word_cycles_max=%llu uploads_while_fill=%llu fills_while_upload=%llu concurrent_span=%llu readback_words=%llu\n",
        (unsigned long long)upload_words,(unsigned long long)upload_cycles,(unsigned long long)upload_max,
        (unsigned long long)upload_while_fill,(unsigned long long)fill_while_upload,
        (unsigned long long)(concurrent_end-concurrent_start),(unsigned long long)upload_words);
}
'''

def prepare():
    parent=json.loads((R.OUT/'inputs.json').read_text())
    for rel,h in parent['sources'].items():assert R.sha(R.OUT/rel)==h,rel
    OUT.mkdir(exist_ok=False);frozen=OUT/'frozen';shutil.copytree(R.OUT/'frozen',frozen)
    p=frozen/'texture_observer.hpp';s=p.read_text()
    s=R.replace(s,'static bool fast_tex=false;','static bool fast_tex=false;\n'+HOST)
    s=R.replace(s,'        auto a=original_texture_address(tb->obs_tex_araddr);','''        auto a=tagged_tex?(tb->obs_tex_araddr&0x3ffffff):original_texture_address(tb->obs_tex_araddr);
        if(tb->obs_c1_route&&tb->rootp->tb_gpu__DOT__gpu__DOT__gpu_tex_mem_up_inflight)++fill_while_upload;''')
    s=R.replace(s,'        for(unsigned k=0;k<4;++k)expected_texture[k]=sdram_word((a>>2)+k);','''        if(tagged_tex&&tb->obs_c1_route){
            if(a+16>0x200000)throw std::runtime_error("Tagged texture read exceeds model safe range");
            for(unsigned k=0;k<4;++k)expected_texture[k]=resident_words[(a>>2)+k];
        }else for(unsigned k=0;k<4;++k)expected_texture[k]=sdram_word((a>>2)+k);''')
    s=R.replace(s,'    if(!fast_tex)return;','    if(!fast_tex||tagged_tex)return;')
    p.write_text(s)
    p=frozen/'replay.cpp';s=p.read_text()
    s=R.replace(s,'#include <vector>','#include <vector>\n#include <algorithm>')
    s=R.replace(s,'static bool wait_idle(uint64_t timeout) {',UPLOAD+'\nstatic bool wait_idle(uint64_t timeout) {')
    s=R.replace(s,'        tick();\n        if (!tb->busy','        concurrency_step();\n        tick();\n        if (!concurrent_word_pending && !tb->busy')
    s=R.replace(s,'        if (tag == GV_TAG_MEM) {','''        if(tag==GV_TAG('T','E','X','1')){
            if(!tagged_tex)throw std::runtime_error("TEX1 requires +tagged_tex=1");
            uint32_t a=rd32(),n=rd32();
            if((a&3)||(n&3)||uint64_t(a)+n>0x200000||texture_inflight||tb->busy)
                throw std::runtime_error("Invalid TEX1 residency snapshot");
            require(n);
            for(uint32_t i=0;i<n;i+=4)cram_preload_word(a+i,rd32());
        }else if(tag==GV_TAG('M','M','I','O')){
            if(!tagged_tex)throw std::runtime_error("MMIO tag requires +tagged_tex=1");
            uint32_t off=rd32(),v=rd32();live_mmio(off,v);
        }else if (tag == GV_TAG_MEM) {''')
    s=R.replace(s,'    texture_report();','    texture_report();\n    upload_report();')
    s=R.replace(s,'    tb = new Vtb_gpu;','''    for(int a=1;a<argc;++a){
        if(!strcmp(argv[a],"+tagged_tex=1"))tagged_tex=true;
        if(!strcmp(argv[a],"+concurrent_upload=1"))concurrent_upload=true;
    }
    if(tagged_tex&&concurrent_upload)throw std::runtime_error("Separate tagged and concurrency tests");
    tb = new Vtb_gpu;''')
    p.write_text(s)
    R.write(OUT/'inputs.json',dict(parent_inputs_sha256=R.sha(R.OUT/'inputs.json'),script_sha256=R.sha(Path(__file__)),
        sources={str(p.relative_to(OUT)):R.sha(p) for p in sorted(frozen.rglob('*')) if p.is_file()},
        scope=__doc__,limits='TEX1 is zero-cycle previous-frame residency; MMIO uses actual controller completion. CMDS remains serialized at idle; separate deliberate upload overlap test. Model safe range first2MiB of die0.'))

def build():
    manifest=json.loads((OUT/'inputs.json').read_text())
    for rel,h in manifest['sources'].items():assert R.sha(OUT/rel)==h,rel
    dest=OUT/'model';dest.mkdir(exist_ok=False)
    old=json.loads((R.OUT/'model-extra0/build.json').read_text())
    cmd=[a.replace(str(R.OUT/'frozen'),str(OUT/'frozen')).replace(str(R.OUT/'model-extra0/obj'),str(dest/'obj')) for a in old['command']]
    with (dest/'build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    R.write(dest/'build.json',dict(command=cmd,simulator_sha256=R.sha(dest/'obj/Vtb_gpu'),inputs_sha256=R.sha(OUT/'inputs.json')))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['prepare','build']);a=p.parse_args()
    if a.action=='prepare':prepare()
    else:build()
