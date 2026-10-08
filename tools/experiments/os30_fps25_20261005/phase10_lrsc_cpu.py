#!/usr/bin/env python3
"""Actual retained-memory CPU tests for private LR/SC-only generation."""
import argparse,hashlib,json,os,re,shlex,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/lrsc-only-v1'
BASE=ROOT/'docs/measurements/os30-dual-20261004/validation/baseline128'
KBASE=ROOT/'docs/measurements/os30-simple-20261005/validation/cpu-kernel'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')

def model():
 dest=OUT/'cpu-model';dest.mkdir(exist_ok=False);frozen=dest/'frozen';manifest=json.loads((BASE/'build-manifest.json').read_text());files={}
 for name,h in manifest['sources'].items():
  p=Path(name);assert sha(p)==h,p;q=frozen/p.relative_to(ROOT);q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q);files[str(q.relative_to(dest))]=h
 oldcpu=frozen/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';newcpu=OUT/'candidate/VexiiRiscv.v';assert sha(newcpu)==json.loads((OUT/'candidate/generated.json').read_text())['rtl_sha256'];shutil.copy2(newcpu,oldcpu);files[str(oldcpu.relative_to(dest))]=sha(oldcpu)
 wrapper=dest/'test.mk';wrapper.write_text((BASE/'test.mk').read_text().replace(str(ROOT),str(frozen)))
 main=frozen/(BASE/'main.cpp').relative_to(ROOT)
 # Reuse the exact expanded retained Verilator invocation; avoid unrelated
 # Make prerequisites that regenerate an unused MiSTer SDRAM adapter.
 lines=(BASE/'build.log').read_text().splitlines();start=next(i for i,l in enumerate(lines) if l.startswith('verilator '));selected=[]
 for line in lines[start:]:
  selected.append(line.rstrip('\\'))
  if not line.endswith('\\'):break
 cmd=shlex.split(' '.join(selected));cmd=[str(dest/'obj') if x==str(BASE/'obj') else x.replace(str(ROOT),str(frozen)) for x in cmd]

 with (dest/'build.log').open('x') as f:subprocess.run(cmd,cwd=frozen/'src/fpga/test',stdout=f,stderr=subprocess.STDOUT,check=True)
 save(dest/'build.json',dict(command=cmd,simulator_sha256=sha(dest/'obj/Vtb_system'),candidate_cpu_sha256=sha(newcpu),files_sha256=files,wrapper_sha256=sha(wrapper)))
 print('candidate CPU model compiled')

