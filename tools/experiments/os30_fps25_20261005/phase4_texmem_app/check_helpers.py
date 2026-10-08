#!/usr/bin/env python3
"""Exercise exact candidate lifecycle helpers with a bounded host MMIO mock."""
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CANDIDATE = ROOT/'docs/measurements/os30-fps25-20261005/phase4/texmem-app'
OUT = CANDIDATE/'helper-tests-v2'


def function(source, name):
    import re
    m = re.search(r'^static [^\n]+\b'+name+r'\(',source,re.M)
    assert m, name
    start=m.start(); brace=source.index('{',m.end()); depth=1; i=brace+1
    while depth:
        depth += (source[i]=='{')-(source[i]=='}');i+=1
    return source[start:i]


PREFIX = r'''
#define _POSIX_C_SOURCE 200112L
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define MAX_TEX 4096
#define OF_HW_GPU_FAST_TEX (1u<<25)
#define SM64_PERF_SCOPE(x) ((void)0)
#define SM64_PERF_SYNC 0
struct of_capabilities { uint32_t version,hw_features,tex_fast_size; };
'''

MOCKS = r'''
static struct GTex g_tex[MAX_TEX];
static uint32_t g_tex_count,g_tex_epoch=1;
static uint16_t g_white_tex[32];
static uint32_t g_white_addr;
static int g_truecolor=1,g_fb_bpp=2,g_st_cache_valid=1,g_tri_state_dirty,g_vc_state_dirty;
static unsigned finishes,uploads,routes,flushes,uncached_reads;
static char events[10000];static unsigned event_count;
static unsigned char fastmem[4096];static int route;
static const void *alias_source;static void *alias_target;
static uint32_t last_upload_off,last_upload_words;
static const void *last_upload_source;
static void event(char e){assert(event_count<sizeof events);events[event_count++]=e;}
static void gpu_material_changed(void){g_vc_state_dirty=g_tri_state_dirty=1;event('M');}
static void of_gpu_finish(void){finishes++;event('F');}
static void _of_gpu_route_fast_tex(int enabled){route=enabled;routes++;event(enabled?'1':'0');}
static void *of_uncached(const void*p){uncached_reads++;return p==alias_source?alias_target:(void*)p;}
static void _of_gpu_fast_tex_upload(uint32_t off,const uint32_t*p,uint32_t words){
 assert(off%16==0 && off+words*4<=sizeof fastmem);
 last_upload_off=off;last_upload_words=words;last_upload_source=p;
 memcpy(fastmem+off,p,words*4);uploads++;event('U');
}
static void gpu_tex_publish(void){flushes++;event('C');}
static uint8_t *gpu_tex_alloc(uint32_t bytes){
 void*p=NULL;assert(posix_memalign(&p,64,(bytes+63)&~63u)==0);memset(p,0,(bytes+63)&~63u);return p;
}
'''

