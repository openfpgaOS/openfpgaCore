#!/usr/bin/env python3
"""Actual A CPU check of the bounded vertex-slot quotient; no FPGA rebuild."""
from common import *
import csv, os, re, shutil, subprocess, concurrent.futures, statistics

out=OUT/'cpu-slot-kernel';out.mkdir(exist_ok=False)
old=OUT/'cpu-kernel';fw=out/'firmware';fw.mkdir()
original=read(old/'manifest.json');exe=old/'Vtb_system'
assert sha(exe)==original['simulator_sha256']
for name in ['start.S','link.ld','firmware.mif']:shutil.copy2(old/'firmware'/name,fw/name)
parent=OUT.parent/'baseline/gfx_pc.c';candidate=OUT.parent/'slot-index-v1/gfx_pc.c'
s=parent.read_text();c=candidate.read_text()
struct=s[s.index('struct LoadedVertex {'):s.index('\n};',s.index('struct LoadedVertex {'))+3]
helper=c[c.index('static inline __attribute__((always_inline)) size_t gfx_vc_slot_index'):c.index('\n}\n',c.index('static inline __attribute__((always_inline)) size_t gfx_vc_slot_index'))+3]
assert 'const size_t idx = offset / sizeof(struct LoadedVertex);' in s
assert 'const size_t idx = gfx_vc_slot_index(offset);' in c
assert 'if (offset >= 32 * sizeof(struct LoadedVertex))' in s and 'if (offset >= 32 * sizeof(struct LoadedVertex))' in c
observed=OUT/'capture-resident-observed-normal/resident-calls.csv'
evidence=read(OUT/'resident-observer-results.json');assert evidence['passed_noninterference'] and sha(observed)==evidence['observation_sha256']
rows=[r for r in csv.DictReader(observed.open()) if int(r['frame'])==360]
offsets=[36*int(r['slot'+str(i)]) for r in rows for i in range(3)]
assert offsets and all(0<=v<1152 for v in offsets)
head='''#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
typedef float rspv_t;
struct RGBA {uint8_t r,g,b,a;};
volatile unsigned traps,irq_count;
'''+struct+'\n'+helper+'\n'
wrappers=''
for name,expr in [('reference','offset / sizeof(struct LoadedVertex)'),('candidate','gfx_vc_slot_index(offset)')]:
 wrappers+='''static __attribute__((noinline)) unsigned '''+name+'''_slot(const struct LoadedVertex *v,const struct LoadedVertex *base) {
 const uintptr_t offset=(uintptr_t)v-(uintptr_t)base;
 if(offset>=32*sizeof(struct LoadedVertex))return ~0u;
 return '''+expr+''';
}
'''
tail='''
static void put(char c) {while(!(*(volatile unsigned *)0x4f000000u&2)){} *(volatile unsigned *)0x4f000004u=(unsigned char)c;}
static void puts_uart(const char*s) {while(*s)put(*s++);}
static void hex(unsigned v) {for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(void) {puts_uart("KERNEL FAIL\\n");for(;;){}}
#define assert(x) do {if(!(x))fail();}while(0)
static unsigned cycle(void) {return *(volatile unsigned *)0x40000004u;}
static unsigned fold(unsigned h,unsigned x) {return (h^x)*16777619u;}
static struct LoadedVertex vertices[32];
static const struct LoadedVertex *at(unsigned offset) {return (const struct LoadedVertex *)((uintptr_t)vertices+offset);}
static __attribute__((noinline)) unsigned reference_batch(void) {
 unsigned h=2166136261u;for(unsigned i=0;i<JOBS;i++)h=fold(h,reference_slot(at(offsets[i]),vertices));return h;
}
static __attribute__((noinline)) unsigned candidate_batch(void) {
 unsigned h=2166136261u;for(unsigned i=0;i<JOBS;i++)h=fold(h,candidate_slot(at(offsets[i]),vertices));return h;
}
static volatile unsigned eviction[65536],eviction_sink;
static void cold_prepare(void) {unsigned h=0;for(unsigned i=0;i<65536;i+=16)h+=eviction[i];eviction_sink=h;__asm__ volatile("fence.i":::"memory");}
int main(void) {
 assert(sizeof(struct LoadedVertex)==36);
 for(unsigned offset=0;offset<1152;offset++)assert(reference_slot(at(offset),vertices)==candidate_slot(at(offset),vertices));
 unsigned outside[]={1152,1153,4096,0x7fffffffu,0x80000000u,0xfffffffeu,0xffffffffu};
 for(unsigned i=0;i<sizeof(outside)/sizeof(outside[0]);i++)assert(reference_slot(at(outside[i]),vertices)==~0u && candidate_slot(at(outside[i]),vertices)==~0u);
 unsigned expected=reference_batch();assert(candidate_batch()==expected);
 puts_uart("INPUTS ");hex(JOBS);puts_uart("\\n");
 for(unsigned cold=0;cold<2;cold++)for(unsigned order=0;order<2;order++)for(unsigned j=0;j<2;j++) {
  unsigned mode=order?1-j:j;
  if(cold)cold_prepare();else assert((mode?candidate_batch():reference_batch())==expected);
  puts_uart("RUN ");hex(cold);hex(order);hex(mode);put('@');
  unsigned start=cycle(),got=mode?candidate_batch():reference_batch(),elapsed=cycle()-start;
  put('#');puts_uart(" TIMER ");hex(elapsed);hex(got);puts_uart("\\n");assert(got==expected);
 }
 puts_uart("KERNEL PASS HAL init\\n");return 0;
}
'''
(fw/'kernel.c').write_text(head+wrappers+'\n#define JOBS '+str(len(offsets))+'u\nstatic const volatile unsigned offsets[JOBS]={'+','.join(map(str,offsets))+'};\n'+tail)
cmd=list(original['compiler_command']);oldfw=str(old/'firmware')
cmd=[str(fw) if t==oldfw else t for t in cmd]
with (out/'firmware-build.log').open('w') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
gcc=cmd.index('riscv64-unknown-elf-gcc');container=cmd[:gcc]
subprocess.run([*container,'riscv64-unknown-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],check=True)
with (out/'kernel.asm').open('w') as f:subprocess.run([*container,'riscv64-unknown-elf-objdump','-d','kernel.elf'],stdout=f,check=True)
asm=(out/'kernel.asm').read_text()
def body(name):
 m=re.search(r'<'+name+r'(?:\.constprop\.\d+)?>:\n(.*?)(?=\n[0-9a-f]+ <)',asm,re.S);assert m,name;return m.group(1)
assert '\tdivu\t' in body('reference_slot') and '\tdivu\t' not in body('candidate_slot')
manifest={'cpu_sha256':original['cpu_sha256'],'simulator_sha256':sha(exe),'parent_cpu_manifest_sha256':sha(old/'manifest.json'),'unchanged_rtl_object_sha256':original['unchanged_rtl_object_sha256'],'observed_frame':360,'triangles':len(rows),'slot_conversions':len(offsets),'all_32_slots_seen':len(set(offsets))==32,'source_hashes':{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),parent,candidate,observed,OUT/'resident-observer-results.json',fw/'kernel.c',out/'kernel.asm']},'firmware_sha256':{p.name:sha(p) for p in fw.iterdir() if p.is_file()},'compiler_command':cmd,'assembly_divu_check_passed':True,'scope':'Extracted exact bounded slot conversion plus original range guard, exercised on observed emitted triangle slots. Actual A CPU/fabric RTL. Calls/hash/load/marker harness overhead is common. This is a component test, not whole-game FPS.'}
(out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
def run(mode):
 env=dict(os.environ,SCAN_ENABLE='1' if mode=='scanout' else '0',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
 log=out/(mode+'.log')
 with log.open('w') as f:subprocess.run([str(exe),'kernel.bin','60000000'],cwd=fw,env=env,stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
 text=log.read_text();assert 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
 return mode
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 for mode in pool.map(run,['quiet','scanout']):print('PASS',mode,flush=True)
result={'passed':True,'scope':manifest['scope'],'variants':{'0':'original range guard and DIVU by36','1':'same guard, exact bounded shift/multiply quotient'},'correctness':'All1152 byte offsets and seven out-of-range offsets agree on actual RV32 CPU; both timed outputs agree on all captured frame360 triangle slots.','runs':{},'summary':{},'evidence_sha256':{}}
for mode in ['quiet','scanout']:
 text=(out/(mode+'.log')).read_text().split('=== UART BEGIN ===')[0]
 pattern=r'RUN ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) @.*?HOST_KERNEL retired=(\d+) cycles=(\d+) freeze=(\d+) hazards=(\d+)\s*.*?TIMER ([0-9a-f]{8}) ([0-9a-f]{8})'
 rs=[]
 for v in re.findall(pattern,text,re.S):
  cold,order,variant=map(lambda s:int(s,16),v[:3]);retired,cycles,freeze,hazards=map(int,v[3:7]);timer,check=map(lambda s:int(s,16),v[7:])
  rs.append(dict(cold_prepared=bool(cold),order=order,variant=variant,retired_with_marker_overhead=retired,cycles_with_marker_overhead=cycles,execute_freeze_cycles=freeze,dispatch_hazard_cycles=hazards,timer_cycles=timer,hash=f'{check:08x}'))
 assert len(rs)==8 and len({r['hash'] for r in rs})==1,len(rs)
 assert all(r['retired_with_marker_overhead']>10*len(offsets) for r in rs),'Measured batch was optimized away'
 result['runs'][mode]=rs;result['summary'][mode]={}
 for cold in [False,True]:
  subsets={i:[r for r in rs if r['cold_prepared']==cold and r['variant']==i] for i in [0,1]};assert all(len(s)==2 for s in subsets.values())
  base=statistics.mean(r['timer_cycles'] for r in subsets[0]);base_ret=statistics.mean(r['retired_with_marker_overhead'] for r in subsets[0])
  result['summary'][mode]['cold_prepared' if cold else 'warm']={str(i):dict(timer_cycles_mean=statistics.mean(r['timer_cycles'] for r in rows),cycle_saving_percent=100*(1-statistics.mean(r['timer_cycles'] for r in rows)/base),retired_mean=statistics.mean(r['retired_with_marker_overhead'] for r in rows),retired_saving_percent=100*(1-statistics.mean(r['retired_with_marker_overhead'] for r in rows)/base_ret),execute_freeze_mean=statistics.mean(r['execute_freeze_cycles'] for r in rows),dispatch_hazard_mean=statistics.mean(r['dispatch_hazard_cycles'] for r in rows)) for i,rows in subsets.items()}
 result['evidence_sha256'][str((out/(mode+'.log')).relative_to(ROOT))]=sha(out/(mode+'.log'))
result['evidence_sha256'][str((out/'manifest.json').relative_to(ROOT))]=sha(out/'manifest.json')
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['summary'],indent=2))
