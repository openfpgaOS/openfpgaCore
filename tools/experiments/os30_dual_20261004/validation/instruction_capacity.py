#!/usr/bin/env python3
"""Independent executable working-set fixture; not a whole-game workload."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
OUT=ROOT/'docs/measurements/os30-dual-20261004/validation'
FIRMWARE=OUT/'instruction-capacity'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def run(cmd,log,**kwargs):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),stdout=f,stderr=subprocess.STDOUT,check=True,**kwargs)

def transform(h,i):
    for r in range(3):
        h=(h+(i*37+r*71)%2048)&0xffffffff
        h=(h<<3)&0xffffffff
        h^=h>>29
        h^=(i*17+r*93)%2048
        h=(h+(h>>11)+r+1)&0xffffffff
    return h

def prepare():
    FIRMWARE.mkdir(parents=True,exist_ok=False)
    for name in ['start.S','link.ld','firmware.mif']:
        shutil.copy2(OUT/'firmware'/name,FIRMWARE/name)
    expected={}
    for n in [64,192]:
        h=0x12345678;index=0
        for _ in range(n*16):
            h=transform(h,index);index+=37
            if index>=n:index-=n
        expected[n]=h
    asm=['.section .text.capacity,"ax"','.option push','.option norvc','.balign 128',
         '.global capacity_start','capacity_start:']
    for i in range(192):
        asm+=['.balign 128',f'.global instruction_block_{i}',f'instruction_block_{i}:']
        for r in range(3):
            asm+=[f'  addi a0,a0,{(i*37+r*71)%2048}','  slli a0,a0,3',
                  '  srli t0,a0,29','  xor a0,a0,t0',f'  xori a0,a0,{(i*17+r*93)%2048}',
                  '  srli t0,a0,11','  add a0,a0,t0',f'  addi a0,a0,{r+1}']
        asm+=['  ret']
    asm+=['.balign 128','.global capacity_end','capacity_end:','.option pop']
    (FIRMWARE/'blocks.S').write_text('\n'.join(asm)+'\n')
    c='\n'.join(f'extern unsigned instruction_block_{i}(unsigned);' for i in range(192))+'\n'
    c+='static unsigned (*const blocks[])(unsigned)={'+','.join(f'instruction_block_{i}' for i in range(192))+'};\n'
    c+=r'''
volatile unsigned traps,irq_count;
static void put(const char *s) {while(*s){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)*s++;}}
static void hex(unsigned v) {char s[10];for(unsigned i=0;i<8;i++)s[i]="0123456789abcdef"[(v>>(28-4*i))&15];s[8]=' ';s[9]=0;put(s);}
static unsigned now(void){return *(volatile unsigned*)0x40000004u;}
__attribute__((noinline)) static unsigned kernel(unsigned n) {
 unsigned h=0x12345678,index=0;
 for(unsigned j=0;j<n*16;j++){h=blocks[index](h);index+=37;if(index>=n)index-=n;}
 return h;
}
static void check(unsigned n,unsigned expected) {
 unsigned start=now(),h=kernel(n),cycles=now()-start;
 if(h!=expected){put("ICACHE FAIL ");hex(n);hex(h);hex(expected);for(;;){}}
 put("ICACHE ");hex(n*128);hex(cycles);hex(h);put("\n");
}
int main(void) {
 for(unsigned pass=0;pass<2;pass++) {
'''
    c+=f'check(64,0x{expected[64]:08x}u);check(192,0x{expected[192]:08x}u);\n'
    c+='}\nput("ICACHE PASS HAL init\\n");return 0;\n}\n'
    (FIRMWARE/'main.c').write_text(c)
    container=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
        '-v',f'{ROOT}:{ROOT}','-w',str(FIRMWARE),'openfpgaos-firmware']
    cmd=[*container,'riscv64-unknown-elf-gcc','-march=rv32imafc_zicsr_zifencei',
        '-mabi=ilp32f','-O2','-ffreestanding','-fno-builtin','-nostdlib','-nostartfiles',
        '-Wl,-T,link.ld','-Wl,--no-relax','start.S','main.c','blocks.S','-o','icache.elf']
    run(cmd,FIRMWARE/'build.log')
    run([*container,'riscv64-unknown-elf-objcopy','-O','binary','icache.elf','icache.bin'],FIRMWARE/'objcopy.log')
    run([*container,'riscv64-unknown-elf-nm','-n','icache.elf'],FIRMWARE/'symbols.txt')
    symbols=(FIRMWARE/'symbols.txt').read_text()
    bounds=[int(re.search(r'^([0-9a-f]+) T '+n+'$',symbols,re.M)[1],16) for n in ['capacity_start','capacity_end']]
    assert bounds[1]-bounds[0]==24576,bounds
    (FIRMWARE/'manifest.json').write_text(json.dumps(dict(scope=__doc__,
        function_blocks=192,block_spacing=128,executed_bytes_per_block=100,
        total_executable_region_bytes=24576,expected=expected,command=cmd,
        sources={str(p):sha(p) for p in FIRMWARE.iterdir() if p.suffix in ['.c','.S','.ld','.mif']},
        binary_sha256=sha(FIRMWARE/'icache.bin')),indent=2)+'\n')

def execute(label):
    out=OUT/label;sim=out/'obj/Vtb_system'
    manifest=json.loads((out/'build-manifest.json').read_text())
    assert sha(sim)==manifest['simulator_sha256']
    results=[]
    for mode in ['quiet','scanout']:
        env=dict(os.environ,SCAN_ENABLE='1' if mode=='scanout' else '0',
            SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
        log=out/('icache-'+mode+'.log')
        run([sim,'icache.bin','4000000'],log,cwd=FIRMWARE,env=env,timeout=120)
        text=log.read_text();uart=text.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
        assert 'ICACHE PASS HAL init' in uart and 'FAIL' not in uart
        assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
        rows=[dict(region_bytes=int(a,16),cycles=int(b,16),hash=c) for a,b,c in
              re.findall(r'ICACHE ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})',uart)]
        assert len(rows)==4
        results.append(dict(mode=mode,rows=rows,log_sha256=sha(log)))
        print(label,mode,rows,flush=True)
    (out/'icache-results.json').write_text(json.dumps(dict(scope=__doc__,
        binary_sha256=sha(FIRMWARE/'icache.bin'),simulator_sha256=sha(sim),results=results),indent=2)+'\n')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['prepare','run']);ap.add_argument('--label')
    a=ap.parse_args()
    if a.action=='prepare':prepare()
    else:execute(a.label)
if __name__=='__main__':main()