TESTS = r'''
static void reset_fixture(void){
 for(unsigned i=0;i<MAX_TEX;i++)for(unsigned j=0;j<2;j++){
  free(g_tex[i].slot[j]);free(g_tex[i].alpha_mask[j]);
 }
 memset(g_tex,0,sizeof g_tex);memset(fastmem,0xcc,sizeof fastmem);
 memset(g_white_tex,0,sizeof g_white_tex);g_white_tex[0]=0xffff;
 g_white_addr=(uint32_t)(uintptr_t)g_white_tex;g_truecolor=1;g_fb_bpp=2;
 g_tex_epoch=1;g_tex_count=1;finishes=uploads=routes=flushes=uncached_reads=event_count=0;
 g_st_cache_valid=1;g_tri_state_dirty=g_vc_state_dirty=0;route=0;
 alias_source=alias_target=NULL;g_fast_tex_active=0;g_fast_tex_top=g_fast_tex_size=0;
}
static struct of_capabilities caps(unsigned bytes){
 struct of_capabilities c={3,OF_HW_GPU_FAST_TEX,bytes};return c;
}
static void start(unsigned bytes){struct of_capabilities c=caps(bytes);gpu_fast_tex_init(&c);assert(g_fast_tex_active);}
int main(void){
 reset_fixture();struct of_capabilities c=caps(256);
 gpu_fast_tex_init(NULL);assert(!uploads&&!routes);
 c.version=2;gpu_fast_tex_init(&c);assert(!uploads&&!routes);
 c=caps(256);c.hw_features=0;gpu_fast_tex_init(&c);assert(!uploads&&!routes);
 c=caps(0x01000001);gpu_fast_tex_init(&c);assert(!uploads&&!routes);
 c=caps(63);gpu_fast_tex_init(&c);assert(!uploads&&!routes);
 c=caps(256);g_truecolor=0;gpu_fast_tex_init(&c);assert(!uploads&&!routes);

 reset_fixture();start(512);assert(route==1&&uploads==1&&finishes==0);
 assert(events[0]=='U'&&events[1]=='1'&&g_white_addr==0&&g_fast_tex_top==64);
 assert(fastmem[0]==255&&fastmem[1]==255);
 struct GTex*t=&g_tex[0];assert(gpu_tex_slot_begin(t,6));
 assert(t->cur==1);uint16_t*pix=(uint16_t*)t->ci8;pix[0]=0;pix[1]=1;pix[2]=0x07e0;
 gpu_tex_slot_finish(t,3,1);
 assert(t->addr==(uint32_t)(uintptr_t)t->ci8);
 assert(t->fast_addr[1]==64&&t->fast_capacity[1]==16&&last_upload_words==2);
 assert(gpu_tex_active_address(t,0)==64&&memcmp(fastmem+64,pix,6)==0);
 assert(gpu_tex_alpha_mask(t));assert(t->fast_mask_addr[1]==80&&t->fast_mask_capacity[1]==16);
 uint16_t*mask=(uint16_t*)(fastmem+80);assert(mask[0]==0xffff&&mask[1]==0&&mask[2]==0);
 assert(gpu_tex_active_address(t,1)==80);unsigned before=uploads;
 assert(gpu_tex_alpha_mask(t)&&uploads==before); /* lazy mask is reused */

 /* The inactive generation may upload while commands reference the current
  * generation: it uses disjoint storage and does not require a finish. */
 t->slot_epoch[t->cur]=g_tex_epoch;assert(gpu_tex_slot_begin(t,6));
 assert(t->cur==0&&finishes==0);gpu_tex_slot_finish(t,3,1);
 assert(t->fast_addr[0]==96&&t->fast_addr[0]!=t->fast_addr[1]);
 t->slot_epoch[0]=g_tex_epoch;
 g_tex_count=UINT32_MAX;g_tex[MAX_TEX-1].slot_epoch[0]=g_tex_epoch;
 assert(gpu_tex_slot_begin(t,6));assert(finishes==1&&t->cur==1&&!t->alpha_mask[1]);
 assert(g_tex[MAX_TEX-1].slot_epoch[0]==0); /* bounded loop even at ID wrap */
 before=g_fast_tex_top;gpu_tex_slot_finish(t,3,1);
 assert(t->fast_addr[1]==64&&g_fast_tex_top==before); /* capacity reuse */
 assert(gpu_tex_alpha_mask(t));assert(t->fast_mask_addr[1]==80&&g_fast_tex_top==before);

 /* Growing one generation keeps all live prior offsets intact and consumes
  * a new bounded extent; reading uses the uncached alias, not stale cache. */
 unsigned char cached[64]={0},uncached[64];memset(uncached,0x5a,sizeof uncached);
 alias_source=cached;alias_target=uncached;uint32_t addr=0,capacity=0;
 assert(gpu_fast_tex_upload(&addr,&capacity,cached,18));
 assert(last_upload_source==uncached&&last_upload_words==5&&capacity==32);
 assert(!memcmp(fastmem+addr,uncached,20));
 uint32_t previous=addr;assert(gpu_fast_tex_upload(&addr,&capacity,cached,33));
 assert(addr!=previous&&capacity==48);

 /* Exhaustion drains existing commands BEFORE switching the global route.
  * Every normal/alpha/white selection then uses the original SDRAM backup. */
 unsigned finish_before=finishes,route_before=routes;event_count=0;
 assert(!gpu_fast_tex_upload(&addr,&capacity,cached,1024));
 assert(finishes==finish_before+1&&routes==route_before+1&&!g_fast_tex_active&&route==0);
 assert(events[0]=='F'&&events[1]=='0'&&events[2]=='M');
 assert(!g_st_cache_valid&&g_tri_state_dirty&&g_vc_state_dirty);
 assert(g_white_addr==(uint32_t)(uintptr_t)g_white_tex);
 assert(gpu_tex_active_address(t,0)==t->addr);
 assert(gpu_tex_active_address(t,1)==(uint32_t)(uintptr_t)t->alpha_mask[t->cur]);
 before=uploads;assert(!gpu_fast_tex_upload(&addr,&capacity,cached,18));
 assert(uploads==before&&finishes==finish_before+1&&routes==route_before+1);

 /* Exhaustion during first lazy-mask creation: allocated CPU mask remains
  * valid for the current batch after the permanent domain switch. */
 reset_fixture();start(80);t=&g_tex[0];assert(gpu_tex_slot_begin(t,6));
 pix=(uint16_t*)t->ci8;pix[0]=0;pix[1]=0xffff;pix[2]=1;gpu_tex_slot_finish(t,3,1);
 assert(gpu_tex_alpha_mask(t));assert(!g_fast_tex_active&&finishes==1);
 mask=(uint16_t*)t->alpha_mask[t->cur];assert(mask[0]==0xffff&&mask[1]==0&&mask[2]==0);
 assert(gpu_tex_active_address(t,1)==(uint32_t)(uintptr_t)mask);

 reset_fixture();start(512);addr=capacity=0;
 assert(!gpu_fast_tex_upload(&addr,&capacity,cached,UINT32_MAX));
 assert(!g_fast_tex_active&&finishes==1);
 reset_fixture();puts("PASS exact extracted helpers: allocation/growth/alias/mask/generations/epoch/fallback/order");
 return 0;
}
'''


