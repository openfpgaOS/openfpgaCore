#!/usr/bin/env python3
"""Private actual-A test of unchanged LOD6 ELF lighting/copy instruction bytes."""
import hashlib, json, os, shutil, struct, subprocess, sys, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=BASE/'lod6-client-native-review-v1'
APP=BASE/'lod6-driver-v1/app.elf'
OLD=BASE/'cpu-audit/lighting-native'
HOST=BASE/'cpu-audit'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def segments(path):
    b=path.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);out=[]
    assert h[0][:5]==b'\x7fELF\x01'
    for i in range(h[10]):
        p=struct.unpack_from('<8I',b,h[5]+i*h[9])
        if p[0]==1 and p[5]:out.append((p[2],p[5],b[p[1]:p[1]+p[4]]))
    return out

KERNEL=r'''
#include <stdint.h>
#include "vectors.h"
volatile unsigned traps,irq_count;
extern void invoke_light(unsigned,unsigned,const void*);
extern void invoke_copy_lit(void*,unsigned);
extern void invoke_copy(void*,unsigned);
#define RSP ((volatile unsigned char*)0x1115d908u)
#define DST ((volatile uint32_t*)(0x1115d908u+940))
#define MEMO ((volatile uint32_t*)0x1115cd08u)
#define EPOCH (*(volatile uint32_t*)0x10fda97cu)
#define VIEW ((volatile uint32_t*)0x13f80000u)
static unsigned char source[32][24] __attribute__((aligned(4)));
static unsigned checks,calls,lit_vertices,copy_vertices;
static void put(char c){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)c;}
static void text(const char*s){while(*s)put(*s++);}
static void hex(unsigned v){for(unsigned i=0;i<8;i++)put("0123456789abcdef"[(v>>(28-4*i))&15]);put(' ');}
static void eq(unsigned job,unsigned index,uint32_t got,uint32_t wanted){
 checks++;if(got!=wanted){text("KERNEL FAIL ");hex(job);hex(index);hex(got);hex(wanted);text("\n");for(;;){}}
}
static void context(const uint32_t *v){
 for(unsigned c=0;c<3;c++){
  *(volatile uint32_t*)(RSP+876+4*c)=v[1+c];
  RSP[849+c]=(v[4]>>(8*c))&255;
  RSP[837+c]=(v[5]>>(8*c))&255;
 }
}
static void input(unsigned i,uint32_t normal,unsigned alpha){
 for(unsigned b=0;b<24;b++)source[i][b]=(unsigned char)(0x40+i+b);
 for(unsigned c=0;c<3;c++)source[i][18+c]=(normal>>(8*c))&255;
 source[i][21]=alpha;
}
static void clear_dest(void){for(unsigned i=0;i<34*9;i++)DST[i]=0xa5a5a5a5u;}
static void check_dest(unsigned job,unsigned first,unsigned count,uint32_t rgb,unsigned alpha){
 for(unsigned i=0;i<34;i++)for(unsigned w=0;w<9;w++){
  uint32_t e=0xa5a5a5a5u;
  if(i>=first&&i<first+count&&w==7)e=rgb|(((alpha+i-first)&255)<<24);
  eq(job,i*9+w,DST[i*9+w],e);
 }
}
static void light(unsigned n,unsigned first){invoke_light(n,first,source);calls++;lit_vertices+=n;}
static uint32_t fp_int(int v){union{float f;uint32_t u;}r;r.f=(float)v;return r.u;}
int main(void){
 __asm__ volatile("csrw fcsr,zero":::"memory");text("LOD6_NATIVE_BEGIN\n");
 for(unsigned i=0;i<VECTOR_COUNT;i++){
  const uint32_t*v=vectors[i];unsigned first=i&31,alpha=i&255;
  unsigned key=v[0]&0xffffff,idx=(key^(key>>11))&255;
  uint32_t epoch=(i&1)?0xffffffffu:(i+1);
  clear_dest();context(v);input(0,key,alpha);EPOCH=epoch;
  // Same hash and epoch but different full normal must recompute.
  MEMO[idx*3]=epoch;MEMO[idx*3+1]=key^0x100u;MEMO[idx*3+2]=0xcc123456u;
  light(1,first);check_dest(i,first,1,v[6],alpha);
  eq(i,400,MEMO[idx*3],epoch);eq(i,401,MEMO[idx*3+1],key);
  eq(i,402,MEMO[idx*3+2],v[6]|0xcc000000u);
  // Deliberately changed coefficients prove the second call takes the memo.
  for(unsigned c=0;c<3;c++)*(volatile uint32_t*)(RSP+876+4*c)=0;
  input(0,key,alpha^255);light(1,first);
  eq(i,403,DST[first*9+7],v[6]|((alpha^255)<<24));
  // Epoch mismatch, including uint32 wrap, must recompute from current state.
  context(v);EPOCH=epoch+1;MEMO[idx*3+2]=0xcc123456u;
  input(0,key,alpha);light(1,first);
  eq(i,404,DST[first*9+7],v[6]|(alpha<<24));
  eq(i,405,MEMO[idx*3],epoch+1);
 }
 // Repeated normals within the same call: all sizes and both edge destinations.
 for(unsigned count=1;count<=32;count++)for(unsigned edge=0;edge<2;edge++){
  unsigned i=count-1,first=edge?32-count:0;const uint32_t*v=vectors[i];
  clear_dest();context(v);EPOCH=0x40000000u+count*2+edge;
  unsigned key=v[0],idx=(key^(key>>11))&255;
  MEMO[idx*3]=0;MEMO[idx*3+2]=0xcc123456u;
  for(unsigned n=0;n<count;n++)input(n,key,n+count);
  light(count,first);check_dest(2000+count*2+edge,first,count,v[6],count);
 }
 // Actual constant-propagated copy machine code, with raw rows in model SDRAM.
 // This checks copy arithmetic/ownership only, not the separate hardware alias.
 for(unsigned count=1;count<=32;count++)for(unsigned lit=0;lit<2;lit++){
  unsigned first=32-count;clear_dest();
  for(unsigned n=0;n<count;n++){
   for(unsigned w=0;w<8;w++)VIEW[n*8+w]=0x12000000u+n*256+w;
   uint16_t u=(uint16_t)(-32768+(int)(count*997+n*37));
   uint16_t v=(uint16_t)(32767-(int)(count*997+n*29));
   VIEW[n*8+5]=(uint32_t)u|((uint32_t)v<<16);
   DST[(first+n)*9+7]=0x80abcdefu+n;
  }
  if(lit)invoke_copy_lit((void*)(DST+first*9),count);
  else invoke_copy((void*)(DST+first*9),count);
  calls++;copy_vertices+=count;
  for(unsigned slot=0;slot<34;slot++)for(unsigned w=0;w<9;w++){
   uint32_t e=0xa5a5a5a5u;
   if(slot>=first&&slot<32){
    unsigned n=slot-first;
    if(w<5)e=VIEW[n*8+w];
    else if(w==5)e=fp_int((int16_t)VIEW[n*8+5]);
    else if(w==6)e=fp_int((int16_t)(VIEW[n*8+5]>>16));
    else if(w==7)e=lit?0x80abcdefu+n:VIEW[n*8+6];
    else e=VIEW[n*8+7];
   }
   eq(3000+count*2+lit,slot*9+w,DST[slot*9+w],e);
  }
 }
 text("COUNTS ");hex(checks);hex(calls);hex(lit_vertices);hex(copy_vertices);
 text("\nLOD6_NATIVE_COMPLETE\nKERNEL PASS HAL init\n");return 0;
}
'''

