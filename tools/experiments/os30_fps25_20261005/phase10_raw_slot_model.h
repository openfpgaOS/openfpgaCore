/* Private executable native-FMA raw cull/formatter reference. gpu.c's os25
 * raster model does not render these legacy commands; actual RTL replay is
 * the rendering gate. Recursive legacy emission supplies ordered semantics. */
#include "fpu.h"
#include <assert.h>
static void exec_command(gpu_t *,uint32_t,const uint32_t *);
static uint32_t rs_op(unsigned op,uint32_t a,uint32_t b,uint32_t c) {
    uint8_t flags=0;uint32_t r=op==0?qf_mul(a,b,0,&flags):op==1?qf_add(a,b,0,&flags):qf_fma(a,b,c,0,&flags);
    unsigned e=r>>23&255;assert(!(r&0x7fffffff)||(e&&e!=255));return r;
}
static int rs_reject(gpu_t*g,unsigned a,unsigned b,unsigned c,unsigned mode) {
    if(!mode)return 0;if(mode==3)return 1;
    uint32_t*x=g->raw_slot[a],*y=g->raw_slot[b],*z=g->raw_slot[c];
    uint32_t bx=rs_op(0,y[0],y[3],0),by=rs_op(0,y[1],y[3],0);
    uint32_t dx1=rs_op(2,x[0],x[3],bx^0x80000000u),dy1=rs_op(2,x[1],x[3],by^0x80000000u);
    uint32_t dx3=rs_op(2,z[0],z[3],bx^0x80000000u),dy3=rs_op(2,z[1],z[3],by^0x80000000u);
    uint32_t p=rs_op(0,dx1,dy3,0),q=rs_op(0,dy1,dx3,0),cross=rs_op(1,p,q^0x80000000u,0);
    return !(cross&0x7fffffffu)||(mode==1?(cross>>31):!(cross>>31));
}
static void rs_execute(gpu_t*g,uint32_t hdr,const uint32_t*p) {
    unsigned op=hdr>>24,n=hdr&8191u;
    if(op==0x60){assert(n==6);unsigned slot=hdr>>13&31;assert(!(hdr>>18&63));memcpy(g->raw_slot[slot],p,24);g->raw_valid|=1u<<slot;g->raw_projected&=~(1u<<slot);g->raw_uploads++;return;}
    if(op==0x61){assert(n==5);memcpy(g->raw_context,p,20);assert(!(p[0]&0xffff00feu)&&p[4]<=1);g->raw_context_valid=1;g->raw_projected=0;g->raw_contexts++;return;}
    assert(op==0x62&&n==1&&g->raw_context_valid);
    unsigned slots[3]={p[0]&31,p[0]>>5&31,p[0]>>10&31};
    uint32_t needed=(1u<<slots[0])|(1u<<slots[1])|(1u<<slots[2]);assert((g->raw_valid&needed)==needed);
    g->raw_triangles++;
    if(rs_reject(g,slots[0],slots[1],slots[2],p[0]>>15&3)){g->raw_rejected++;return;}
    for(unsigned k=0;k<3;k++)if(!(g->raw_projected&(1u<<slots[k]))) {
        unsigned slot=slots[k];uint32_t*v=g->raw_slot[slot],*d=g->raw_context,out[8];uint8_t flags=0;
        out[0]=slot;
        out[1]=qf_to_i32(qf_mul(v[0],0x43800000u,0,&flags),0,&flags);
        out[2]=qf_to_i32(qf_mul(v[1],0x43800000u,0,&flags),0,&flags);
        out[3]=qf_to_i32(qf_mul(v[2],0x43800000u,0,&flags),0,&flags);
        out[4]=(d[0]&1)?((int32_t)(int16_t)v[4]-(int32_t)(d[3]&65535)*8)*2048+(d[4]?32768:0):0;
        out[5]=(d[0]&1)?((int32_t)(int16_t)(v[4]>>16)-(int32_t)(d[3]>>16)*8)*2048+(d[4]?32768:0):0;
        unsigned source=d[0]>>8&255;assert(source!=7);
        uint32_t rgb=source==255?0xffffff:source==4?v[5]:source==3?d[1]:source==5?d[2]:0;
        out[6]=((rgb&255)>>3)<<11|((rgb>>8&255)>>2)<<5|((rgb>>16&255)>>3);
        uint32_t depth=qf_mul(v[3],0x4e800000u,0,&flags);
        if(qf_lt(depth,0x3f800000u,&flags))depth=0x3f800000u;
        if(qf_lt(0x4effffffu,depth,&flags))depth=0x4effffffu;
        out[7]=qf_to_i32(depth,0,&flags);
        g->raw_internal=1;exec_command(g,0x56000008u,out);g->raw_internal=0;
        g->raw_projected|=1u<<slot;g->raw_formatted++;
    }
    uint32_t tri=p[0]&32767u;g->raw_internal=1;exec_command(g,0x54000001u,&tri);g->raw_internal=0;g->raw_draws++;
}
void gpu_raw_slot_report(gpu_t*g,const char*out) {
    char path[4096];snprintf(path,sizeof path,"%s/raw-slot-model.json",out);FILE*f=fopen(path,"w");assert(f);
    fprintf(f,"{\"raw_uploads\":%llu,\"contexts\":%llu,\"triangles\":%llu,\"rejected\":%llu,\"formatted\":%llu,\"draws\":%llu,\"transport_words\":%llu,\"reference_only\":true}\n",
        (unsigned long long)g->raw_uploads,(unsigned long long)g->raw_contexts,(unsigned long long)g->raw_triangles,
        (unsigned long long)g->raw_rejected,(unsigned long long)g->raw_formatted,(unsigned long long)g->raw_draws,
        (unsigned long long)(7*g->raw_uploads+6*g->raw_contexts+2*g->raw_triangles));fclose(f);
}
