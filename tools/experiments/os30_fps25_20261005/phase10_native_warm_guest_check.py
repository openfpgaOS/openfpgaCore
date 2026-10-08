#!/usr/bin/env python3
"""Literal qsim restore and ECALL ABI checks of the linked continuous guest."""
import json
import subprocess
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P=CAMP/'phase10';G=P/'native-renderer-window-v1/guest-warm-v3';O=G/'literal-check'
Q=P/'native-renderer-capture-v1/qsim';S=P/'native-warm-capture-v4/run-reference/snapshot'
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
static uint32_t wantx[32],wantf[32],wantcsr;static unsigned checked;
static void load(const char*p,void*d,size_t n){FILE*f=fopen(p,"rb");assert(f);assert(fread(d,1,n,f)==n);assert(fgetc(f)==EOF);fclose(f);}
static void overlay(qsim_cpu*c,const char*path){
 FILE*f=fopen(path,"rb");assert(f);Elf32_Ehdr h;assert(fread(&h,sizeof h,1,f)==1);
 for(unsigned i=0;i<h.e_phnum;i++){Elf32_Phdr p;fseek(f,h.e_phoff+i*h.e_phentsize,SEEK_SET);assert(fread(&p,sizeof p,1,f)==1);if(p.p_type!=PT_LOAD)continue;
  void*d=qsim_mem_ptr(c,p.p_vaddr,p.p_memsz);assert(d);memset(d,0,p.p_memsz);fseek(f,p.p_offset,SEEK_SET);assert(fread(d,1,p.p_filesz,f)==p.p_filesz);qsim_invalidate(c,p.p_vaddr,p.p_memsz);
 }fclose(f);
}
static uint32_t rng=0x92346571;static uint32_t random32(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static int hook(void*x,qsim_cpu*c,uint32_t pc){
 (void)x;assert(pc==0x1033f004);
 for(unsigned i=0;i<32;i++){
  uint32_t w=i==10?0:i==11?17000000:wantx[i];
  if(c->x[i]!=w){fprintf(stderr,"reg%u got%x want%x\n",i,c->x[i],w);abort();}
  assert(c->f[i]==wantf[i]);
 }
 assert((qsim_fflags(c)|(c->frm<<5))==wantcsr);assert(c->mie==0&&c->mscratch==0);
 checked++;return QSIM_TRAP_STOP;
}
int main(int argc,char**argv){assert(argc==6);qsim_cpu*c=qsim_cpu_new(NULL);assert(c);
 load(argv[1],c->sdram,c->sdram_size);load(argv[2],c->bram,c->bram_size);
 uint64_t total=0;uint32_t trap=(uint32_t)strtoul(argv[4],0,0);
 c->env.hook=hook;assert(!qsim_add_hook(c,0x1033f004));
 for(unsigned t=0;t<128;t++){
  overlay(c,argv[3]);qsim_cpu_reset(c,0x1033f000);
  uint32_t insn=0x00000073;memcpy(qsim_mem_ptr(c,0x1033f000,4),&insn,4);qsim_invalidate(c,0x1033f000,8);
  for(unsigned i=0;i<32;i++){wantx[i]=i?random32():0;wantf[i]=random32();}
  wantx[2]=0x13a00000;wantx[3]=0x10fe1658;wantx[16]=2;wantx[17]=0xc0de0006;
  memcpy(c->x,wantx,sizeof wantx);memcpy(c->f,wantf,sizeof wantf);
  c->mtvec=trap;c->mstatus=0x6000;c->mie=0;c->mscratch=0;c->frm=t%5;qsim_set_fflags(c,t&31);wantcsr=(c->frm<<5)|(t&31);
  uint64_t before=c->instret;int rc=qsim_run(c,10000);assert(rc==QSIM_RUN_TRAP&&checked==t+1);total+=c->instret-before;
 }
 FILE*f=fopen(argv[5],"w");assert(f);fprintf(f,"{\"passed\":true,\"ecall_cases\":%u,\"integer_checks\":%u,\"float_checks\":%u,\"total_instructions\":%llu,\"native_execution\":false}\n",checked,checked*32,checked*32,(unsigned long long)total);fclose(f);qsim_cpu_free(c);return 0;
}
'''

def main():
    O.mkdir(exist_ok=False)
    restore=(P/'native-renderer-window-v1/guest-v2/restore-check/check.c').read_text()
    (O/'restore.c').write_text(restore);(O/'ecall.c').write_text(C)
    common=['cc','-O2','-I'+str(Q)]
    objects=[str(Q/f'build/qsim/{n}.o')for n in('cpu','fpu','cache','prof','lint','qelf')]
    commands=[]
    for name in ['restore','ecall']:
        cmd=common+[str(O/f'{name}.c')]+objects+['-lm','-o',str(O/name)];commands.append(cmd)
        with(O/f'{name}-build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
    sym=json.loads((G/'overlay.json').read_text())['symbols']
    runs=[
        [str(O/'restore'),str(S/'sdram.bin'),str(S/'bram.bin'),str(S/'cpu-state.bin'),str(G/'overlay.elf'),str(O/'restore-result.json')],
        [str(O/'ecall'),str(S/'sdram.bin'),str(S/'bram.bin'),str(G/'overlay.elf'),str(sym['native_trap']),str(O/'ecall-result.json')],
    ]
    for name,run in zip(['restore','ecall'],runs):
        with(O/f'{name}-run.log').open('x')as f:subprocess.run(run,stdout=f,stderr=subprocess.STDOUT,check=True)
    write(O/'manifest.json',dict(passed=True,commands=commands,runs=runs,
        files={f.name:sha(f)for f in O.iterdir()if f.is_file()},
        evidence_sha256={str(f.relative_to(ROOT)):sha(f)for f in[Path(__file__),G/'overlay.elf',G/'prepared.json',S/'cpu-state.bin']},
        limits=['Literal linked guest instruction proof under qsim, not actual Vexii/cache execution.','ECALL test covers TIMER save/restore with arbitrary integer/FP payloads; service semantics have a separate component proof.']))
    print(sha(O/'manifest.json'))

if __name__=='__main__':main()