def prepare():
    OUT.mkdir(exist_ok=False);f=OUT/'firmware';f.mkdir()
    assert sha(APP)=='5ec576552eadd0e359a31abdf62eba4e30380e2a23af149e53b86195a5311b9e'
    rows=[[int(s,16) for s in line.split()] for line in (OLD/'native-golden.txt').read_text().splitlines()]
    assert len(rows)==1227 and all(len(r)==9 and r[0]==i for i,r in enumerate(rows))
    (f/'vectors.h').write_text('#define VECTOR_COUNT 1227u\nstatic const uint32_t vectors[VECTOR_COUNT][7]={\n'+',\n'.join('{'+','.join('0x%08xu'%v for v in r[1:8])+'}' for r in rows)+'\n};\n')
    (f/'kernel.c').write_text(KERNEL)
    for n in ['start.S','link.ld','firmware.mif']:shutil.copy2(OLD/'firmware'/n,f/n)
    asm='.section .text.invoke,"ax"\n'
    for name,addr in [('invoke_light',0x104ce680),('invoke_copy_lit',0x104cf2c0),('invoke_copy',0x104cf340)]:
        asm+=f'''.global {name}
.type {name},@function
{name}:
 addi sp,sp,-16
 sw ra,12(sp)
 sw gp,8(sp)
 li gp,0x10fdae98
 li t0,0x{addr:x}
 jalr ra,t0,0
 lw gp,8(sp)
 lw ra,12(sp)
 addi sp,sp,16
 ret
.size {name},.-{name}
'''
    (f/'invoke.S').write_text(asm)
    manifest=json.loads((OLD/'manifest.json').read_text())
    cmd=[t.replace(str(OLD/'firmware'),str(f)) for t in manifest['compiler_command']]
    cmd.insert(cmd.index('kernel.c'),'invoke.S')
    (OUT/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
    print('prepared',OUT)

def build():
    f=OUT/'firmware';cmd=json.loads((OUT/'build-command.json').read_text())
    with (OUT/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (OUT/'fixture.asm').open('x') as log:subprocess.run(['llvm-objdump','-d','--mattr=+m,+f,+c',str(f/'kernel.elf')],stdout=log,check=True)
    segs=[('app',*s) for s in segments(APP)]+[('fixture',*s) for s in segments(f/'kernel.elf')]
    for i,(name,a,n,b) in enumerate(segs):
        assert a>=0x10320000 and a+n<0x13a00000
        for _,aa,nn,bb in segs[:i]:assert a+n<=aa or aa+nn<=a
    image=bytearray(max(a+n for _,a,n,b in segs)-0x10320000)
    for _,a,n,b in segs:image[a-0x10320000:a-0x10320000+len(b)]=b
    (f/'kernel.bin').write_bytes(image)
    assert struct.unpack_from('<I',image,0x10d3b9bc-0x10320000)[0]==0x3c010204
    nm=subprocess.check_output(['llvm-nm','-S',str(APP)],text=True);(OUT/'app-symbols.txt').write_text(nm)
    selected={}
    for line in nm.splitlines():
        p=line.split()
        if len(p)==4 and p[3] in ('load_alias_light','load_alias_copy_lit.constprop.0','load_alias_copy.constprop.0'):
            a,n=int(p[0],16),int(p[1],16)
            original=next(b[a-aa:a-aa+n] for aa,nn,b in segments(APP) if aa<=a and a+n<=aa+len(b))
            assert image[a-0x10320000:a-0x10320000+n]==original
            selected[p[3]]=dict(address=a,size=n,original_bytes_sha256=hashlib.sha256(original).hexdigest())
    assert len(selected)==3
    result=dict(app_elf_sha256=sha(APP),function_instruction_bytes_retained=True,functions=selected,
      host_manifest_sha256=sha(HOST/'host-manifest.json'),native_golden_sha256=sha(OLD/'native-golden.txt'),
      native_golden_count=1227,app_load_segments_retained=True,
      firmware_sha256={p.name:sha(p) for p in f.iterdir() if p.is_file()},
      evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),APP,OUT/'build-command.json',OUT/'build.log',OUT/'fixture.asm',OLD/'final-manifest.json']})
    (OUT/'firmware-manifest.json').write_text(json.dumps(result,indent=2)+'\n');print('built',len(image))

