/* Private qsim timing calibration against the exact retained-CPU alias ELF. */
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "cpu.h"
#include "cache.h"
#include "qelf.h"
uint32_t coupled_render_pc;
void coupled_render_enter(void) {}
void coupled_sync(void) {}
int coupled_active(void) { return 1; }
uint64_t coupled_alias_refill(uint32_t addr,uint64_t earliest) {
    assert((addr&~2047u)==0x13f80000u);
    return earliest+17; /* AR acceptance plus16 beats from the one-cycle native probe */
}
static char line[1024];static unsigned pos,complete;
static int rd(void *ctx,qsim_cpu*c,uint32_t a,unsigned size,uint32_t*v) {
    (void)ctx;assert(size==4);
    if(a==0x4f000000)*v=2;
    else if(a==0x40000004)*v=c->model->cyc;
    else {fprintf(stderr,"bad read %x\n",a);abort();}
    return 0;
}
static int wr(void *ctx,qsim_cpu*c,uint32_t a,unsigned size,uint32_t v) {
    (void)ctx;assert(size==4 && a==0x4f000004);
    putchar(v);line[pos++]=(char)v;assert(pos<sizeof line);
    if((char)v=='\n') {
        line[pos]=0;
        assert(!strstr(line,"KERNEL FAIL"));
        if(strstr(line,"KERNEL PASS")){complete=1;qsim_request_stop(c);}
        pos=0;
    }
    return 0;
}
static int trap(void*ctx,qsim_cpu*c,uint32_t cause,uint32_t tval) {
    (void)ctx;fprintf(stderr,"trap pc=%x cause=%u value=%x\n",c->pc,cause,tval);abort();
}
int main(int argc,char**argv) {
    assert(argc==3);qsim_cpu*c=qsim_cpu_new(NULL);assert(c);
    uint32_t entry;assert(!qsim_elf_load(c,argv[1],&entry));
    FILE*f=fopen(argv[2],"r");assert(f);uint32_t v;unsigned n=0;
    while(fscanf(f,"%x",&v)==1){assert(n<512);memcpy(c->sdram+0x3f80000+4*n,&v,4);n++;}fclose(f);assert(n==512);
    qsim_cpu_reset(c,entry);assert(!qsim_enable_profiling(c,NULL));
    c->env.mmio_read=rd;c->env.mmio_write=wr;c->env.trap=trap;
    int r=qsim_run(c,100000000);assert(r==QSIM_RUN_STOP && complete);
    fprintf(stderr,"CALIBRATION PASS %llu cycles\n",(unsigned long long)c->model->cyc);
    qsim_cpu_free(c);return 0;
}
