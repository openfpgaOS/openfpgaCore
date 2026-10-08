#!/usr/bin/env python3
"""Private native directed FP RAW/WAW/freeze/redirect sequence for RS3 trial."""
import hashlib,json,os,re,shutil,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
N=P/'cpu-fp-rs3-mux-v1/native';D=P/'cpu-fp-rs3-mux-v1/hazards';F=D/'firmware'
OLD=P/'cpu-audit/lighting-native'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
 F.mkdir(parents=True,exist_ok=False)
 for n in ('start.S','firmware.mif','link.ld'):shutil.copy2(OLD/'firmware'/n,F/n)
 source='''#include <stdint.h>
volatile unsigned traps,irq_count;
static void put(char c){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)c;}
static void text(const char*s){while(*s)put(*s++);}
static void hex(unsigned v){for(unsigned i=0;i<8;i++)put("0123456789abcdef"[(v>>(28-4*i))&15]);put(' ');}
'''
 for gap in range(13):
  asm=['li t0,0x3f800000','fmv.w.x f1,t0','li t0,0x40000000','fmv.w.x f2,t0','li t0,0x40400000','fmv.w.x f3,t0','li t0,0x40800000','fmv.w.x f4,t0',
       'fmadd.s f6,f1,f2,f3','fmadd.s f6,f2,f2,f3','fmadd.s f6,f3,f2,f4']+['addi zero,zero,0']*gap+[
       'fmadd.s f7,f1,f2,f6','fmadd.s f7,f2,f2,f7','fdiv.s f8,f7,f2','fmadd.s f9,f1,f2,f8','fmv.x.w %0,f9']
  source+='__attribute__((noinline))static uint32_t gap%d(void){uint32_t out;__asm__ volatile('%gap+json.dumps('\n'.join(asm))+':"=r"(out)::"t0","f1","f2","f3","f4","f6","f7","f8","f9");return out;}\n'
 source+='int main(void){__asm__ volatile("csrw fcsr,zero":::"memory");text("RS3_HAZARDS_BEGIN\\n");unsigned bad=0;for(unsigned i=0;i<16;i++){\n'
 for gap in range(13):source+=' {uint32_t v=gap%d();bad|=(v!=0x41200000u);text("RS3_HAZARD ");hex(i*13+%d);hex(v);text("\\n");}\n'%(gap,gap)
 source+='__asm__ volatile("ecall":::"memory");}\nif(bad||traps!=16)text("KERNEL FAIL RS3\\n");else text("RS3_HAZARDS_COMPLETE\\nKERNEL PASS HAL init\\n");return 0;}\n'
 (F/'kernel.c').write_text(source)
 cmd=[x.replace(str(OLD/'firmware'),str(F)) for x in json.loads((OLD/'manifest.json').read_text())['compiler_command']]
 save(D/'build-command.json',cmd);shutil.copy2(__file__,D/'recipe.py')
def build():
 cmd=json.loads((D/'build-command.json').read_text())
 with (D/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 with (D/'kernel.asm').open('x') as f:subprocess.run(['llvm-objdump','-d','--mattr=+m,+f,+c',str(F/'kernel.elf')],stdout=f,check=True)
 blob=(F/'kernel.elf').read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',blob);segs=[]
 for i in range(h[10]):
  q=struct.unpack_from('<8I',blob,h[5]+i*h[9])
  if q[0]==1 and q[5]:segs.append((q[2],q[5],blob[q[1]:q[1]+q[4]]))
 start=0x10320000;end=max(a+n for a,n,d in segs);data=bytearray(end-start)
 for a,n,d in segs:assert start<=a<end;data[a-start:a-start+len(d)]=d
 (F/'kernel.bin').write_bytes(data)
 save(D/'firmware.json',{p.name:sha(p) for p in F.iterdir() if p.is_file()})
def run(mode):
 assert mode in ('quiet','nominal')
 env=dict(SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
 cmd=[str(N/'obj/Vtb_system'),'kernel.bin','30000000'];save(D/(mode+'-invocation.json'),dict(command=cmd,environment=env,simulator_sha256=sha(N/'obj/Vtb_system')))
 with (D/(mode+'.log')).open('x') as f:r=subprocess.run(cmd,cwd=F,env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,timeout=300)
 raw=(D/(mode+'.log')).read_text();t=re.sub(r'\n?\[c=\d+\][^\n]*\n    m1_ar:[^\n]*\n    arb_st=[^\n]*\n','',raw.split('=== UART BEGIN ===')[0])
 rows=[tuple(int(x,16) for x in m) for m in re.findall(r'RS3_HAZARD ([0-9a-f]{8}) ([0-9a-f]{8})',t)]
 assert rows==[(i,0x41200000) for i in range(208)],(len(rows),rows[:2])
 assert r.returncode==0 and 'RS3_HAZARDS_COMPLETE' in t and 'KERNEL FAIL' not in raw and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in raw
 m=re.search(r'FP_RS3_COVER checks=(\d+) freezes=(\d+) redirects=(\d+) multiple=(\d+) seen=([0-9a-f]+)',raw);assert m
 coverage=dict(zip(('checks','freezes','redirects','multiple','seen'),[int(x,16 if i==4 else 10) for i,x in enumerate(m.groups())]))
 assert all(coverage[k]>0 for k in ('checks','freezes','redirects'))
 save(D/(mode+'-result.json'),dict(passed=True,exact_results=208,gaps=list(range(13)),synchronous_traps=16,coverage=coverage,log_sha256=sha(D/(mode+'.log'))));print(coverage)
if __name__=='__main__':globals()[sys.argv[1]](*sys.argv[2:])
