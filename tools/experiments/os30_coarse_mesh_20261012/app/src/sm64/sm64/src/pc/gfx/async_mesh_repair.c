/* Pure ordered-job repair, derived from frozen F19 gfx_pc.c/gfx_gpu.c.
 * Renderer state is supplied by value. Arithmetic helpers pin the target DAG
 * against the application's -ffast-math; host tests use IEEE binary32/fmaf. */
#include "async_mesh_repair.h"
#include <string.h>
#ifndef __riscv
#include <math.h>
#include <fenv.h>
#endif

static inline float from_bits(uint32_t u) {float f;memcpy(&f,&u,4);return f;}
static inline uint32_t bits(float f) {uint32_t u;memcpy(&u,&f,4);return u;}
static inline float neg(float f) {return from_bits(bits(f)^0x80000000u);}
static inline float absolute(float f) {return from_bits(bits(f)&0x7fffffffu);}
#ifdef __riscv
#define AM_BIN(name,op) static inline float name(float a,float b) {float r;__asm__ volatile(op " %0,%1,%2,rne":"=f"(r):"f"(a),"f"(b));return r;}
AM_BIN(add,"fadd.s") AM_BIN(sub,"fsub.s") AM_BIN(mul,"fmul.s") AM_BIN(divide,"fdiv.s")
static inline float fused(float a,float b,float c) {float r;__asm__ volatile("fmadd.s %0,%1,%2,%3,rne":"=f"(r):"f"(a),"f"(b),"f"(c));return r;}
static inline int lt(float a,float b) {int r;__asm__ volatile("flt.s %0,%1,%2":"=r"(r):"f"(a),"f"(b));return r;}
static inline int le(float a,float b) {int r;__asm__ volatile("fle.s %0,%1,%2":"=r"(r):"f"(a),"f"(b));return r;}
static inline int32_t rne(float a) {int32_t r;__asm__ volatile("fcvt.w.s %0,%1,rne":"=r"(r):"f"(a));return r;}
static inline int32_t rtz(float a) {int32_t r;__asm__ volatile("fcvt.w.s %0,%1,rtz":"=r"(r):"f"(a));return r;}
typedef unsigned fp_environment;
static inline void fp_enter(fp_environment *s) {__asm__ volatile("csrr %0,fcsr\n\tcsrw fcsr,zero":"=r"(*s)::"memory");}
static inline void fp_leave(fp_environment s) {__asm__ volatile("csrw fcsr,%0"::"r"(s):"memory");}
#else
static inline float add(float a,float b) {volatile float r=a+b;return r;}
static inline float sub(float a,float b) {volatile float r=a-b;return r;}
static inline float mul(float a,float b) {volatile float r=a*b;return r;}
static inline float divide(float a,float b) {volatile float r=a/b;return r;}
static inline float fused(float a,float b,float c) {return fmaf(a,b,c);}
static inline int lt(float a,float b) {return a<b;}
static inline int le(float a,float b) {return a<=b;}
/* RV32 FCVT saturation, including NaN, without a host float-to-int UB. */
static inline int32_t rne(float a) {
    if(isnan(a)||a>=2147483648.0f)return INT32_MAX;
    if(a<=-2147483648.0f)return INT32_MIN;
    return (int32_t)nearbyintf(a);
}
static inline int32_t rtz(float a) {
    if(isnan(a)||a>=2147483648.0f)return INT32_MAX;
    if(a<=-2147483648.0f)return INT32_MIN;
    return (int32_t)a;
}
typedef fenv_t fp_environment;
static inline void fp_enter(fp_environment *s) {feholdexcept(s);fesetround(FE_TONEAREST);}
static inline void fp_leave(fp_environment s) {fesetenv(&s);}
#endif

