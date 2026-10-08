#!/usr/bin/env python3
"""Execute the literal restore overlay to entry under qsim; native proof is separate."""
import json,subprocess
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';G=P/'native-renderer-window-v1/guest-v2';O=G/'restore-check'
Q=P/'native-renderer-capture-v1/qsim';S=P/'native-renderer-capture-v1/run-reference/snapshot'
C=r'''
#include <assert.h>
#include <elf.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu.h"
uint32_t coupled_render_pc;
void coupled_sync(void){} void coupled_render_enter(void){}
int coupled_active(void){return 0;}
uint64_t coupled_alias_refill(uint32_t a,uint64_t b){(void)a;(void)b;abort();}
static uint32_t want[77];static unsigned checked;
static void load(const char *p,void *dst,size_t n){FILE*f=fopen(p,"rb");assert(f);assert(fread(dst,1,n,f)==n);assert(fgetc(f)==EOF);fclose(f);}
static int hook(void *ctx,qsim_cpu *c,uint32_t pc){
 (void)ctx;assert(pc==want[64]);
 for(unsigned i=0;i<32;i++){assert(c->x[i]==want[i]);assert(c->f[i]==want[32+i]);}
 assert((qsim_fflags(c)|(c->frm<<5))==want[65]);
 assert((c->mstatus&0x6008)==(want[66]&0x6008));
 assert(c->mie==want[67]&&c->mip==want[68]&&c->mscratch==want[70]&&c->csr_7ff==want[74]);
 checked=1;return QSIM_TRAP_STOP;
}
int main(int argc,char**argv){assert(argc==6);
 qsim_cpu*c=qsim_cpu_new(NULL);assert(c);load(argv[1],c->sdram,c->sdram_size);load(argv[2],c->bram,c->bram_size);load(argv[3],want,sizeof want);
 FILE*f=fopen(argv[4],"rb");assert(f);Elf32_Ehdr h;assert(fread(&h,sizeof h,1,f)==1);assert(h.e_machine==EM_RISCV);
 for(unsigned i=0;i<h.e_phnum;i++){Elf32_Phdr p;fseek(f,h.e_phoff+i*h.e_phentsize,SEEK_SET);assert(fread(&p,sizeof p,1,f)==1);if(p.p_type!=PT_LOAD)continue;
  void*d=qsim_mem_ptr(c,p.p_vaddr,p.p_memsz);assert(d);memset(d,0,p.p_memsz);fseek(f,p.p_offset,SEEK_SET);assert(fread(d,1,p.p_filesz,f)==p.p_filesz);
 }fclose(f);qsim_cpu_reset(c,h.e_entry);c->env.hook=hook;assert(!qsim_add_hook(c,want[64]));
 int result=qsim_run(c,10000);assert(checked&&result==QSIM_RUN_TRAP);
 FILE*out=fopen(argv[5],"w");assert(out);fprintf(out,"{\"passed\":true,\"integer_registers\":32,\"float_registers\":32,\"instructions\":%llu,\"fcsr\":%u,\"native_execution\":false}\n",(unsigned long long)c->instret,want[65]);fclose(out);qsim_cpu_free(c);return 0;
}
'''
O.mkdir(exist_ok=False);(O/'check.c').write_text(C)
command=['cc','-O2','-I'+str(Q),str(O/'check.c'),*[str(Q/f'build/qsim/{n}.o')for n in('cpu','fpu','cache','prof','lint','qelf')],'-lm','-o',str(O/'check')]
with(O/'build.log').open('x')as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
run=[str(O/'check'),str(S/'sdram.bin'),str(S/'bram.bin'),str(S/'cpu-state.bin'),str(G/'overlay.elf'),str(O/'result.json')]
with(O/'run.log').open('x')as f:subprocess.run(run,stdout=f,stderr=subprocess.STDOUT,check=True)
write(O/'manifest.json',dict(passed=True,command=command,run=run,evidence_sha256={str(f.relative_to(ROOT)):sha(f)for f in[Path(__file__),G/'overlay.elf',G/'prepared.json',S/'cpu-state.bin',O/'check.c',O/'result.json',O/'build.log',O/'run.log']},limits=['Literal guest restore instructions preserve captured registers at gfx_run entry in qsim. Actual Vexii/cache/bus integration is a separate pending proof.','mtvec/mepc/MPP/MPIE are controlled private return/entry machinery; all integer/FP registers and FCSR restore exactly.']))
print(sha(O/'manifest.json'))
