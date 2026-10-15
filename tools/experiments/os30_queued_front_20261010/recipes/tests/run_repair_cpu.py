#!/usr/bin/env python3
"""Execute the actual C repair arithmetic block on the frozen current CPU."""
import hashlib,json,struct,subprocess,sys
from pathlib import Path
T=Path(__file__).resolve().parent;Q=T.parent;P=Q.parent
O=P/'triangle-front-v1/tests/cpu-arithmetic-v1';C=P/'cached-cpu-v1/native-sdram-v2'
src=Q/'work/sm64/src/sm64/sm64/src/pc/gfx/gfx_gpu.c'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
out=T/sys.argv[1];assert out.parent==T and not out.exists();out.mkdir()
s=src.read_text();start=s.index('    unsigned saved_fcsr;',s.index('void gfx_gpu_queued_front_service(void) {'));end=s.index('    BRAM_GEO_REGS[0x02e] = serial;',start);block=s[start:end]
rows=json.loads((O/'inputs.json').read_text())['rows'];gold=list(struct.iter_unpack('<11I',(O/'outputs.bin').read_bytes()))
selected=[i for i,a in enumerate(rows)if a[9]==0 and not any(a[10:13]) and a[13]in(1,2)]
selected=selected[::4]+[i for i in selected if i>=4120]
selected=sorted(set(selected))
inputs=[rows[i]for i in selected for _ in range(5)]
header='#define VECTOR_COUNT %du\nstatic const uint32_t vectors[VECTOR_COUNT][14]={\n'%len(inputs)+',\n'.join('{'+','.join('0x%08xu'%x for x in row)+'}'for row in inputs)+'\n};\n'
(out/'vectors.h').write_text(header)
kernel='''#include <stdint.h>
#include "vectors.h"
#define STATUS ((volatile uint32_t *)0x5007f000u)
#define OUTPUT ((volatile uint32_t *)0x50a00000u)
__attribute__((noinline)) static unsigned repair(const float v[9],unsigned cull,uint32_t output[9]){
'''+block+'''
 float values[9]={center_x,center_y,dx0,dy0,dx2,dy2,product0,product1,cross};
 for(unsigned j=0;j<9;j++)output[j]=((uint32_t *)values)[j];
 return keep;
}
void kernel(void){for(unsigned i=0;i<VECTOR_COUNT;i++){
 uint32_t outputs[9],csr,initial=((i%5)<<5)|(i&31);
 __asm__ volatile("csrw fcsr,%0"::"r"(initial):"memory");
 unsigned keep=repair((const float *)vectors[i],vectors[i][13],outputs);
 __asm__ volatile("csrr %0,fcsr":"=r"(csr));
 for(unsigned j=0;j<9;j++)OUTPUT[i*11+j]=outputs[j];
 OUTPUT[i*11+9]=keep;OUTPUT[i*11+10]=csr;
 if((i&127)==0)STATUS[2]=i;
 }__asm__ volatile("fence rw,rw":::"memory");STATUS[2]=VECTOR_COUNT;STATUS[0]=0x600dcafe;for(;;)__asm__ volatile("nop");}
'''
(out/'kernel.c').write_text(kernel);(out/'start.S').write_text((O/'start.S').read_text());(out/'kernel.ld').write_text((O/'kernel.ld').read_text())
cpu=Q/'model/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
assert sha(cpu)==sha(C/'frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
for p,h in json.loads((C/'prepared.json').read_text())['source_sha256'].items():assert sha(C/p)==h,p
paths=[Path(__file__),src,cpu,O/'manifest.json',O/'inputs.json',O/'outputs.bin',O/'host',C/'prepared.json']+[out/x for x in ['kernel.c','vectors.h','start.S','kernel.ld']]
before={str(p):sha(p)for p in paths};report={'status':'RUNNING','inputs_sha256':before,'extracted_block_sha256':hashlib.sha256(block.encode()).hexdigest(),'original_oracle_indices':selected,'rounding_modes_per_vector':5,'commands':[],'scope':'Actual frozen CPU executes extracted app repair arithmetic/comparison/FCSR-restore block. MMIO operand acquisition/ACK tested separately in worker harness.'}
def run(argv,name):
 with (out/(name+'.log')).open('x')as f:r=subprocess.run(list(map(str,argv)),cwd=out,stdout=f,stderr=subprocess.STDOUT)
 report['commands'].append({'argv':list(map(str,argv)),'returncode':r.returncode,'log_sha256':sha(out/(name+'.log'))});(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 assert r.returncode==0,(name,(out/(name+'.log')).read_text()[-6000:])
run(['riscv64-elf-gcc','-march=rv32imafc_zicsr_zifencei','-mabi=ilp32f','-O2','-ffast-math','-fno-strict-aliasing','-ffreestanding','-fno-builtin','-fno-stack-protector','-msmall-data-limit=0','-nostdlib','-Wl,--no-relax,-T,kernel.ld','start.S','kernel.c','-o','kernel.elf'],'build')
run(['riscv64-elf-objcopy','-O','binary','kernel.elf','kernel.bin'],'objcopy')
run([O/'host',out/'kernel.bin',out/'outputs.bin',out/'result.json',len(inputs)],'run')
assert json.loads((out/'result.json').read_text())['pass']
actual=list(struct.iter_unpack('<11I',(out/'outputs.bin').read_bytes()));assert len(actual)==len(inputs)
for j,a in enumerate(actual):
 expected=gold[selected[j//5]]
 assert a[:9]==expected[:9],(j,'DAG result',a[:9],expected[:9])
 assert a[9]==int(expected[9]==0),(j,'keep/reject',a[9],expected[9])
 assert a[10]==((j%5)<<5)|(j&31),(j,'FCSR restore',a[10])
assert before=={str(p):sha(p)for p in paths}
report.update(status='PASS_ACTUAL_CPU_QGF_REPAIR_AND_FCSR',vectors=len(selected),executions=len(actual),exact_intermediate_checks=9*len(actual),outputs_sha256=sha(out/'outputs.bin'),kernel_sha256=sha(out/'kernel.elf'),result=json.loads((out/'result.json').read_text()))
(out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],len(actual))
