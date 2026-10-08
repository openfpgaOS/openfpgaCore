#!/usr/bin/env python3
"""Retained-A CPU/FPU component checks for the positive-W guard fast path."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase7/combined-v1'
CAND=CAMP/'phase9/w-guard-v2'
OLD=ROOT/'docs/measurements/os30-simple-20261005/validation/cpu-kernel'
OUT=CAND/'cpu-guard-timer'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

PREFIX=r'''#include <stdint.h>
#include <stddef.h>
#include <string.h>
volatile unsigned traps,irq_count;
#define RSPV_TO_FLOAT(x) (x)
struct LoadedVertex {float x,y,z,w,w_inv,u,v;uint32_t color;uint8_t clip_rej;};
static float fp(uint32_t u) {float f;memcpy(&f,&u,4);return f;}
static void put(char c) {while(!(*(volatile unsigned *)0x4f000000u&2)){} *(volatile unsigned *)0x4f000004u=(unsigned char)c;}
static void text(const char*s) {while(*s)put(*s++);}
static void hex(unsigned v) {for(unsigned n=0;n<8;n++)put("0123456789abcdef"[(v>>(28-4*n))&15]);put(' ');}
static void fail(unsigned i,unsigned a,unsigned b) {text("KERNEL FAIL ");hex(i);hex(a);hex(b);put('\n');for(;;){}}
static unsigned cycles(void) {__asm__ volatile("":::"memory");unsigned x=*(volatile unsigned *)0x40000004u;__asm__ volatile("":::"memory");return x;}
'''
TAIL=r'''
static const uint32_t edges[]={0,1,2,0x007fffff,0x00800000,0x3e800000,0x3f000000,0x3f800000,
 0x40000000,0x46fffdfe,0x46fffdff,0x46fffe00,0x46fffe01,0x46ffffff,0x47000000,
 0x7f7fffff,0x7f800000,0x7f800001,0x7fc00000,0x7fffffff,0x80000000,0x80000001,
 0x807fffff,0x80800000,0xbf800000,0xff7fffff,0xff800000,0xff800001,0xffc00000,0xffffffff};
static struct LoadedVertex vertices[32];
static uint8_t jobs[512][3];
static unsigned rng=0x29675431u;
static unsigned next(void) {unsigned x=rng;x^=x<<13;x^=x>>17;x^=x<<5;return rng=x;}
static volatile unsigned sink;
static __attribute__((noinline,noclone)) unsigned bench_reference(void) {
 unsigned total=0;
 for(unsigned r=0;r<8;r++) {
  __asm__ volatile("":::"memory");
  for(unsigned i=0;i<512;i++)total+=reference(&vertices[jobs[i][0]],&vertices[jobs[i][1]],&vertices[jobs[i][2]]);
 }
 return total;
}
static __attribute__((noinline,noclone)) unsigned bench_candidate(void) {
 unsigned total=0;
 for(unsigned r=0;r<8;r++) {
  __asm__ volatile("":::"memory");
  for(unsigned i=0;i<512;i++)total+=candidate(&vertices[jobs[i][0]],&vertices[jobs[i][1]],&vertices[jobs[i][2]]);
 }
 return total;
}
int main(void) {
 struct LoadedVertex a={0},b={0},c={0};unsigned cases=0;
 const unsigned n=sizeof(edges)/sizeof(edges[0]);
 for(unsigned mode=0;mode<5;mode++) {
  __asm__ volatile("csrw frm,%0"::"r"(mode):"memory");
  for(unsigned i=0;i<n;i++)for(unsigned j=0;j<n;j++)for(unsigned k=0;k<n;k++) {
   a.w=fp(edges[i]);b.w=fp(edges[j]);c.w=fp(edges[k]);
   unsigned x=reference(&a,&b,&c),y=candidate(&a,&b,&c);
   if(x!=y)fail((i<<16)|(j<<8)|k,x,y);
   cases++;
  }
 }
 __asm__ volatile("csrw fcsr,zero":::"memory");
 text("CHECK PASS ");hex(cases);put('\n');
 /* Confirm the standalone reference has the retained F9 NaN decision. */
 a.w=fp(0x7fc00001);b.w=1.0f;c.w=2.0f;
 unsigned nan=reference(&a,&b,&c);text("NAN BASELINE ");hex(nan);put('\n');
 for(unsigned category=0;category<3;category++) {
  for(unsigned i=0;i<32;i++) {
   vertices[i].w=(float)(i*317+1)+0.125f;
   if(category==1 && (i&3)==0)vertices[i].w=fp(edges[(i*7)%n]);
   if(category==2)vertices[i].w=64.0f;
  }
  for(unsigned i=0;i<512;i++)for(unsigned k=0;k<3;k++)jobs[i][k]=next()&31;
  unsigned x=bench_reference(),y=bench_candidate();
  if(x!=y)fail(0x10000000u|category,x,y);sink=x;
  for(unsigned pass=0;pass<4;pass++) {
   unsigned ta,tb,t;
   if(pass&1) {t=cycles();sink=bench_candidate();tb=cycles()-t;t=cycles();sink=bench_reference();ta=cycles()-t;}
   else {t=cycles();sink=bench_reference();ta=cycles()-t;t=cycles();sink=bench_candidate();tb=cycles()-t;}
   text("TIME ");hex(category);hex(pass);hex(ta);hex(tb);put('\n');
  }
 }
 text("KERNEL PASS HAL init\n");return 0;
}
'''


def extracted(path,name):
    s=path.read_text();s=s[s.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run('):]
    a=s.index('            const float w1 = ');b=s.index('            ++live;',a)
    body=s[a:b].replace('goto fallback_command;','return 0;')
    return f'static __attribute__((noinline,noclone)) unsigned {name}(const struct LoadedVertex *v1,const struct LoadedVertex *v2,const struct LoadedVertex *v3) {{\n'+body+'return 1;\n}\n'


def generate():
    OUT.mkdir(exist_ok=False);fw=OUT/'firmware';fw.mkdir()
    for n in ('start.S','link.ld','firmware.mif'):shutil.copy2(OLD/'firmware'/n,fw/n)
    (fw/'kernel.c').write_text(PREFIX+extracted(BASE/'gfx_pc.c','reference')+extracted(CAND/'gfx_pc.c','candidate')+TAIL)
    old=read(OLD/'manifest.json');exe=OLD/'Vtb_system'
    assert sha(exe)==old['simulator_sha256']
    for p,h in old['unchanged_rtl_object_sha256'].items():assert sha(ROOT/p)==h
    cmd=[str(fw) if t==str(OLD/'firmware') else t for t in old['compiler_command']]
    cmd=[('-mtune=sifive-5-series' if t=='-mtune=sifive-7-series' else t) for t in cmd]
    image=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}','openfpgaos-firmware'],text=True).strip()
    cmd=[image if t=='openfpgaos-firmware' else t for t in cmd]
    with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    container=cmd[:cmd.index('riscv64-unknown-elf-gcc')]
    with (OUT/'compiler.txt').open('x') as f:subprocess.run([*container,'riscv64-unknown-elf-gcc','--version'],stdout=f,check=True)
    subprocess.run([*container,'riscv64-unknown-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],check=True)
    with (OUT/'kernel.asm').open('x') as f:subprocess.run([*container,'riscv64-unknown-elf-objdump','-d','kernel.elf'],stdout=f,check=True)
    manifest=dict(cpu_sha256=old['cpu_sha256'],simulator_path=str(exe),simulator_sha256=sha(exe),
        unchanged_rtl_object_sha256=old['unchanged_rtl_object_sha256'],compiler_command=cmd,pinned_image=image,
        source_sha256={str(p.relative_to(ROOT)):sha(p) for p in [BASE/'gfx_pc.c',CAND/'gfx_pc.c',Path(__file__)]},
        firmware_sha256={p.name:sha(p) for p in fw.iterdir() if p.is_file()},assembly_sha256=sha(OUT/'kernel.asm'),
        scope='Extracted W guard on retained A CPU/FPU/SDRAM RTL. Boundary triples inclNaNs/zeros/Inf over5roundingmodes; output decisions only. Component cycles on warm32vertex/512job workload, not whole-game FPS. Full production codegen equivalence checked separately.')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(OUT/'manifest.json')


def run():
    m=read(OUT/'manifest.json');exe=Path(m['simulator_path']);assert sha(exe)==m['simulator_sha256']
    for n,h in m['firmware_sha256'].items():assert sha(OUT/'firmware'/n)==h
    for n,h in m['unchanged_rtl_object_sha256'].items():assert sha(ROOT/n)==h
    log=OUT/'quiet.log'
    with log.open('x') as f:
        subprocess.run([str(exe),'kernel.bin','25000000'],cwd=OUT/'firmware',env=dict(os.environ,SCAN_ENABLE='0'),stdout=f,stderr=subprocess.STDOUT,check=True,timeout=600)
    text=log.read_text()
    assert 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text,text[-2200:]
    assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
    count=int(re.search(r'CHECK PASS ([0-9a-f]+)',text).group(1),16);assert count==135000
    nan=int(re.search(r'NAN BASELINE ([0-9a-f]+)',text).group(1),16)
    timings=[dict(category=int(c,16),pass_index=int(p,16),reference_cycles=int(a,16),candidate_cycles=int(b,16),
                  saving_percent=100*(1-int(b,16)/int(a,16))) for c,p,a,b in re.findall(r'TIME ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)',text)]
    assert len(timings)==12
    result=dict(passed=True,comparisons=count,rounding_modes=5,standalone_baseline_nan_accepts=bool(nan),timings=timings,
        scope=m['scope'],evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in [OUT/'manifest.json',log,OUT/'compiler.txt',OUT/'kernel.asm']})
    (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':{'generate':generate,'run':run}[sys.argv[1]]()
