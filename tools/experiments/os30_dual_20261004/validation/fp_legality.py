#!/usr/bin/env python3
"""FP decode legality: FS enable, reserved/dynamic RM, compressed memory and FP CSRs."""
import argparse,hashlib,json,os,re,shutil,subprocess
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
OUT=ROOT/'docs/measurements/os30-dual-20261004/validation';FW=OUT/'fp-legality'
# assembly, instruction bytes, needs rounding mode, encoded rm, legal F3/buffer bits
OPS=[('.4byte 0x002081d3',4,1,0,0x40000000,0x3f800000),
     ('.4byte 0x0020d1d3',4,1,5,0,0x3f800000),
     ('.4byte 0x0020e1d3',4,1,6,0,0x3f800000),
     ('.4byte 0x0020f1d3',4,1,7,0x40000000,0x3f800000),
     ('fsgnj.s ft3,ft1,ft2',4,0,0,0x3f800000,0x3f800000),
     ('flw ft3,0(a0)',4,0,0,0x3f800000,0x3f800000),
     ('fsw ft3,0(a0)',4,0,0,0x40400000,0x40400000),
     ('.option push\n.option rvc\nc.flw fa0,0(a0)\n.option pop',2,0,0,0x40400000,0x3f800000),
     ('.option push\n.option rvc\nc.fsw fa0,0(a0)\n.option pop',2,0,0,0x40400000,0x3f800000),
     ('csrrs zero,fflags,zero',4,0,0,0x40400000,0x3f800000),
     ('csrwi fflags,0',4,0,0,0x40400000,0x3f800000)]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def run(cmd,log,**kwargs):
    with log.open('w') as f:subprocess.run(list(map(str,cmd)),stdout=f,stderr=subprocess.STDOUT,check=True,**kwargs)
