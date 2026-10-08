/* GDR1 functional qsim peripheral. IEEE instruction DAG only; real RTL timing
 * and retained reduced-product FMA are separately validated. */
#include "fpu.h"
typedef struct {
 uint32_t regs[0x140/4],status,matrix_mask,valid,raw[32][9],pending[32][9];
 uint32_t jobs[64],jobcount,counts,accepted,reason,stale,words,vertices;
 unsigned phase,delay,operation,count,first,prechecked,result_status,initialized;
 const char *mode;
 uint64_t loads,load_vertices,load_success,load_failures,prechecks,commits,commit_failures,draws,packets,readbacks,writes,polls;
} gd_model_state;
static gd_model_state gdm;
static int gm_supported(uint32_t x){unsigned e=(x>>23)&255;return e!=255&&(e!=0||(x&0x7fffffff)==0);}
static uint32_t gm_op(unsigned op,uint32_t a,uint32_t b,uint32_t c,int*bad){
 uint8_t flags=0;uint32_t r;*bad|=!gm_supported(a)||!gm_supported(b)||(op==2&&!gm_supported(c));
 if(op==0)r=qf_mul(a,b,0,&flags);else if(op==1)r=qf_add(a,b,0,&flags);else if(op==2)r=qf_fma(a,b,c,0,&flags);else r=qf_div(a,b,0,&flags);
 *bad|=!gm_supported(r);return r;
}
static void gm_initialize(void){if(gdm.initialized)return;gdm.initialized=1;gdm.mode=getenv("GDR1_MODEL_MODE");if(!gdm.mode)gdm.mode="normal";}
static void gm_load(qenv *E){
 gd_model_state*m=&gdm;m->loads++;m->count=m->regs[0x10/4]&63;m->first=m->regs[0x10/4]>>8&31;
 unsigned count=m->count;uint32_t src=m->regs[0xc/4];
 if(!count||count>32||count+m->first>32||(src&3)||!gp(E,src,count*24)||m->matrix_mask!=65535){fatal(E,"GDR1 invalid LOAD descriptor/context");return;}
 uint32_t mask=(count==32?~0u:(1u<<count)-1)<<m->first;m->valid&=~mask;m->load_vertices+=count;
 int bad=0;uint8_t flags=0;unsigned all_inside=1;
    for(unsigned i=0;i<count;i++) {
        uint32_t raw[6];memcpy(raw,gp(E,src+i*24,24),24);
        uint8_t *bytes=(uint8_t*)raw;uint32_t *out=m->pending[i];
        for(unsigned j=0;j<4;j++) {
            uint32_t q=gm_op(0,raw[1],m->regs[0x100/4+4+j],0,&bad);
            q=gm_op(2,raw[0],m->regs[0x100/4+j],q,&bad);
            q=gm_op(1,q,m->regs[0x100/4+12+j],0,&bad);
            q=gm_op(2,raw[2],m->regs[0x100/4+8+j],q,&bad);
            if(!j)q=gm_op(0,q,m->regs[0x14/4],0,&bad);
            out[j]=q;
        }
        out[4]=gm_op(3,0x3f800000,out[3],0,&bad);
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
            uint32_t dot=gm_op(0,ny,m->regs[0x1c/4],0,&bad);
            dot=gm_op(2,nx,m->regs[0x18/4],dot,&bad);
            dot=gm_op(2,nz,m->regs[0x20/4],dot,&bad);
            dot=gm_op(0,dot,0x3c010204,0,&bad);
            out[7]&=0xff000000;
            for(unsigned k=0;k<3;k++) {
                int32_t color=m->regs[0x24/4]>>(8*k)&255;
                if(qf_lt(0,dot,&flags)) {
                    uint32_t diffuse=qf_from_i32(m->regs[0x28/4]>>(8*k)&255,0,&flags);
                    uint32_t ambient=qf_from_i32(color,0,&flags);
                    color=(int32_t)qf_to_i32(gm_op(2,diffuse,dot,ambient,&bad),1,&flags);
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
 if(!strcmp(m->mode,"faults")){if(m->loads%11==0)m->result_status=8;else if(m->loads%13==0)m->result_status=0x40;}
 m->phase=1;m->operation=1;m->delay=2;m->status=1|0x100;
}
static unsigned gm_reject(uint32_t*a,uint32_t*b,uint32_t*c,unsigned cull,int*bad){
 if(a[8]&b[8]&c[8])return 1;
 if(!cull)return 0;
 uint32_t bx=gm_op(0,b[0],b[4],0,bad),by=gm_op(0,b[1],b[4],0,bad);
 uint32_t dx1=gm_op(2,a[0],a[4],bx^0x80000000,bad),dy1=gm_op(2,a[1],a[4],by^0x80000000,bad);
 uint32_t dx2=gm_op(2,c[0],c[4],bx^0x80000000,bad),dy2=gm_op(2,c[1],c[4],by^0x80000000,bad);
 uint32_t lhs=gm_op(0,dx1,dy2,0,bad),rhs=gm_op(0,dy1,dx2,0,bad);
 uint32_t cross=gm_op(1,lhs,rhs^0x80000000,0,bad);
 if((a[3]^b[3]^c[3])>>31)cross^=0x80000000;
 uint8_t f=0;return cull==3||(cull==1&&qf_le(cross,0,&f))||(cull==2&&qf_le(0,cross,&f))?2:0;
}
static void gm_precheck(qenv*E){
 gd_model_state*m=&gdm;m->prechecks++;m->prechecked=0;m->accepted=m->jobcount=m->counts=m->reason=0;
 unsigned budget=m->regs[0x48/4]>>8&127,cull=m->regs[0x48/4]&3,seen=0;
 uint32_t address=m->regs[0x44/4];
 if((address&7)||budget<1||budget>64){fatal(E,"GDR1 invalid PRECHECK descriptor");return;}
 for(unsigned i=0;i<64;i++){
  uint32_t w[2];void*p=gp(E,address+i*8,8);if(!p){m->reason=6;break;}memcpy(w,p,8);
  unsigned op=w[0]>>24,n=op==5?1:op==6?2:0;if(!n||seen+n>budget)break;
  unsigned idx[2][3],reject[2]={0},reason=0;
  for(unsigned t=0;t<n;t++){
   for(unsigned k=0;k<3;k++){unsigned x=(w[t]>>(16-8*k)&255)/2;idx[t][k]=x;if(x>=32||!(m->valid&(1u<<x)))reason=1;}
   if(reason)break;
   uint32_t*a=m->raw[idx[t][0]],*b=m->raw[idx[t][1]],*c=m->raw[idx[t][2]];int bad=0;
   reject[t]=gm_reject(a,b,c,cull,&bad);if(bad){reason=1;break;}
   if(reject[t])continue;
   if(a[8]|b[8]|c[8]){reason=2;break;}
   if(a[3]-1u>=0x46fffdffu||b[3]-1u>=0x46fffdffu||c[3]-1u>=0x46fffdffu||(a[3]==b[3]&&a[3]==c[3])){reason=3;break;}
  }
  if(reason){m->reason=reason;break;}
  for(unsigned t=0;t<n;t++){
   if(reject[t]==1)m->counts++;else if(reject[t]==2)m->counts+=256;
   else m->jobs[m->jobcount++]=idx[t][0]|(idx[t][1]<<5)|(idx[t][2]<<10);
  }
  seen+=n;m->accepted++;
 }
 m->counts|=seen<<16;m->result_status=4;m->prechecked=1;
 if(!strcmp(m->mode,"faults")&&m->prechecks%23==0){m->result_status=0x40;m->prechecked=0;}
 m->phase=1;m->operation=2;m->delay=2;m->status=1|0x100;
}
static void gm_gpu_word(qenv*E,uint32_t word){
 if(E->gpu){if(E->vec_nframes)vec_mmio_write(E,4,word);else gpu_mmio_write(E->gpu,4,word);}
 else {if(E->ng_staged>=4095){fatal(E,"GDR1 ring overflow");return;}E->ng_words[E->ng_staged++]=word;}
}
static void gm_publish(qenv*E){
 if(E->gpu){if(E->vec_nframes)vec_mmio_write(E,0,8);else gpu_mmio_write(E->gpu,0,8);}
 else {E->ng_wrptr=(E->ng_wrptr+E->ng_staged)&4095;ng_publish(E);}
}
static void gm_commit(qenv*E){
 gd_model_state*m=&gdm;if(!m->prechecked){fatal(E,"GDR1 COMMIT without PRECHECK");return;}
 m->commits++;m->stale=m->regs[0x64/4];m->words=m->vertices=0;
 unsigned flags=m->regs[0x60/4],source=flags>>8&255,linear=m->regs[0x74/4],textured=flags&1;uint32_t words[1856];
 if(source==7||linear>1){fatal(E,"GDR1 unsupported DRAW uniforms");return;}
 for(unsigned t=0;t<m->jobcount;t++){
  unsigned slot[3]={m->jobs[t]&31,m->jobs[t]>>5&31,m->jobs[t]>>10&31},dirty=0;
  for(unsigned k=0;k<3;k++)dirty|=((m->stale>>slot[k])&1)<<k;
  for(unsigned k=0;k<3;k++)if(dirty&(1u<<k)){
   uint32_t*v=m->raw[slot[k]];uint8_t f=0;
   words[m->words++]=0x56000008;words[m->words++]=slot[k];
   unsigned coords[3]={0,1,3};for(unsigned j=0;j<3;j++)words[m->words++]=qf_to_i32(qf_mul(v[coords[j]],0x43800000,0,&f),0,&f);
   int32_t u=(int32_t)qf_to_i32(v[5],1,&f),vv=(int32_t)qf_to_i32(v[6],1,&f);
   words[m->words++]=textured?(uint32_t)((u-(int32_t)(m->regs[0x70/4]&65535)*8)*2048+(linear?32768:0)):0;
   words[m->words++]=textured?(uint32_t)((vv-(int32_t)(m->regs[0x70/4]>>16)*8)*2048+(linear?32768:0)):0;
   uint32_t rgb=source==255?0xffffff:source==4?v[7]:source==3?m->regs[0x68/4]:source==5?m->regs[0x6c/4]:0;
   words[m->words++]=((rgb&255)>>3)<<11|((rgb>>8&255)>>2)<<5|((rgb>>16&255)>>3);
   uint32_t depth=qf_mul(v[4],0x4e800000,0,&f);if(qf_lt(depth,0x3f800000,&f))depth=0x3f800000;if(qf_lt(0x4effffff,depth,&f))depth=0x4effffff;
   words[m->words++]=qf_to_i32(depth,0,&f);m->vertices++;
  }
  words[m->words++]=0x54000001;words[m->words++]=m->jobs[t];
  for(unsigned k=0;k<3;k++)m->stale&=~(1u<<slot[k]);
 }
 m->result_status=4;
 if(!strcmp(m->mode,"faults")&&m->commits%19==0){m->result_status=8;m->commit_failures++;m->words=m->vertices=0;m->stale=m->regs[0x64/4];}
 else {
  /* The real worker stages exactly this whole bounded batch, then publishes
   * atomically. This functional model deliberately has no DMA/queue timing. */
  for(unsigned i=0;i<m->words;i++)gm_gpu_word(E,words[i]);gm_publish(E);m->draws+=m->jobcount;m->packets+=m->vertices;
 }
 m->phase=1;m->operation=3;m->delay=2;m->status=1|0x100;m->prechecked=0;
}
static void gm_finish(void){
 gd_model_state*m=&gdm;
 if(m->operation==1){
  if(m->result_status&4){for(unsigned i=0;i<m->count;i++)memcpy(m->raw[m->first+i],m->pending[i],36);m->valid|=(m->count==32?~0u:(1u<<m->count)-1)<<m->first;m->load_success++;}
  else m->load_failures++;
 }
 m->status=m->result_status|2|0x100;m->phase=0;
}
static int gm_read(qenv*E,uint32_t off,unsigned size,uint32_t*val){
 gm_initialize();gd_model_state*m=&gdm;if(size!=4||(off&3)){fatal(E,"GDR1 invalid MMIO read");return 0;}
 if(off==0){*val=!strcmp(m->mode,"absent")?0:0x47445201;return 0;}
 if(off==4){m->polls++;if(m->phase&&!--m->delay)gm_finish();*val=m->status;return 0;}
 if(off>=0x400&&off<0x800){unsigned row=(off-0x400)/128,slot=(off/4)&31;uint32_t*v=m->raw[slot];if(m->phase||!(m->valid&(1u<<slot))){fatal(E,"GDR1 unowned raw read");return 0;}m->readbacks++;uint8_t f=0;
  *val=row<5?v[row]:row==5?((uint16_t)qf_to_i32(v[5],1,&f)|((uint32_t)(uint16_t)qf_to_i32(v[6],1,&f)<<16)):row==6?v[7]:v[8];return 0;}
 switch(off){case 0x4c:*val=m->accepted;return 0;case 0x50:*val=m->jobcount;return 0;case 0x54:*val=m->reason;return 0;case 0x58:*val=m->counts;return 0;case 0x5c:*val=m->stale;return 0;case 0x78:*val=m->words;return 0;case 0x7c:*val=m->vertices;return 0;}
 *val=off<0x140?m->regs[off/4]:0;return 0;
}
static int gm_write(qenv*E,uint32_t off,unsigned size,uint32_t value){
 gm_initialize();gd_model_state*m=&gdm;if(size!=4||(off&3)||m->phase){fatal(E,"GDR1 invalid/active MMIO write");return 0;}m->writes++;
 if(off==8){
  if(value&16){m->prechecked=0;m->status=0x100;return 0;}
  if(value&2)m->status&=~2u;
  if(value&1)gm_load(E);else if(value&4)gm_precheck(E);else if(value&8)gm_commit(E);return 0;
 }
 if(off==0x40){if(m->prechecked){fatal(E,"GDR1 invalidate owned RUN");return 0;}m->valid&=~value;return 0;}
 if(off<0x140){if(m->prechecked&&off<0x60){fatal(E,"GDR1 context changes under RUN ownership");return 0;}m->regs[off/4]=value;if(off>=0x100)m->matrix_mask|=1u<<((off-0x100)/4);}
 return 0;
}
void env_gd_report(qenv*E,const char*out){
 gd_model_state*m=&gdm;char path[1024];snprintf(path,sizeof path,"%s/gdr1-model.json",out);FILE*f=fopen(path,"w");if(!f)abort();
 if(m->phase||m->prechecked)fatal(E,"GDR1 capture ends while worker owned");
 fprintf(f,"{\"functional_only\":true,\"mode\":\"%s\",\"loads\":%llu,\"load_vertices\":%llu,\"load_success\":%llu,\"load_failures\":%llu,\"prechecks\":%llu,\"commits\":%llu,\"commit_failures\":%llu,\"draws\":%llu,\"packets\":%llu,\"raw_readbacks\":%llu,\"register_writes\":%llu,\"polls\":%llu}\n",m->mode?m->mode:"unused",(unsigned long long)m->loads,(unsigned long long)m->load_vertices,(unsigned long long)m->load_success,(unsigned long long)m->load_failures,(unsigned long long)m->prechecks,(unsigned long long)m->commits,(unsigned long long)m->commit_failures,(unsigned long long)m->draws,(unsigned long long)m->packets,(unsigned long long)m->readbacks,(unsigned long long)m->writes,(unsigned long long)m->polls);fclose(f);
}
