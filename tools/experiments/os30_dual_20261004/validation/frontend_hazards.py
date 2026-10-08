#!/usr/bin/env python3
"""Exercise real CPU frontend redirects/RVC boundaries under IRQ and fetch stalls."""
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
FIRMWARE=OUT/'frontend-hazards'
OFFSETS=[0,2,4,6,30,62]

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log,**kwargs):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),stdout=f,stderr=subprocess.STDOUT,check=True,**kwargs)

def prepare():
    FIRMWARE.mkdir(parents=True,exist_ok=False)
    for name in ['start.S','link.ld','firmware.mif']:
        shutil.copy2(OUT/'firmware'/name,FIRMWARE/name)
    h=0x12345678;index=0
    for _ in range(2048):
        h=(h+index+2)&0xffffffff
        h=(h+(2 if h&1 else 4))&0xffffffff
        h^=0x3f800000;h^=h>>7
        index=(index+37)&127
    asm=['.section .text.frontend,"ax"','.option push','.option norvc']
    for i in range(128):
        offset=OFFSETS[i%len(OFFSETS)]
        asm+=['.balign 256',f'.zero {offset}',f'.global flow_{i}',f'flow_{i}:',
            f'addi a0,a0,{i+1}',
            '.option push','.option rvc','c.addi a0,1','.option pop',
            'andi t0,a0,1',f'beq t0,zero,.Leven_{i}',
            'addi a0,a0,3',f'jal zero,.Ljoin_{i}',
            'sw zero,0(a1)','ebreak',f'.Leven_{i}:','addi a0,a0,5',
            f'.Ljoin_{i}:','.option push','.option rvc','c.addi a0,-1','.option pop',
            'lui t0,0x3f800','fmv.w.x ft1,t0','fdiv.s ft0,ft1,ft1',
            'fmv.x.w t0,ft0','xor a0,a0,t0','srli t0,a0,7','xor a0,a0,t0',
            f'beq a0,a0,.Lsafe_{i}','sw zero,0(a1)','ebreak',
            f'.Lsafe_{i}:','.option push','.option rvc','c.jr ra','.option pop']
    asm+=['.option pop']
    (FIRMWARE/'flow.S').write_text('\n'.join(asm)+'\n')
    c='\n'.join(f'extern unsigned flow_{i}(unsigned,volatile unsigned *);' for i in range(128))+'\n'
    c+='static unsigned (*const blocks[])(unsigned,volatile unsigned*)={'+','.join(f'flow_{i}' for i in range(128))+'};\n'
    c+=r'''
volatile unsigned traps,irq_count,sentinel=0xcafebabe;
static void put(const char *s) {while(*s){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)*s++;}}
static void hex(unsigned v) {char s[10];for(unsigned i=0;i<8;i++)s[i]="0123456789abcdef"[(v>>(28-4*i))&15];s[8]=' ';s[9]=0;put(s);}
static unsigned now(void){return *(volatile unsigned*)0x40000004u;}
static void fail(unsigned h) {put("FRONTEND FAIL ");hex(h);hex(sentinel);hex(irq_count);for(;;){}}
int main(void) {
 *(volatile unsigned*)0x400000b4u=2003;
 *(volatile unsigned*)0x400000b8u=3;
 __asm__ volatile("li t0,128; csrs mie,t0; csrsi mstatus,8":::"t0","memory");
 unsigned h=0x12345678,index=0,start=now();
 for(unsigned j=0;j<2048;j++) {
   h=blocks[index](h,&sentinel);index=(index+37)&127;
   if(sentinel!=0xcafebabe)fail(h);
 }
 unsigned cycles=now()-start;
 __asm__ volatile("csrci mstatus,8":::"memory");
 *(volatile unsigned*)0x400000b8u=2;
'''
    c+=f' if(h!=0x{h:08x}u || irq_count<50 || traps)fail(h);\n'
    c+='put("FRONTEND RESULT ");hex(h);hex(cycles);hex(irq_count);put("\\nFRONTEND PASS HAL init\\n");return 0;\n}\n'
    (FIRMWARE/'main.c').write_text(c)
    container=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}',
        '-v',f'{ROOT}:{ROOT}','-w',str(FIRMWARE),'openfpgaos-firmware']
    cmd=[*container,'riscv64-unknown-elf-gcc','-march=rv32imafc_zicsr_zifencei',
        '-mabi=ilp32f','-O2','-ffreestanding','-fno-builtin','-nostdlib','-nostartfiles',
        '-Wl,-T,link.ld','-Wl,--no-relax','start.S','main.c','flow.S','-o','frontend.elf']
    run(cmd,FIRMWARE/'build.log')
    run([*container,'riscv64-unknown-elf-objcopy','-O','binary','frontend.elf','frontend.bin'],FIRMWARE/'objcopy.log')
    run([*container,'riscv64-unknown-elf-nm','-n','frontend.elf'],FIRMWARE/'symbols.txt')
    symbols=(FIRMWARE/'symbols.txt').read_text()
    for i in range(128):
        address=int(re.search(r'^([0-9a-f]+) T flow_'+str(i)+'$',symbols,re.M)[1],16)
        assert address%256==OFFSETS[i%len(OFFSETS)],(i,address)
    (FIRMWARE/'manifest.json').write_text(json.dumps(dict(scope=__doc__,
        calls=2048,blocks=128,entry_offsets_in_256byte_block=OFFSETS,expected_hash=f'{h:08x}',
        command=cmd,sources={str(p):sha(p) for p in FIRMWARE.iterdir() if p.suffix in ['.c','.S','.ld','.mif']},
        binary_sha256=sha(FIRMWARE/'frontend.bin')),indent=2)+'\n')

def execute(label):
    out=OUT/label;sim=out/'obj/Vtb_system'
    manifest=json.loads((out/'build-manifest.json').read_text())
    assert sha(sim)==manifest['simulator_sha256']
    expected=json.loads((FIRMWARE/'manifest.json').read_text())['expected_hash']
    results=[]
    for mode in ['quiet','scanout']:
        env=dict(os.environ,SCAN_ENABLE='1' if mode=='scanout' else '0',
            SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
        log=out/('frontend-'+mode+'.log')
        run([sim,'frontend.bin','4000000'],log,cwd=FIRMWARE,env=env,timeout=120)
        text=log.read_text();uart=text.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
        assert 'FRONTEND PASS HAL init' in uart and 'FAIL' not in uart
        assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
        match=re.search(r'FRONTEND RESULT ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})',uart)
        assert match and match[1]==expected
        result=dict(mode=mode,hash=match[1],cycles=int(match[2],16),interrupts=int(match[3],16),log_sha256=sha(log))
        assert result['interrupts']>=50
        results.append(result);print(label,result,flush=True)
    (out/'frontend-results.json').write_text(json.dumps(dict(scope=__doc__,
        binary_sha256=sha(FIRMWARE/'frontend.bin'),simulator_sha256=sha(sim),results=results),indent=2)+'\n')

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['prepare','run']);ap.add_argument('--label')
    a=ap.parse_args()
    if a.action=='prepare':prepare()
    else:execute(a.label)
if __name__=='__main__':main()