START=r'''.section .text.start,"ax"
.global _start
_start:
.option push
.option norelax
la gp,__global_pointer$
.option pop
li sp,0x103ffff0
li t0,0x6000
csrs mstatus,t0
la t0,trap
csrw mtvec,t0
la t0,__bss_start
la t1,__bss_end
1: bgeu t0,t1,2f
sw zero,0(t0)
addi t0,t0,4
j 1b
2: call main
3: j 3b
.balign 4
trap:
addi sp,sp,-16
sw t0,0(sp)
sw t1,4(sp)
csrr t1,mcause
la t0,last_cause
sw t1,0(t0)
csrr t1,mtval
la t0,last_tval
sw t1,0(t0)
la t0,traps
lw t1,0(t0)
addi t1,t1,1
sw t1,0(t0)
csrr t0,mepc
addi t0,t0,4
csrw mepc,t0
lw t0,0(sp)
lw t1,4(sp)
addi sp,sp,16
mret
'''
C=r'''#include <stdint.h>
volatile uint32_t traps,last_cause,last_tval;
static volatile uint32_t mem[32768] __attribute__((aligned(64)));
static void put(char c){while(!(*(volatile uint32_t*)0x4f000000&2)){}*(volatile uint32_t*)0x4f000004=(unsigned char)c;}
static void text(const char*s){while(*s)put(*s++);}
static void hex(uint32_t v){for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(uint32_t n,uint32_t a,uint32_t b){text("KERNEL FAIL ");hex(n);hex(a);hex(b);put('\n');for(;;){}}
#define CHECK(n,a,b) do{uint32_t aa=(a),bb=(b);if(aa!=bb)fail(n,aa,bb);}while(0)
static unsigned timer(void){__asm__ volatile("":::"memory");unsigned n=*(volatile unsigned*)0x40000004;__asm__ volatile("":::"memory");return n;}
#define LRSC(SUFFIX) __asm__ volatile("lr.w" SUFFIX " %0,(%2); addi %1,%0,1; sc.w" SUFFIX " %1,%1,(%2)":"=&r"(old),"=&r"(status):"r"(p):"memory")
static unsigned increment(volatile uint32_t *p,unsigned mode){unsigned old,status,tries=0;do{switch(mode){case 0:LRSC("");break;case 1:LRSC(".aq");break;case 2:LRSC(".rl");break;default:LRSC(".aqrl");break;}if(++tries>10000)fail(1,mode,tries);}while(status);return old;}
static unsigned workload(unsigned count){unsigned hash=2166136261u;for(unsigned i=0;i<count;i++){
 unsigned ix=((i*977u)&32767u);volatile uint32_t *p=&mem[ix];unsigned v=i*0x10204081u;*p=v;
 unsigned old=increment(p,i&3);CHECK(2,old,v);CHECK(3,*p,v+1);hash=(hash^old)*16777619u;
 unsigned status,value;__asm__ volatile("sc.w %0,zero,(%1)":"=&r"(status):"r"(p):"memory");CHECK(4,status,1);CHECK(5,*p,v+1);
 /* Any intervening store kills this implementation's reservation. */
 __asm__ volatile("lr.w %0,(%2); sw %4,0(%3); sc.w %1,zero,(%2)":"=&r"(value),"=&r"(status):"r"(p),"r"(&mem[(ix+1)&32767]),"r"(v):"memory");CHECK(6,status,1);CHECK(7,*p,v+1);
 }return hash;}
static unsigned expected_amo(unsigned fn,unsigned a,unsigned b){switch(fn){case 1:return b;case 0:return a+b;case 4:return a^b;case 12:return a&b;case 8:return a|b;case 16:return (int32_t)a<(int32_t)b?a:b;case 20:return (int32_t)a>(int32_t)b?a:b;case 24:return a<b?a:b;default:return a>b?a:b;}}
AMO_FUNCTIONS
int main(void){unsigned misa;__asm__ volatile("csrr %0,misa":"=r"(misa));unsigned fullA=misa&1;text("MISA ");hex(misa);put('\n');
 unsigned warm=workload(512);CHECK(8,traps,0);unsigned start=timer();unsigned hash=workload(4096);unsigned elapsed=timer()-start;CHECK(9,traps,0);
 text("LRSC PASS ");hex(warm);hex(hash);hex(elapsed);put('\n');
 unsigned total=0;for(unsigned k=0;k<36;k++)for(unsigned v=0;v<4;v++){
 unsigned a=0x81234567u+v*0x31415927u,b=0x7e314159u-v*0x12893659u;mem[0]=a;unsigned prev=traps;
 unsigned old=amo_tests[k](&mem[0],b);unsigned op=amo_ops[k];
 if(fullA){CHECK(10,traps,prev);CHECK(11,old,a);CHECK(12,mem[0],expected_amo(op>>27,a,b));}
 else{CHECK(13,traps,prev+1);CHECK(14,last_cause,2);CHECK(15,last_tval,op);CHECK(16,mem[0],a);CHECK(17,old,0x5a5a1234u);}
 total++;
 }
 text("AMO CHECKS ");hex(total);hex(traps);put('\n');
 text("KERNEL PASS HAL init\n");return 0;}
'''

