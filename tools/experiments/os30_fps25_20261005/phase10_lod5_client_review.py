#!/usr/bin/env python3
"""Instantiate the frozen LOD5 source-cache C function with an upload observer."""
import hashlib,json,os,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
SRC=P/'load-cram-driver-v1/gfx_gpu.c';OUT=P/'lod5-client-review-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
PRE=r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define CHECK(x) do {if(!(x)){fprintf(stderr,"FAIL line %u: %s\n",__LINE__,#x);exit(1);}}while(0)
static uint32_t physical[0x1000000/4];
static unsigned uploads,upload_words,calls;
static uint32_t word(uintptr_t p,unsigned i){return (uint32_t)p*0x9e3779b9u ^ i*0x6c078965u;}
static void _of_gpu_fast_tex_upload(uint32_t offset,const uint32_t *source,unsigned count){
 CHECK(offset>=0xfc0000 && !(offset&63) && count>0 && count<=192);
 CHECK((uint64_t)offset+4*count<=0x1000000);
 uploads++;upload_words+=count;
 for(unsigned i=0;i<count;i++)physical[offset/4+i]=word((uintptr_t)source,i);
}
'''
POST=r'''
static void reset(void){memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));
 memset(cram_vertex_stats,0,sizeof(cram_vertex_stats));
 g_cram_vertex_top=CRAM_VERTEX_BEGIN;g_cram_vertex_enabled=1;uploads=upload_words=0;
 memset(physical,0xa7,sizeof(physical));}
static uintptr_t matching(unsigned h,unsigned n,unsigned serial){
 uintptr_t p=0x10400000u+serial*0x10000u;
 for(;;p+=4)if((((p>>4)^(p>>14)^(n*33u))&1023)==h)return p;
}
static uint32_t call(uintptr_t p,unsigned count,int expected){
 uint32_t offset=0xdeadbeefu;unsigned before=uploads;calls++;
 CHECK(gfx_gpu_cram_vertex_source((const void*)p,count,&offset)==expected);
 if(expected){CHECK(!(offset&63));CHECK(offset>=0xfc0000 && (uint64_t)offset+count*24<=0x1000000);
   for(unsigned i=0;i<count*6;i++)CHECK(physical[offset/4+i]==word(p,i));
 }else{CHECK(offset==0xdeadbeefu);CHECK(uploads==before);}
 CHECK(physical[0x003c0000/4]==0xa7a7a7a7u);CHECK(physical[0x00fbfffc/4]==0xa7a7a7a7u);
 return offset;
}
int main(void){
 reset();g_cram_vertex_enabled=0;call(0x10400000,1,0);g_cram_vertex_enabled=1;
 call(0x10400000,0,0);call(0x10400000,33,0);call(0x10400001,1,0);
 uintptr_t same=matching(0,1,0);uint32_t a=call(same,1,1),b=call(same,2,1);CHECK(a!=b);
 unsigned u=uploads;CHECK(call(same,1,1)==a);CHECK(call(same,2,1)==b);CHECK(uploads==u);
 // Fill exactly all sixteen bounded probes; the seventeenth never uploads.
 reset();uintptr_t collision[17];
 for(unsigned i=0;i<17;i++)collision[i]=matching(1020,32,i);
 for(unsigned i=0;i<16;i++)call(collision[i],32,1);
 uint32_t top=g_cram_vertex_top;call(collision[16],32,0);CHECK(g_cram_vertex_top==top);
 for(unsigned i=0;i<16;i++)call(collision[i],32,1);CHECK(uploads==16);
 // Fill the complete256KiB pool without probe pressure:341*768+192+64.
 reset();uintptr_t keys[343];uint32_t offsets[343];unsigned counts[343];
 for(unsigned i=0;i<343;i++){
   counts[i]=i<341?32:i==341?8:1;keys[i]=matching(i,counts[i],i);
   offsets[i]=call(keys[i],counts[i],1);
 }
 CHECK(g_cram_vertex_top==CRAM_VERTEX_END);CHECK(uploads==343);
 top=g_cram_vertex_top;call(matching(900,1,900),1,0);CHECK(g_cram_vertex_top==top);
 for(unsigned i=0;i<343;i++)CHECK(call(keys[i],counts[i],1)==offsets[i]);CHECK(uploads==343);
 // Repeated existing entries never upload or alter their immutable contents.
 uint32_t rng=0x9e3779b9u;
 for(unsigned i=0;i<100000;i++){rng=rng*1664525u+1013904223u;unsigned k=rng%343;
   CHECK(call(keys[k],counts[k],1)==offsets[k]);}
 CHECK(uploads==343);CHECK(g_cram_vertex_top==CRAM_VERTEX_END);
 printf("RESULT PASS calls=%u full_pool_entries=343 bounded_collision_entries=16 uploads=%u words=%u\n",calls,uploads,upload_words);
}
'''
def main():
 OUT.mkdir(exist_ok=False)
 s=SRC.read_text();a=s.index('#define CRAM_VERTEX_BEGIN');b=s.index('\n}',s.index('int gfx_gpu_cram_vertex_source(',a))+2
 fragment=s[a:b]
 (OUT/'instantiated.c').write_text(PRE+fragment+POST)
 (OUT/'recipe.py').write_text(Path(__file__).read_text())
 cmd=['cc','-std=c11','-O2','-Wall','-Wextra','-Wno-misleading-indentation','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(OUT/'instantiated.c'),'-o',str(OUT/'test')]
 (OUT/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
 with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0')
 (OUT/'run-environment.json').write_text(json.dumps({'ASAN_OPTIONS':env['ASAN_OPTIONS']})+'\n')
 with (OUT/'run.log').open('x') as f:r=subprocess.run([str(OUT/'test')],stdout=f,stderr=subprocess.STDOUT,env=env)
 result=dict(passed=r.returncode==0 and 'RESULT PASS' in (OUT/'run.log').read_text(),returncode=r.returncode,source_sha256=sha(SRC),c_fragment_sha256=hashlib.sha256(fragment.encode()).hexdigest(),scope='Actual frozen cache C instantiated; host uintptr size differs; no target CPU or hardware performance claim.')
 (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print((OUT/'run.log').read_text());assert result['passed']
if __name__=='__main__':main()
