#!/usr/bin/env python3
"""Check illegal compressed/full instruction mtval and mepc across fetch boundaries."""
import argparse,hashlib,json,os,re,shutil,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
OUT=ROOT/'docs/measurements/os30-dual-20261004/validation';FW=OUT/'decode-traps'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log,**kwargs):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),stdout=f,stderr=subprocess.STDOUT,check=True,**kwargs)
def prepare():
    FW.mkdir(parents=True,exist_ok=False)
    for n in ['link.ld','firmware.mif']:shutil.copy2(OUT/'firmware'/n,FW/n)
    start=(ROOT/'src/fpga/test/cpu_stress/start.S').read_text().split('\n.balign 4\ntrap:')[0]
    start+='''
.balign 4
trap:
 addi sp,sp,-16
 sw t0,0(sp)
 sw t1,4(sp)
 csrr t0,mcause
 la t1,last_cause
 sw t0,0(t1)
 csrr t0,mtval
 la t1,last_tval
 sw t0,0(t1)
 csrr t0,mepc
 la t1,last_pc
 sw t0,0(t1)
 la t1,expected_step
 lw t1,0(t1)
 add t0,t0,t1
 csrw mepc,t0
 la t1,traps
 lw t0,0(t1)
 addi t0,t0,1
 sw t0,0(t1)
 lw t0,0(sp)
 lw t1,4(sp)
 addi sp,sp,16
 mret
'''
    (FW/'start.S').write_text(start)
    cases=[(offset,code,size) for offset in [0,2,6,62] for code,size in [(0x8002,2),(0,2),(0x00c5850b,4)]]
    asm=['.section .text.probes,"ax"','.option push','.option norvc']
    for i,(offset,code,size) in enumerate(cases):
        asm += [f'.global probe_{i}',f'probe_{i}:',f'la t0,.Lbad_{i}','la t1,expected_pc','sw t0,0(t1)',
                f'jal zero,.Lbad_{i}','.balign 64',f'.zero {offset}',f'.global fault_{i}',f'fault_{i}:',f'.Lbad_{i}:',
                f'.{size}byte 0x{code:x}','.option push','.option rvc','c.jr ra','.option pop']
    asm+=['.option pop'];(FW/'probes.S').write_text('\n'.join(asm)+'\n')
    c='\n'.join(f'extern void probe_{i}(void);' for i in range(len(cases)))+'\n'
    c+='static void (*const probes[])(void)={'+','.join(f'probe_{i}' for i in range(len(cases)))+'};\n'
    c+='static const unsigned codes[]={'+','.join(hex(code) for off,code,size in cases)+'};\n'
    c+='static const unsigned sizes[]={'+','.join(str(size) for off,code,size in cases)+'};\n'
    c+=r'''
volatile unsigned traps,last_cause,last_tval,last_pc,expected_pc,expected_step;
static void put(const char *s){while(*s){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)*s++;}}
static void hex(unsigned v){char s[10];for(unsigned i=0;i<8;i++)s[i]="0123456789abcdef"[(v>>(28-4*i))&15];s[8]=' ';s[9]=0;put(s);}
int main(void){
 for(unsigned pass=0;pass<4;pass++)for(unsigned i=0;i<sizeof(probes)/sizeof(probes[0]);i++){
   unsigned before=traps;last_cause=last_tval=last_pc=0xffffffff;expected_step=sizes[i];
   probes[i]();
   if(traps!=before+1||last_cause!=2||last_tval!=codes[i]||last_pc!=expected_pc){
     put("DECODE TRAP FAIL ");hex(i);hex(last_cause);hex(last_tval);hex(last_pc);hex(expected_pc);for(;;){}
   }
 }
 put("DECODE TRAP PASS ");hex(traps);put("HAL init\n");return 0;
}
'''
    (FW/'main.c').write_text(c)
    container=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}', '-v',f'{ROOT}:{ROOT}','-w',str(FW),'openfpgaos-firmware']
    cmd=[*container,'riscv64-unknown-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O2','-ffreestanding','-fno-builtin','-nostdlib','-nostartfiles','-Wl,-T,link.ld','-Wl,--no-relax','start.S','main.c','probes.S','-o','traps.elf']
    run(cmd,FW/'build.log')
    run([*container,'riscv64-unknown-elf-objcopy','-O','binary','traps.elf','traps.bin'],FW/'objcopy.log')
    run([*container,'riscv64-unknown-elf-nm','-n','traps.elf'],FW/'symbols.txt')
    symbols=(FW/'symbols.txt').read_text()
    for i,(off,code,size) in enumerate(cases):
        address=int(re.search(r'^([0-9a-f]+) T fault_'+str(i)+'$',symbols,re.M)[1],16)
        assert address%64==off,(i,address)
    (FW/'manifest.json').write_text(json.dumps(dict(scope=__doc__,cases=cases,repeats=4,command=cmd,
        sources={str(p):sha(p) for p in FW.iterdir() if p.suffix in ['.c','.S','.ld','.mif']},binary_sha256=sha(FW/'traps.bin')),indent=2)+'\n')
def execute(label):
    out=OUT/label;sim=out/'obj/Vtb_system';assert sha(sim)==json.loads((out/'build-manifest.json').read_text())['simulator_sha256']
    results=[]
    for mode in ['quiet','scanout']:
        env=dict(os.environ,SCAN_ENABLE='1' if mode=='scanout' else '0',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
        log=out/('decode-traps-'+mode+'.log');run([sim,'traps.bin','2000000'],log,cwd=FW,env=env,timeout=60)
        text=log.read_text();uart=text.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
        assert 'DECODE TRAP PASS 00000030 HAL init' in uart and 'FAIL' not in uart
        assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
        results.append(dict(mode=mode,traps=48,log_sha256=sha(log)));print(label,mode,'PASS48 traps',flush=True)
    (out/'decode-traps-results.json').write_text(json.dumps(dict(scope=__doc__,binary_sha256=sha(FW/'traps.bin'),simulator_sha256=sha(sim),results=results),indent=2)+'\n')
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['prepare','run']);ap.add_argument('--label');a=ap.parse_args()
    if a.action=='prepare':prepare()
    else:execute(a.label)
if __name__=='__main__':main()
