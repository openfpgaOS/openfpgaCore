#!/usr/bin/env python3
"""ASan/UBSan boundary proof using the exact private recycling cache C."""
import hashlib,json,os,subprocess
from pathlib import Path
from phase10_lod5_client_review import PRE
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
SRC=P/'lod5-recycle-driver-v1/gfx_gpu.c';OUT=P/'lod5-recycle-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
POST=r'''
static void reset(void){memset(g_cram_vertex_source,0,sizeof(g_cram_vertex_source));
 memset(cram_vertex_stats,0,sizeof(cram_vertex_stats));memset(cram_vertex_recycle_stats,0,sizeof(cram_vertex_recycle_stats));
 g_cram_vertex_top=CRAM_VERTEX_BEGIN;g_cram_vertex_enabled=1;uploads=upload_words=0;
 g_cram_vertex_frame=g_cram_vertex_last_recycle=g_cram_vertex_recycled=0;
 memset(physical,0xa7,sizeof(physical));}
static uintptr_t matching(unsigned h,unsigned n,unsigned serial){
 uintptr_t p=0x10400000u+serial*0x10000u;
 for(;;p+=4)if((((p>>4)^(p>>14)^(n*33u))&1023)==h)return p;
}
static unsigned entries(void){unsigned n=0;for(unsigned i=0;i<1024;i++)if(g_cram_vertex_source[i].source)n++;return n;}
static uint32_t call(uintptr_t p,unsigned count,int expected){
 uint32_t offset=0xdeadbeefu;unsigned before=uploads;calls++;
 CHECK(gfx_gpu_cram_vertex_source((const void*)p,count,&offset)==expected);
 if(expected){CHECK(!(offset&63));CHECK(offset>=0xfc0000 && (uint64_t)offset+count*24<=0x1000000);
   for(unsigned i=0;i<count*6;i++)CHECK(physical[offset/4+i]==word(p,i));
 }else{CHECK(offset==0xdeadbeefu);CHECK(uploads==before);}
 CHECK(physical[0x003c0000/4]==0xa7a7a7a7u);CHECK(physical[0x00fbfffc/4]==0xa7a7a7a7u);
 return offset;
}
static uintptr_t keys[343];static uint32_t offsets[343];static unsigned counts[343];
static void full(unsigned serial){
 for(unsigned i=0;i<343;i++){
  counts[i]=i<341?32:i==341?8:1;keys[i]=matching(i,counts[i],serial+i);
  offsets[i]=call(keys[i],counts[i],1);
 }
 CHECK(g_cram_vertex_top==CRAM_VERTEX_END);CHECK(entries()==343);
}
// Refill after one64-byte entry has started a fresh generation. Force one
// former key to miss, then exactly fill the remaining262080bytes.
static void refill(uintptr_t old,unsigned serial){
 unsigned before=uploads;uint32_t p=call(old,32,1);CHECK(uploads==before+1);CHECK(p==CRAM_VERTEX_BEGIN+64);
 for(unsigned i=1;i<341;i++)call(matching(i,32,serial+i),32,1);
 call(matching(341,8,serial+341),8,1);
 CHECK(g_cram_vertex_top==CRAM_VERTEX_END);CHECK(entries()==343);
}
int main(void){
 reset();g_cram_vertex_enabled=0;call(0x10400000,1,0);g_cram_vertex_enabled=1;
 call(0x10400000,0,0);call(0x10400000,33,0);call(0x10400001,1,0);
 CHECK(cram_vertex_recycle_stats[0]==0);
 uintptr_t same=matching(0,1,0);uint32_t a=call(same,1,1),b=call(same,2,1);CHECK(a!=b);
 unsigned u=uploads;CHECK(call(same,1,1)==a);CHECK(call(same,2,1)==b);CHECK(uploads==u);
 reset();uintptr_t collision[17];
 for(unsigned i=0;i<17;i++)collision[i]=matching(1020,32,i);
 for(unsigned i=0;i<16;i++)call(collision[i],32,1);
 uint32_t top=g_cram_vertex_top;call(collision[16],32,0);CHECK(g_cram_vertex_top==top);
 CHECK(cram_vertex_recycle_stats[0]==0);
 // Even with no capacity, a sixteen-probe collision does not clear the pool.
 g_cram_vertex_top=CRAM_VERTEX_END;call(collision[16],32,0);CHECK(cram_vertex_recycle_stats[0]==0);
 for(unsigned i=0;i<16;i++)call(collision[i],32,1);CHECK(uploads==16);
 reset();full(0);uintptr_t old=keys[0];u=uploads;
 // A full pool remains usable for all old hits; misses trigger the first recycle.
 for(unsigned i=0;i<343;i++)CHECK(call(keys[i],counts[i],1)==offsets[i]);CHECK(uploads==u);
 uintptr_t fresh=matching(900,1,400);CHECK(call(fresh,1,1)==CRAM_VERTEX_BEGIN);
 CHECK(cram_vertex_recycle_stats[0]==1 && entries()==1 && g_cram_vertex_last_recycle==0);
 refill(old,512);u=uploads;
 for(unsigned frame=0;frame<120;frame++){
  g_cram_vertex_frame=frame;call(matching(901,1,901),1,0);
  CHECK(call(fresh,1,1)==CRAM_VERTEX_BEGIN);CHECK(uploads==u);
 }
 CHECK(cram_vertex_recycle_stats[0]==1 && cram_vertex_recycle_stats[1]==120);
 g_cram_vertex_frame=120;uintptr_t second=matching(902,1,902);
 CHECK(call(second,1,1)==CRAM_VERTEX_BEGIN);CHECK(cram_vertex_recycle_stats[0]==2 && entries()==1);
 refill(old,1024);u=uploads;
 g_cram_vertex_frame=239;call(matching(903,1,903),1,0);CHECK(uploads==u);
 g_cram_vertex_frame=240;CHECK(call(matching(904,1,904),1,1)==CRAM_VERTEX_BEGIN);
 CHECK(cram_vertex_recycle_stats[0]==3 && entries()==1);
 // Natural uint32 frame wrap:119 remains throttled;120 is allowed.
 reset();full(0);old=keys[0];g_cram_vertex_frame=0xffffffc0u;
 fresh=matching(900,1,400);CHECK(call(fresh,1,1)==CRAM_VERTEX_BEGIN);
 refill(old,512);u=uploads;
 g_cram_vertex_frame=0x37u;call(matching(901,1,901),1,0);CHECK(uploads==u);
 CHECK(cram_vertex_recycle_stats[0]==1);
 // Existing hits survive even after wrap and on the cooldown boundary.
 for(unsigned i=0;i<100000;i++)CHECK(call(fresh,1,1)==CRAM_VERTEX_BEGIN);
 CHECK(uploads==u);
 g_cram_vertex_frame=0x38u;CHECK(call(matching(902,1,902),1,1)==CRAM_VERTEX_BEGIN);
 CHECK(cram_vertex_recycle_stats[0]==2 && entries()==1);
 printf("RESULT PASS calls=%u first_recycle=1 cooldown119_rejected=1 cooldown120_allowed=1 wrap119_rejected=1 wrap120_allowed=1 probe_exhaustion_preserves_pool=1\n",calls);
}
'''
def main():
 OUT.mkdir(exist_ok=False);s=SRC.read_text()
 assert sha(SRC)=='4e0b8cfde1501f0035756987876aa4a2b3cdd18cbbbb08478a4cff5419938980'
 a=s.index('#define CRAM_VERTEX_BEGIN');b=s.index('\n}',s.index('int gfx_gpu_cram_vertex_source(',a))+2
 fragment=s[a:b];assert s.count('++g_cram_vertex_frame;')==1
 (OUT/'instantiated.c').write_text(PRE+fragment+POST);(OUT/'recipe.py').write_text(Path(__file__).read_text())
 cmd=['cc','-std=c11','-O2','-Wall','-Wextra','-Wno-misleading-indentation','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(OUT/'instantiated.c'),'-o',str(OUT/'test')]
 (OUT/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
 with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 with (OUT/'run.log').open('x') as f:r=subprocess.run([str(OUT/'test')],stdout=f,stderr=subprocess.STDOUT,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
 result=dict(passed=r.returncode==0 and 'RESULT PASS' in (OUT/'run.log').read_text(),returncode=r.returncode,source_sha256=sha(SRC),fragment_sha256=hashlib.sha256(fragment.encode()).hexdigest(),scope='Actual cache/recycle C, simulated physical upload observer, ASan/UBSan. Host pointer width differs; tests use32-bit address values. Frame age assigned to test exact unsigned boundaries; full app frame-call placement is separately reviewed. No asynchronous caller or performance claim.')
 (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print((OUT/'run.log').read_text());assert result['passed']
 files=[p for p in OUT.iterdir() if p.is_file()]+[SRC,SRC.parent/'source.json',SRC.parent/'candidate.patch',Path(__file__),Path(__file__).with_name('phase10_lod5_client_review.py')]
 (OUT/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in files}),indent=2)+'\n')
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