def main():
    OUT.mkdir(exist_ok=False)
    source=(CANDIDATE/'gfx_gpu.c').read_text()
    struct_text=source[source.index('struct GTex {'):source.index('};',source.index('struct GTex {'))+2]
    helpers=source[source.index('/* ---- Dedicated RGB565'):source.index('/* ---- End resident-texture helpers ---- */')]
    bodies='\n\n'.join(function(source,n) for n in ['gpu_tex_slot_begin','gpu_tex_alpha_mask','gpu_tex_slot_finish'])
    (OUT/'helpers.c').write_text(PREFIX+struct_text+MOCKS+helpers+bodies+TESTS)
    results=[]
    for mode,flags in [('plain',['-O2']),('sanitized',['-O1','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
        cmd=['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(OUT/'helpers.c'),'-o',str(OUT/mode)]
        subprocess.run(cmd,check=True)
        env=dict(os.environ)
        # LeakSanitizer cannot enumerate threads under the execution sandbox's
        # ptrace supervision; AddressSanitizer and UBSan remain enabled.
        if mode=='sanitized':env['ASAN_OPTIONS']='detect_leaks=0'
        r=subprocess.run([str(OUT/mode)],capture_output=True,text=True,check=True,env=env)
        results.append(dict(mode=mode,command=cmd,output=r.stdout))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    record=dict(passed=True,candidate_sha256=sha(CANDIDATE/'gfx_gpu.c'),script_sha256=sha(Path(__file__)),
                extracted_source_sha256=sha(OUT/'helpers.c'),runs=results,
                limitation='Real helper bodies with host mocks, not CPU/GPU simulation. SDK MMIO polling/CRAM1 controller concurrency and full rendering must pass separate tests. LeakSanitizer disabled under ptrace; ASan and UBSan enabled.')
    (OUT/'results.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))


if __name__=='__main__': main()