def run(mode):
    assert mode in ('quiet','nominal')
    h=json.loads((HOST/'host-manifest.json').read_text());assert sha(HOST/'Vtb_system')==h['simulator_sha256']
    for p,v in h['unchanged_rtl_object_sha256'].items():assert sha(ROOT/p)==v,p
    m=json.loads((OUT/'firmware-manifest.json').read_text())
    for n,v in m['firmware_sha256'].items():assert sha(OUT/'firmware'/n)==v,n
    env=dict(SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
    cmd=[str(HOST/'Vtb_system'),'kernel.bin','60000000'];begin=time.monotonic()
    (OUT/(mode+'-invocation.json')).write_text(json.dumps(dict(command=cmd,environment=env,firmware_manifest_sha256=sha(OUT/'firmware-manifest.json')),indent=2)+'\n')
    with (OUT/(mode+'.log')).open('x') as log:
        p=subprocess.run(cmd,cwd=OUT/'firmware',env=dict(os.environ,**env),stdout=log,stderr=subprocess.STDOUT,timeout=240)
    s=(OUT/(mode+'.log')).read_text()
    ok=p.returncode==0 and 'LOD6_NATIVE_COMPLETE' in s and 'KERNEL PASS HAL init' in s and 'KERNEL FAIL' not in s and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in s
    import re
    match=re.search(r'COUNTS ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)',s)
    result=dict(passed=ok,returncode=p.returncode,elapsed_seconds=time.monotonic()-begin,
      counts=dict(zip(['comparisons','helper_calls','lit_vertices','copied_vertices'],[int(x,16) for x in match.groups()])) if match else None,
      log_sha256=sha(OUT/(mode+'.log')),invocation_sha256=sha(OUT/(mode+'-invocation.json')))
    (OUT/(mode+'-result.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result));assert ok

if __name__=='__main__':
    action=sys.argv[1]
    if action=='prepare':prepare()
    elif action=='build':build()
    elif action=='run':run(sys.argv[2])
    else:raise SystemExit('prepare | build | run quiet/nominal')