struct vertex {float x,y,z,w,iw,u,v;uint8_t rgba[4];uint8_t clip;};
struct sink {am_repair_emit emit;void *opaque;struct am_repair_result *result;};
static int put(struct sink *s,uint32_t w) {
    if(!s->emit(s->opaque,w))return 0;
    ++s->result->emitted_words;return 1;
}
static int put_all(struct sink *s,const uint32_t *w,unsigned n) {
    for(unsigned i=0;i<n;++i)if(!put(s,w[i]))return 0;
    return 1;
}
static uint16_t le16(const unsigned char *p) {return p[0]|(uint16_t)p[1]<<8;}
static uint32_t le32(const unsigned char *p) {return le16(p)|(uint32_t)le16(p+2)<<16;}
static uint8_t clip_bits(const struct vertex *v) {
    return (lt(v->x,neg(v->w))?32:0)|(lt(v->w,v->x)?16:0)|
           (lt(v->y,neg(v->w))?8:0)|(lt(v->w,v->y)?4:0)|
           (lt(v->z,neg(v->w))?2:0)|(lt(v->w,v->z)?1:0);
}
static void recompute(const struct am_repair_job *j,struct vertex *out) {
    const struct am_transform *t=&j->transform;
    for(unsigned i=0;i<j->count;++i) {
        const unsigned char *p=j->vertices+24*i;
        float ox=from_bits(le32(p)),oy=from_bits(le32(p+4)),oz=from_bits(le32(p+8));
        float xyz[4];
        /* Exact existing compiler/LOD5 order: Y product; X FMA; translation
         * addition; Z FMA; aspect only on X. No reassociation is permitted. */
        for(unsigned k=0;k<4;++k) {
            float q=mul(oy,from_bits(t->matrix[4+k]));
            q=fused(ox,from_bits(t->matrix[k]),q);
            q=add(q,from_bits(t->matrix[12+k]));
            xyz[k]=fused(oz,from_bits(t->matrix[8+k]),q);
        }
        struct vertex *v=&out[i];
        v->x=mul(xyz[0],from_bits(t->aspect));v->y=xyz[1];v->z=xyz[2];v->w=xyz[3];
        v->iw=divide(1.0f,v->w);
        int32_t up=(int16_t)le16(p+14)*(int32_t)(t->scales&65535);
        int32_t vp=(int16_t)le16(p+16)*(int32_t)(t->scales>>16);
        v->u=(float)(int16_t)(up>>16);v->v=(float)(int16_t)(vp>>16);
        for(unsigned c=0;c<4;++c)v->rgba[c]=p[18+c];
        if(t->lighting) {
            float intensity=mul((float)(int8_t)p[19],from_bits(t->coefficient[1]));
            intensity=fused((float)(int8_t)p[18],from_bits(t->coefficient[0]),intensity);
            intensity=fused((float)(int8_t)p[20],from_bits(t->coefficient[2]),intensity);
            intensity=mul(intensity,from_bits(0x3c010204u));
            for(unsigned c=0;c<3;++c) {
                int32_t color=(t->ambient>>(8*c))&255;
                if(lt(0.0f,intensity))color=rtz(fused(intensity,(float)((t->diffuse>>(8*c))&255),(float)color));
                v->rgba[c]=color<0?0:color>255?255:(uint8_t)color;
            }
        }
        v->clip=clip_bits(v);
    }
}
static void raw_vertices(const struct am_repair_job *j,struct vertex *v) {
    for(unsigned i=0;i<j->count;++i) {
        const uint32_t *r=j->raw[i];
        v[i].x=from_bits(r[0]);v[i].y=from_bits(r[1]);v[i].z=from_bits(r[2]);
        v[i].w=from_bits(r[3]);v[i].iw=from_bits(r[4]);
        v[i].u=(float)(int16_t)r[5];v[i].v=(float)(int16_t)(r[5]>>16);
        for(unsigned c=0;c<4;++c)v[i].rgba[c]=(uint8_t)(r[6]>>(8*c));
        v[i].clip=(uint8_t)r[7];
    }
}
static int reject(const struct vertex *a,const struct vertex *b,const struct vertex *c,unsigned mode) {
    if(a->clip&b->clip&c->clip)return 1;
    if(!mode)return 0;
    float cx=mul(b->x,b->iw),cy=mul(b->y,b->iw);
    float dx0=fused(a->x,a->iw,neg(cx)),dy0=fused(a->y,a->iw,neg(cy));
    float dx2=fused(c->x,c->iw,neg(cx)),dy2=fused(c->y,c->iw,neg(cy));
    float cross=sub(mul(dx0,dy2),mul(dy0,dx2));
    if(lt(a->w,0.0f)^lt(b->w,0.0f)^lt(c->w,0.0f))cross=neg(cross);
    return (mode==3||(mode==1?le(cross,0.0f):le(0.0f,cross)))?2:0;
}
static float lerp(float a,float b,float t) {return fused(t,sub(b,a),a);}
static struct vertex intersect(const struct vertex *a,const struct vertex *b,float t) {
    struct vertex v;
    v.x=lerp(a->x,b->x,t);v.y=lerp(a->y,b->y,t);v.z=lerp(a->z,b->z,t);
    v.w=lerp(a->w,b->w,t);v.u=lerp(a->u,b->u,t);v.v=lerp(a->v,b->v,t);
    for(unsigned k=0;k<4;++k)
        v.rgba[k]=(uint8_t)((int32_t)a->rgba[k]+rtz(mul(t,(float)((int)b->rgba[k]-(int)a->rgba[k]))));
    v.iw=divide(1.0f,v.w);v.clip=0;return v;
}
static uint16_t color565(const struct vertex *v,const struct am_material *m) {
    return (m->flags_rgb&(1u<<18))?((uint16_t)(v->rgba[0]>>3)<<11)|((uint16_t)(v->rgba[1]>>2)<<5)|(v->rgba[2]>>3):(uint16_t)m->flags_rgb;
}
static void texture(const struct vertex *v,const struct am_material *m,float *u,float *t) {
    if(m->flags_rgb&(1u<<16)) {
        float bias=(m->flags_rgb&(1u<<17))?0.5f:0.0f;
        *u=fused(sub(v->u,(float)m->u_offset),0.03125f,bias);
        *t=fused(sub(v->v,(float)m->v_offset),0.03125f,bias);
    } else {*u=0;*t=0;}
}
static uint32_t depth_word(float iw) {
    float d=mul(iw,1073741824.0f);
    if(lt(d,1.0f))d=1.0f;else if(lt(2147483520.0f,d))d=2147483520.0f;
    return (uint32_t)rne(d);
}
static void project(const struct vertex *v,const struct am_material *m,int16_t *x,int16_t *y) {
    int32_t xq=rne(mul(v->x,256.0f)),yq=rne(mul(v->y,256.0f)),wq=rne(mul(v->w,256.0f));
    uint32_t den=wq<256?256u:(uint32_t)wq;
    uint32_t reciprocal=UINT32_MAX/den+((den&(den-1u))==0);
    int32_t rx=(int32_t)(((int64_t)xq*reciprocal)>>16),ry=(int32_t)(((int64_t)yq*reciprocal)>>16);
    int64_t sx=m->xcenter*16LL+(((int64_t)m->xscale*rx)>>12);
    int64_t sy=m->ycenter-(((int64_t)m->yscale*ry)>>16);
    *x=sx < -32768?-32768:sx>32767?32767:(int16_t)sx;
    *y=sy < -32768?-32768:sy>32767?32767:(int16_t)sy;
}
static int direct(struct sink *s,const struct am_material *m,const struct vertex *a,const struct vertex *b,const struct vertex *c) {
    const struct vertex *v[3]={a,b,c};float u[3],t[3],scale=64.0f,max=0.0f;
    uint32_t w[18]={0x4e000011u};uint16_t rgb[3];
    for(unsigned k=0;k<3;++k) {
        texture(v[k],m,&u[k],&t[k]);
        if(m->flags_rgb&(1u<<16)) {
            float candidates[3]={absolute(v[k]->iw),absolute(mul(u[k],v[k]->iw)),absolute(mul(t[k],v[k]->iw))};
            for(unsigned i=0;i<3;++i)if(lt(max,candidates[i]))max=candidates[i];
        }
    }
    if(m->flags_rgb&(1u<<16)) {if(lt(0.0f,max))scale=divide(8192.0f,max);if(lt(scale,1.0f))scale=1.0f;}
    for(unsigned k=0;k<3;++k) {
        int16_t x,y;project(v[k],m,&x,&y);
        w[1+k]=(uint16_t)x|((uint32_t)(uint16_t)y<<16);
        w[4+k]=(uint32_t)rne(mul(u[k],65536.0f));w[7+k]=(uint32_t)rne(mul(t[k],65536.0f));
        int32_t zi=rne(mul(v[k]->iw,mul(65536.0f,scale)));w[10+k]=zi<1?1:(uint32_t)zi;
        rgb[k]=color565(v[k],m);w[15+k]=depth_word(v[k]->iw);
    }
    w[13]=(uint32_t)rgb[0]|((uint32_t)rgb[1]<<16);w[14]=rgb[2];
    if(!put_all(s,w,18))return 0;
    ++s->result->direct_triangles;return 1;
}
static int indexed(struct sink *s,const struct am_material *m,const struct vertex *v,const unsigned indices[3]) {
    for(unsigned k=0;k<3;++k) {
        const struct vertex *a=&v[indices[k]];float u,t;texture(a,m,&u,&t);
        uint32_t w[9]={0x56000008u,indices[k],(uint32_t)rne(mul(a->x,256.0f)),(uint32_t)rne(mul(a->y,256.0f)),
            (uint32_t)rne(mul(a->w,256.0f)),(uint32_t)rne(mul(u,65536.0f)),(uint32_t)rne(mul(t,65536.0f)),color565(a,m),depth_word(a->iw)};
        if(!put_all(s,w,9))return 0;
    }
    uint32_t w[2]={0x54000001u,indices[0]|(indices[1]<<5)|(indices[2]<<10)};
    if(!put_all(s,w,2))return 0;
    ++s->result->indexed_triangles;return 1;
}
static int clipped(struct sink *s,const struct am_material *m,const struct vertex *a,const struct vertex *b,const struct vertex *c) {
    struct vertex poly[2][12];unsigned counts[2]={3,0},side=0;
    poly[0][0]=*a;poly[0][1]=*b;poly[0][2]=*c;
    unsigned clip=a->clip|b->clip|c->clip;
    for(unsigned plane=0;plane<6;++plane)if(clip&(1u<<plane)) {
        unsigned n=counts[side],out=!side;counts[out]=0;
        unsigned axis=plane<2?2:plane<4?1:0;int positive=plane&1;
        const struct vertex *first=&poly[side][0];
        float coord=axis==0?first->x:axis==1?first->y:first->z;
        float d1=positive?add(coord,first->w):sub(first->w,coord);
        for(unsigned i=0;i<n;++i) {
            const struct vertex *a=&poly[side][i],*b=&poly[side][i+1==n?0:i+1];
            coord=axis==0?b->x:axis==1?b->y:b->z;
            float d2=positive?add(coord,b->w):sub(b->w,coord);
            int inside=lt(0.0f,d1),next=lt(0.0f,d2);
            if(inside) {if(counts[out]>=12)return 0;poly[out][counts[out]++]=*a;}
            if(inside!=next) {
                if(counts[out]>=12)return 0;
                poly[out][counts[out]++]=inside?intersect(a,b,divide(d1,sub(d1,d2))):intersect(b,a,divide(d2,sub(d2,d1)));
            }
            d1=d2;
        }
        if(counts[out]<3)return 1;
        side=out;
    }
    for(unsigned i=1;i+1<counts[side];++i)
        if(!direct(s,m,&poly[side][0],&poly[side][i],&poly[side][i+1]))return 0;
    return 1;
}
static int structural(const struct am_repair_job *j) {
    if(!j||!j->count||j->count>32||!j->command_count||j->command_count>64||j->cull>3||!j->commands)return 0;
    if(j->raw_valid?!j->raw:(!j->vertices||j->transform.lighting>1))return 0;
    const struct am_material *m=&j->material;
    if(m->flags_rgb>>19||m->alpha>255)return 0;
    if((m->flags_rgb&(1u<<16))&&(m->u_offset>524280||m->v_offset>524280||(m->u_offset&7)||(m->v_offset&7)))return 0;
    unsigned triangles=0;
    for(unsigned i=0;i<j->command_count;++i) {
        unsigned op=j->commands[i][0]>>24,n=op==5?1:op==6?2:0;
        if(!n||triangles+n>64)return 0;triangles+=n;
        for(unsigned k=0;k<n;++k) {
            uint32_t word=j->commands[i][k];
            for(unsigned shift=0;shift<24;shift+=8)if(((word>>shift)&255)/2>=j->count)return 0;
        }
    }
    return 1;
}
int am_repair_transform(const struct am_repair_job *j,uint32_t raw[32][8]) {
    if(!j||!raw||!j->vertices||!j->count||j->count>32||j->transform.lighting>1)return AM_REPAIR_BAD_INPUT;
    fp_environment saved;fp_enter(&saved);struct vertex v[32];recompute(j,v);
    for(unsigned i=0;i<j->count;++i) {
        raw[i][0]=bits(v[i].x);raw[i][1]=bits(v[i].y);raw[i][2]=bits(v[i].z);raw[i][3]=bits(v[i].w);raw[i][4]=bits(v[i].iw);
        raw[i][5]=(uint16_t)(int16_t)v[i].u|((uint32_t)(uint16_t)(int16_t)v[i].v<<16);
        raw[i][6]=v[i].rgba[0]|((uint32_t)v[i].rgba[1]<<8)|((uint32_t)v[i].rgba[2]<<16)|((uint32_t)v[i].rgba[3]<<24);
        raw[i][7]=v[i].clip;
    }
    fp_leave(saved);return AM_REPAIR_OK;
}
int am_repair(const struct am_repair_job *j,am_repair_emit emit,void *opaque,struct am_repair_result *result) {
    if(!emit||!result||!structural(j))return AM_REPAIR_BAD_INPUT;
    memset(result,0,sizeof(*result));fp_environment saved;fp_enter(&saved);
    struct vertex v[32];if(j->raw_valid)raw_vertices(j,v);else {recompute(j,v);result->recomputed_vertices=j->count;}
    struct sink s={emit,opaque,result};int ok=1;
    for(unsigned i=0;i<j->command_count&&ok;++i) {
        unsigned n=j->commands[i][0]>>24==6?2:1;
        for(unsigned k=0;k<n&&ok;++k) {
            uint32_t word=j->commands[i][k];unsigned indices[3]={((word>>16)&255)/2,((word>>8)&255)/2,(word&255)/2};
            struct vertex *a=&v[indices[0]],*b=&v[indices[1]],*c=&v[indices[2]];
            ++result->input_triangles;int r=reject(a,b,c,j->cull);
            if(r==1){++result->clip_rejected;continue;}if(r==2){++result->cull_rejected;continue;}
            if(a->clip|b->clip|c->clip) {++result->clipped_triangles;ok=clipped(&s,&j->material,a,b,c);}
            else {
                /* Preserve the frozen -ffast-math executable's two FLE
                 * rejection guards, including its unordered-W behavior. */
                int good=!le(a->w,0)&&!le(32767,a->w)&&!le(b->w,0)&&!le(32767,b->w)&&!le(c->w,0)&&!le(32767,c->w);
                int equal=le(a->w,b->w)&&le(b->w,a->w)&&le(a->w,c->w)&&le(c->w,a->w);
                ok=good&&!equal?indexed(&s,&j->material,v,indices):direct(&s,&j->material,a,b,c);
            }
        }
    }
    fp_leave(saved);return ok?AM_REPAIR_OK:AM_REPAIR_CANCELLED;
}