def firmware():
 out=OUT/'cpu-tests';out.mkdir(exist_ok=False);fw=out/'firmware';fw.mkdir()
 for n in ['link.ld','firmware.mif']:shutil.copy2(KBASE/'firmware'/n,fw/n)
 (fw/'start.S').write_text(START)
 functions=[];names=[];ops=[]
 for fn in [1,0,4,12,8,16,20,24,28]:
  for aqrl in range(4):
   op=(fn<<27)|(aqrl<<25)|(11<<20)|(10<<15)|(2<<12)|(12<<7)|0x2f;name=f'amo_{fn}_{aqrl}';names.append(name);ops.append(op)
   functions.append(f'''static __attribute__((noinline,noclone)) unsigned {name}(volatile unsigned *p,unsigned value){{register unsigned a0 __asm__("a0")=(unsigned)p;register unsigned a1 __asm__("a1")=value;register unsigned a2 __asm__("a2")=0x5a5a1234u;__asm__ volatile(".word 0x{op:08x}":"+r"(a2):"r"(a0),"r"(a1):"memory");return a2;}}''')
 functions.append('static unsigned (*const amo_tests[36])(volatile unsigned*,unsigned)={'+','.join(names)+'};')
 functions.append('static const unsigned amo_ops[36]={'+','.join(hex(x)+'u' for x in ops)+'};')
 (fw/'kernel.c').write_text(C.replace('AMO_FUNCTIONS','\n'.join(functions)))
 old=json.loads((KBASE/'manifest.json').read_text());cmd=[str(fw) if t==str(KBASE/'firmware') else t for t in old['compiler_command']];image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','openfpgaos-firmware'],text=True).strip();cmd=[image if t=='openfpgaos-firmware' else t for t in cmd]
 with (out/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 prefix=cmd[:cmd.index('riscv64-unknown-elf-gcc')];subprocess.run([*prefix,'riscv64-unknown-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],check=True)
 with (out/'kernel.asm').open('x') as f:subprocess.run([*prefix,'riscv64-unknown-elf-objdump','-d','kernel.elf'],stdout=f,check=True)
 save(out/'firmware.json',dict(command=cmd,firmware_sha256={p.name:sha(p) for p in fw.iterdir() if p.is_file()},asm_sha256=sha(out/'kernel.asm')))
 print('CPU ISA probe compiled')

def run():
 out=OUT/'cpu-tests';fw=out/'firmware';m=json.loads((out/'firmware.json').read_text())
 for n,h in m['firmware_sha256'].items():assert sha(fw/n)==h
 sims={'baseline':BASE/'obj/Vtb_system','candidate':OUT/'cpu-model/obj/Vtb_system'};rows=[]
 for label,exe in sims.items():
  for scan in (0,1):
   log=out/f'{label}-scan{scan}.log';cmd=[str(exe),'kernel.bin','10000000']
   with log.open('x') as f:subprocess.run(cmd,cwd=fw,env=dict(os.environ,SCAN_ENABLE=str(scan)),stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
   text=log.read_text();assert 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text,text[-2500:]
   assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
   misa=int(re.search(r'MISA ([0-9a-f]+)',text).group(1),16);assert bool(misa&1)==(label=='baseline')
   warm,checksum,cycles=[int(x,16) for x in re.search(r'LRSC PASS ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)',text).groups()]
   checks,traps=[int(x,16) for x in re.search(r'AMO CHECKS ([0-9a-f]+) ([0-9a-f]+)',text).groups()];assert checks==144 and traps==(0 if label=='baseline' else 144)
   rows.append(dict(label=label,scan=scan,misa=misa,lrsc_warm_hash=warm,lrsc_hash=checksum,lrsc_cycles=cycles,amo_checks=checks,illegal_instruction_traps=traps,simulator_sha256=sha(exe),command=cmd,log_sha256=sha(log)))
 for scan in (0,1):
  a,b=[r for r in rows if r['scan']==scan];assert a['lrsc_hash']==b['lrsc_hash'] and a['lrsc_warm_hash']==b['lrsc_warm_hash']
 save(out/'result.json',dict(passed=True,scope='Actual CPU+retained memory RTL;4 aq/rl LRSC forms, success/failure/store-kill on4592 iterations;36AMO formsx4values either baseline operation or precise illegal trap with no memory write.',runs=rows))
 print(json.dumps(rows))

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['model','firmware','run']);a=p.parse_args();globals()[a.action]()