def prepare():
    FW.mkdir(parents=True,exist_ok=False)
    for n in ['start.S','link.ld','firmware.mif']:shutil.copy2(OUT/'decode-traps'/n,FW/n)
    asm=['.section .text.probes,"ax"','.option push','.option norvc']
    for i,(code,size,need,rm,f3,buf) in enumerate(OPS):
        asm += [f'.global probe_{i}',f'probe_{i}:',f'la t0,fault_{i}','la t1,expected_pc','sw t0,0(t1)',
                f'.global fault_{i}',f'fault_{i}:',code,'ret']
    asm+=['.option pop'];(FW/'probes.S').write_text('\n'.join(asm)+'\n')
    c='\n'.join(f'extern void probe_{i}(unsigned*);extern const unsigned char fault_{i}[];' for i in range(len(OPS)))+'\n'
    c+='static void (*const probes[])(unsigned*)={'+','.join(f'probe_{i}' for i in range(len(OPS)))+'};\n'
    c+='static const unsigned char *const faults[]={'+','.join(f'fault_{i}' for i in range(len(OPS)))+'};\n'
    for name,index in [('sizes',1),('needs_rm',2),('rms',3),('f3_bits',4),('buf_bits',5)]:
        c+=f'static const unsigned {name}[]={{'+','.join(hex(op[index]) for op in OPS)+'};\n'
    c+=r'''
volatile unsigned traps,last_cause,last_tval,last_pc,expected_pc,expected_step;
static void put(const char *s){while(*s){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)*s++;}}
static void hex(unsigned v){char s[10];for(unsigned i=0;i<8;i++)s[i]="0123456789abcdef"[(v>>(28-4*i))&15];s[8]=' ';s[9]=0;put(s);}
static void fail(unsigned i,unsigned fs,unsigned frm,unsigned f3,unsigned buf) {
 put("FP LEGALITY FAIL ");hex(i);hex(fs);hex(frm);hex(traps);hex(last_cause);hex(last_tval);hex(last_pc);hex(expected_pc);hex(f3);hex(buf);for(;;){}
}
int main(void){
 unsigned cases=0;
 for(unsigned pass=0;pass<4;pass++)for(unsigned fs=0;fs<2;fs++)for(unsigned mode=0;mode<3;mode++)
 for(unsigned i=0;i<sizeof(probes)/sizeof(probes[0]);i++) {
   unsigned frm=mode?mode+4:0,buffer=0x3f800000;
   __asm__ volatile("li t0,0x6000;csrs mstatus,t0;csrw frm,%0;li t0,0x3f800000;fmv.w.x ft1,t0;fmv.w.x ft2,t0;fmv.w.x fa0,t0;li t0,0x40400000;fmv.w.x ft3,t0"
       ::"r"(frm):"t0","ft1","ft2","ft3","fa0","memory");
   if(!fs)__asm__ volatile("li t0,0x6000;csrc mstatus,t0":::"t0","memory");
   unsigned before=traps;last_cause=last_tval=last_pc=0xffffffff;expected_step=sizes[i];
   unsigned illegal=!fs || (needs_rm[i] && (rms[i]==5 || rms[i]==6 || (rms[i]==7 && frm>=5)));
   unsigned raw=0;for(unsigned j=0;j<sizes[i];j++)raw|=(unsigned)faults[i][j]<<(8*j);
   probes[i](&buffer);
   unsigned f3;
   __asm__ volatile("li t0,0x6000;csrs mstatus,t0;csrw frm,zero;fmv.x.w %0,ft3":"=r"(f3)::"t0","memory");
   if(traps!=before+illegal || (illegal && (last_cause!=2 || last_tval!=raw || last_pc!=expected_pc)) ||
      (!illegal && last_cause!=0xffffffff) || f3!=(illegal?0x40400000:f3_bits[i]) ||
      buffer!=(illegal?0x3f800000:buf_bits[i]))fail(i,fs,frm,f3,buffer);
   cases++;
 }
 put("FP LEGALITY PASS ");hex(cases);hex(traps);put("HAL init\n");return 0;
}
'''
    (FW/'main.c').write_text(c)
    container=['docker','run','--rm','--user',f'{os.getuid()}:{os.getgid()}', '-v',f'{ROOT}:{ROOT}','-w',str(FW),'openfpgaos-firmware']
    cmd=[*container,'riscv64-unknown-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O2','-ffreestanding','-fno-builtin','-nostdlib','-nostartfiles','-Wl,-T,link.ld','-Wl,--no-relax','start.S','main.c','probes.S','-o','fp.elf']
    run(cmd,FW/'build.log');run([*container,'riscv64-unknown-elf-objcopy','-O','binary','fp.elf','fp.bin'],FW/'objcopy.log')
    (FW/'manifest.json').write_text(json.dumps(dict(scope=__doc__,operations=OPS,repeats=4,cases=264,expected_traps=164,command=cmd,
        sources={str(p):sha(p) for p in FW.iterdir() if p.suffix in ['.c','.S','.ld','.mif']},binary_sha256=sha(FW/'fp.bin')),indent=2)+'\n')
def execute(label):
    out=OUT/label;sim=out/'obj/Vtb_system';assert sha(sim)==json.loads((out/'build-manifest.json').read_text())['simulator_sha256']
    results=[]
    for mode in ['quiet','scanout']:
        env=dict(os.environ,SCAN_ENABLE='1' if mode=='scanout' else '0',SCAN_PERIOD='700',SCAN_LEN='128',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000')
        log=out/('fp-legality-'+mode+'.log');run([sim,'fp.bin','2000000'],log,cwd=FW,env=env,timeout=60)
        text=log.read_text();uart=text.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
        assert 'FP LEGALITY PASS 00000108 000000a4 HAL init' in uart and 'FAIL' not in uart
        assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
        results.append(dict(mode=mode,cases=264,traps=164,log_sha256=sha(log)));print(label,mode,'PASS264 cases,164 traps',flush=True)
    (out/'fp-legality-results.json').write_text(json.dumps(dict(scope=__doc__,binary_sha256=sha(FW/'fp.bin'),simulator_sha256=sha(sim),results=results),indent=2)+'\n')
def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['prepare','run']);ap.add_argument('--label');a=ap.parse_args()
    if a.action=='prepare':prepare()
    else:execute(a.label)
if __name__=='__main__':main()
