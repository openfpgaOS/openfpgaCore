/* Private LOD3 functional model. Uses qsim IEEE arithmetic, not a timing model
 * and not the retained CPU's reduced-product FMA RTL oracle. */
#include "fpu.h"
typedef struct {
    uint32_t regs[0x140/4],status,matrix_mask,output,count,pending[32][9];
    uint32_t delay,result_status,post_mask,pre[32];
    unsigned pre_count,phase,initialized;
    const char *mode;
    uint64_t jobs,vertices,writes,success,unsupported,read_errors,write_errors;
    uint64_t polls,cbos,pre_checks,post_checks,timeout_injections;
} load_model_state;
static load_model_state load_model;
static int lm_supported(uint32_t x) {
    unsigned e=(x>>23)&255;return e!=255&&(e!=0||(x&0x7fffffff)==0);
}
static uint32_t lm_op(unsigned op,uint32_t a,uint32_t b,uint32_t c,int *bad) {
    uint8_t flags=0;uint32_t r;
    *bad|=!lm_supported(a)||!lm_supported(b)||(op==2&&!lm_supported(c));
    if(op==0)r=qf_mul(a,b,0,&flags);
    else if(op==1)r=qf_add(a,b,0,&flags);
    else if(op==2)r=qf_fma(a,b,c,0,&flags);
    else r=qf_div(a,b,0,&flags);
    *bad|=!lm_supported(r);return r;
}
static void lm_initialize(void) {
    if(load_model.initialized)return;load_model.initialized=1;
    load_model.mode=getenv("LOD3_MODEL_MODE");
    if(!load_model.mode)load_model.mode="normal";
}
static void lm_cbo(void *ctx,qsim_cpu *cpu,unsigned op,uint32_t addr) {
    (void)cpu;load_model_state *m=&load_model;if(op)return;
    if(m->phase==1) {fatal(ctx,"LOD3 ownership: invalidate while active");return;}
    m->cbos++;
    if(m->phase==2&&addr>=m->output&&addr<m->output+m->count*64) {
        if(addr&63) {fatal(ctx,"LOD3 unaligned post-invalidate");return;}
        m->post_mask|=1u<<((addr-m->output)/64);m->post_checks++;
        uint32_t mask=m->count==32?~0u:(1u<<m->count)-1;
        if(m->post_mask==mask)m->phase=0;
    } else {
        if(m->pre_count<32)m->pre[m->pre_count++]=addr;
        else {memmove(m->pre,m->pre+1,31*4);m->pre[31]=addr;}
    }
}
static void lm_finish(qenv *E) {
    load_model_state *m=&load_model;
    unsigned rows=(m->result_status&4)?m->count:(m->result_status&0x600)?m->count/2+1:0;
    if(rows>m->count)rows=m->count;
    for(unsigned i=0;i<rows;i++) {
        uint32_t off=m->output+i*64-E->cpu->sdram_base;
        uint8_t *target=E->lint&&E->lint->strict?E->lint->C+off:gp(E,m->output+i*64,36);
        memcpy(target,m->pending[i],36);
        if(E->lint)for(unsigned b=0;b<36;b++)lint_gpu_wr(E->lint,off+b);
    }
    m->status=m->result_status|2u|0x100u;m->phase=2;m->post_mask=0;
    if(m->status&4)m->success++;
    if(m->status&8)m->unsupported++;
    if(m->status&0x60)m->read_errors++;
    if(m->status&0x600)m->write_errors++;
}
static void lm_start(qenv *E) {
    load_model_state *m=&load_model;
    if(m->phase) {fatal(E,"LOD3 job starts before prior ownership is released");return;}
    m->output=m->regs[0x40/4];m->count=m->regs[0x10/4]&63;
    unsigned first=m->regs[0x10/4]>>8&31;
    uint32_t src=m->regs[0xc/4],count=m->count;
    if(!count||count>32||count+first>32||(src&3)||(m->output&63)||
       !gp(E,src,count*24)||!gp(E,m->output,count*64)) {
        fatal(E,"LOD3 invalid driver descriptor");return;
    }
    if(m->matrix_mask!=65535) {fatal(E,"LOD3 incomplete matrix context");return;}
    if(m->pre_count<count) {fatal(E,"LOD3 missing ownership pre-invalidation");return;}
    for(unsigned i=0;i<count;i++)if(m->pre[m->pre_count-count+i]!=m->output+i*64) {
        fatal(E,"LOD3 incorrect pre-invalidation range");return;
    }
    m->pre_checks+=count;m->pre_count=0;m->jobs++;m->vertices+=count;
    int bad=0;uint8_t flags=0;unsigned all_inside=1;
    for(unsigned i=0;i<count;i++) {
        uint32_t raw[6];memcpy(raw,gp(E,src+i*24,24),24);
        uint8_t *bytes=(uint8_t*)raw;uint32_t *out=m->pending[i];
        for(unsigned j=0;j<4;j++) {
            uint32_t q=lm_op(0,raw[1],m->regs[0x100/4+4+j],0,&bad);
            q=lm_op(2,raw[0],m->regs[0x100/4+j],q,&bad);
            q=lm_op(1,q,m->regs[0x100/4+12+j],0,&bad);
            q=lm_op(2,raw[2],m->regs[0x100/4+8+j],q,&bad);
            if(!j)q=lm_op(0,q,m->regs[0x14/4],0,&bad);
            out[j]=q;
        }
        out[4]=lm_op(3,0x3f800000,out[3],0,&bad);
        int16_t tu,tv;memcpy(&tu,bytes+14,2);memcpy(&tv,bytes+16,2);
        int32_t uu=(int32_t)tu*(int32_t)(m->regs[0x2c/4]&65535);
        int32_t vv=(int32_t)tv*(int32_t)(m->regs[0x2c/4]>>16);
        out[5]=qf_from_i32((int16_t)(uu>>16),0,&flags);
        out[6]=qf_from_i32((int16_t)(vv>>16),0,&flags);
        out[7]=bytes[18]|((uint32_t)bytes[19]<<8)|((uint32_t)bytes[20]<<16)|((uint32_t)bytes[21]<<24);
        if(m->regs[0x30/4]&1) {
            uint32_t nx=qf_from_i32((int8_t)bytes[18],0,&flags);
            uint32_t ny=qf_from_i32((int8_t)bytes[19],0,&flags);
            uint32_t nz=qf_from_i32((int8_t)bytes[20],0,&flags);
            uint32_t dot=lm_op(0,ny,m->regs[0x1c/4],0,&bad);
            dot=lm_op(2,nx,m->regs[0x18/4],dot,&bad);
            dot=lm_op(2,nz,m->regs[0x20/4],dot,&bad);
            dot=lm_op(0,dot,0x3c010204,0,&bad);
            out[7]&=0xff000000;
            for(unsigned k=0;k<3;k++) {
                int32_t color=m->regs[0x24/4]>>(8*k)&255;
                if(qf_lt(0,dot,&flags)) {
                    uint32_t diffuse=qf_from_i32(m->regs[0x28/4]>>(8*k)&255,0,&flags);
                    uint32_t ambient=qf_from_i32(color,0,&flags);
                    color=(int32_t)qf_to_i32(lm_op(2,diffuse,dot,ambient,&bad),1,&flags);
                }
                if(color<0)color=0;if(color>255)color=255;
                out[7]|=(uint32_t)color<<(8*k);
            }
        }
        out[8]=0;
        for(unsigned j=0;j<3;j++) {
            if(qf_lt(out[j],out[3]^0x80000000,&flags))out[8]|=32>>(2*j);
            if(qf_lt(out[3],out[j],&flags))out[8]|=16>>(2*j);
        }
        all_inside&=out[8]==0;
    }
    m->result_status=bad?8:4|(all_inside?16:0);
    m->delay=2;
    if(!strcmp(m->mode,"faults")) {
        if(m->jobs%11==0)m->result_status=8;
        else if(m->jobs%13==0)m->result_status=0x40;
        else if(m->jobs%17==0)m->result_status=0x400;
    } else if(!strcmp(m->mode,"timeout")&&m->jobs==100) {
        m->delay=4100;m->timeout_injections++;
    }
    m->status=1|0x100;m->phase=1;
}
static int lm_read(qenv *E,uint32_t off,unsigned size,uint32_t *val) {
    lm_initialize();load_model_state *m=&load_model;
    if(size!=4||(off&3)) {fatal(E,"LOD3 non-word read");return 0;}
    if(!off){*val=!strcmp(m->mode,"absent")?0:0x4c4f4403;return 0;}
    if(off==4) {
        m->polls++;if(m->phase==1&&!--m->delay)lm_finish(E);
        *val=m->status;return 0;
    }
    *val=off<0x140?m->regs[off/4]:0;return 0;
}
static int lm_write(qenv *E,uint32_t off,unsigned size,uint32_t val) {
    lm_initialize();load_model_state *m=&load_model;
    if(size!=4||(off&3)) {fatal(E,"LOD3 non-word write");return 0;}
    if(m->phase==1) {fatal(E,"LOD3 context changed while active");return 0;}
    m->writes++;
    if(off==8) {if(val&2)m->status&=~2u;if(val&1)lm_start(E);return 0;}
    if(off<0x140) {
        m->regs[off/4]=val;m->status&=~4u;
        if(off>=0x100)m->matrix_mask|=1u<<((off-0x100)/4);
    }
    return 0;
}
void env_load_report(qenv *E,const char *out) {
    char path[1024];snprintf(path,sizeof path,"%s/lod3-model.json",out);
    FILE *f=fopen(path,"w");if(!f)abort();load_model_state *m=&load_model;
    if(m->phase)fatal(E,"LOD3 capture ends with unreleased ownership");
    fprintf(f,"{\"mode\":\"%s\",\"functional_only\":true,\"jobs\":%llu,\"vertices\":%llu,\"register_writes\":%llu,\"success\":%llu,\"unsupported\":%llu,\"read_errors\":%llu,\"write_errors\":%llu,\"status_reads\":%llu,\"pre_invalidated_slots\":%llu,\"post_invalidated_slots\":%llu,\"timeout_injections\":%llu}\n",
       m->mode?m->mode:"unused",(unsigned long long)m->jobs,(unsigned long long)m->vertices,
       (unsigned long long)m->writes,(unsigned long long)m->success,(unsigned long long)m->unsupported,
       (unsigned long long)m->read_errors,(unsigned long long)m->write_errors,(unsigned long long)m->polls,
       (unsigned long long)m->pre_checks,(unsigned long long)m->post_checks,(unsigned long long)m->timeout_injections);
    fclose(f);
}
