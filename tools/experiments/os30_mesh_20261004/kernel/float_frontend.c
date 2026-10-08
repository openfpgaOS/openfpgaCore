#include "float_frontend.h"
#include <math.h>
#include <string.h>
#define G_LIGHTING 0x20000u
#define G_TEXTURE_GEN 0x40000u
#define G_FOG 0x10000u
#define G_CULL_FRONT 0x1000u
#define G_CULL_BACK 0x2000u
#define G_CULL_BOTH (G_CULL_FRONT|G_CULL_BACK)
static uint8_t clamp_color(int c) {return c<0?0:c>255?255:c;}
void mf_transform(const struct MFShader *s,const struct MFSource *input,unsigned count,struct MFVertex *output) {
    const float m00=s->matrix[0][0],m01=s->matrix[0][1],m02=s->matrix[0][2],m03=s->matrix[0][3];
    const float m10=s->matrix[1][0],m11=s->matrix[1][1],m12=s->matrix[1][2],m13=s->matrix[1][3];
    const float m20=s->matrix[2][0],m21=s->matrix[2][1],m22=s->matrix[2][2],m23=s->matrix[2][3];
    const float m30=s->matrix[3][0],m31=s->matrix[3][1],m32=s->matrix[3][2],m33=s->matrix[3][3];
    for(unsigned i=0;i<count;i++) {
        const struct MFSource *v=input+i;struct MFVertex *d=output+i;
        const float ox=v->position[0],oy=v->position[1],oz=v->position[2];
        float x=(ox*m00+oy*m10+oz*m20+m30)*s->aspect;
        float y=ox*m01+oy*m11+oz*m21+m31;
        float z=ox*m02+oy*m12+oz*m22+m32;
        float w=ox*m03+oy*m13+oz*m23+m33;
        int16_t u=v->uv[0]*s->scale_s>>16,t=v->uv[1]*s->scale_t>>16;
        if(s->geometry_mode&G_LIGHTING) {
            int r=s->light[s->lights-1][0],g=s->light[s->lights-1][1],b=s->light[s->lights-1][2];
            for(unsigned l=0;l<s->lights-1;l++) {
                float intensity=0;
                intensity+=(int8_t)v->color[0]*s->light_coeff[l][0];
                intensity+=(int8_t)v->color[1]*s->light_coeff[l][1];
                intensity+=(int8_t)v->color[2]*s->light_coeff[l][2];
                intensity/=127.0f;
                if(intensity>0.0f) {
                    r+=intensity*s->light[l][0];g+=intensity*s->light[l][1];b+=intensity*s->light[l][2];
                }
            }
            d->color[0]=clamp_color(r);d->color[1]=clamp_color(g);d->color[2]=clamp_color(b);
            if(s->geometry_mode&G_TEXTURE_GEN) {
                float dotx=0,doty=0;
                dotx+=(int8_t)v->color[0]*s->lookat[0][0];
                dotx+=(int8_t)v->color[1]*s->lookat[0][1];
                dotx+=(int8_t)v->color[2]*s->lookat[0][2];
                doty+=(int8_t)v->color[0]*s->lookat[1][0];
                doty+=(int8_t)v->color[1]*s->lookat[1][1];
                doty+=(int8_t)v->color[2]*s->lookat[1][2];
                u=(int32_t)((dotx/127.0f+1.0f)/4.0f*s->scale_s);
                t=(int32_t)((doty/127.0f+1.0f)/4.0f*s->scale_t);
            }
        } else memcpy(d->color,v->color,3);
        d->u=u;d->v=t;d->clip=0;
        if(x < -w)d->clip|=32;if(x > w)d->clip|=16;
        if(y < -w)d->clip|=8;if(y > w)d->clip|=4;
        if(z < -w)d->clip|=2;if(z > w)d->clip|=1;
        d->x=x;d->y=y;d->z=z;d->w=w;d->wi=1.0f/w;d->depth=0;d->exact_depth=0;
        if(s->fixed) {
            int16_t pos[3]={(int16_t)ox,(int16_t)oy,(int16_t)oz};int32_t q[3];
            for(unsigned row=0;row<3;row++) {
                int64_t sum=0;for(unsigned k=0;k<3;k++)sum+=(int64_t)s->fixed_matrix[row*4+k]*pos[k];
                q[row]=(int32_t)((sum>>16)+s->fixed_matrix[row*4+3]);
            }
            d->x=q[0]*(1.0f/256.0f);d->y=q[1]*(1.0f/256.0f);d->w=q[2]*(1.0f/256.0f);d->wi=1.0f/d->w;
            d->depth=(uint32_t)((UINT64_C(1)<<38)/(uint32_t)q[2]);d->exact_depth=1;d->clip=0;
            /* Original geometry attributes use one diffuse light and a
             * separately accumulated sum; preserve their exact expression. */
            if(s->geometry_mode&G_LIGHTING) {
                float intensity=s->lights>1?((int8_t)v->color[0]*s->light_coeff[0][0]
                    +(int8_t)v->color[1]*s->light_coeff[0][1]
                    +(int8_t)v->color[2]*s->light_coeff[0][2])/127.0f:0.0f;
                if(intensity<0.0f)intensity=0.0f;
                for(unsigned k=0;k<3;k++) {
                    int channel=s->light[s->lights-1][k];channel+=intensity*s->light[0][k];
                    d->color[k]=channel>255?255:channel;
                }
            }
        }
        if(s->fog_enabled&&(s->geometry_mode&G_FOG)) {
            float wi=w==0.0f?1.0f/0.001f:1.0f/w;
            if(wi<0.0f)wi=32767.0f;
            d->color[3]=clamp_color((int)(z*wi*s->fog_mul+s->fog_offset));
        } else d->color[3]=v->color[3];
    }
}
int mf_cull(const struct MFVertex v[3],uint32_t mode) {
    if(v[0].clip&v[1].clip&v[2].clip)return 1;
    if(mode&G_CULL_BOTH) {
        float dx1=v[0].x*v[0].wi-v[1].x*v[1].wi;
        float dy1=v[0].y*v[0].wi-v[1].y*v[1].wi;
        float dx2=v[2].x*v[2].wi-v[1].x*v[1].wi;
        float dy2=v[2].y*v[2].wi-v[1].y*v[1].wi;
        float cross=dx1*dy2-dy1*dx2;
        if((v[0].w<0)^(v[1].w<0)^(v[2].w<0))cross=-cross;
        switch(mode&G_CULL_BOTH) {
            case G_CULL_FRONT:return cross<=0.0f;
            case G_CULL_BACK:return cross>=0.0f;
            default:return 1;
        }
    }
    return 0;
}
static struct MFVertex interpolate(const struct MFVertex *a,const struct MFVertex *b,float t) {
    struct MFVertex d;
    d.x=a->x+t*(b->x-a->x);d.y=a->y+t*(b->y-a->y);
    d.z=a->z+t*(b->z-a->z);d.w=a->w+t*(b->w-a->w);
    d.u=a->u+t*(b->u-a->u);d.v=a->v+t*(b->v-a->v);
    for(unsigned k=0;k<4;k++)d.color[k]=a->color[k]+(int)(t*(float)(b->color[k]-a->color[k]));
    d.wi=1.0f/d.w;d.clip=0;d.depth=0;d.exact_depth=0;return d;
}
unsigned mf_clip(const struct MFVertex v[3],struct MFVertex result[12]) {
    if(v[0].clip&v[1].clip&v[2].clip)return 0;
    unsigned mask=v[0].clip|v[1].clip|v[2].clip;
    if(!mask) {memcpy(result,v,3*sizeof(*v));return 3;}
    static const int8_t planes[6][4]={{0,0,-1,1},{0,0,1,1},{0,-1,0,1},{0,1,0,1},{-1,0,0,1},{1,0,0,1}};
    struct MFVertex buf[2][12];memcpy(buf[0],v,3*sizeof(*v));unsigned input=0,count=3;
    for(unsigned plane=0;plane<6;plane++)if(mask&(1u<<plane)) {
        unsigned outcount=0;const int8_t *p=planes[plane];
        for(unsigned i=0;i<count;i++) {
            const struct MFVertex *a=buf[input]+i,*b=buf[input]+(i+1)%count;
            float da=p[0]*a->x+p[1]*a->y+p[2]*a->z+p[3]*a->w;
            float db=p[0]*b->x+p[1]*b->y+p[2]*b->z+p[3]*b->w;
            int ia=da>0,ib=db>0;
            if(ia)buf[!input][outcount++]=*a;
            if(ia^ib)buf[!input][outcount++]=ia?interpolate(a,b,da/(da-db)):interpolate(b,a,db/(db-da));
        }
        if(outcount<3)return 0;
        input=!input;count=outcount;
    }
    memcpy(result,buf[input],count*sizeof(*v));
    /* Even unchanged vertices in a clipped fan live outside the RSP cache. */
    for(unsigned i=0;i<count;i++){result[i].depth=0;result[i].exact_depth=0;result[i].clip=0;}
    return count;
}
static uint32_t reciprocal(uint32_t den) {
    uint32_t q=UINT32_MAX/den;return q+(UINT32_MAX-q*den==den-1u);
}
void mf_project(const struct MFVertex v[3],const struct MFMaterial *m,unsigned cached,uint32_t out[24]) {
    float uv[3][2],zscale=64.0f;
    for(unsigned i=0;i<3;i++) {
        uv[i][0]=m->textured?(v[i].u-m->uls*8)/32.0f+(m->bias?0.5f:0.0f):0.0f;
        uv[i][1]=m->textured?(v[i].v-m->ult*8)/32.0f+(m->bias?0.5f:0.0f):0.0f;
    }
    if(!cached&&m->textured) {
        float largest=0.0f;
        for(unsigned i=0;i<3;i++) {
            float a=fabsf(v[i].wi),b=fabsf(uv[i][0]*v[i].wi),c=fabsf(uv[i][1]*v[i].wi);
            if(a>largest)largest=a;if(b>largest)largest=b;if(c>largest)largest=c;
        }
        if(largest>0.0f)zscale=8192.0f/largest;
        if(zscale<1.0f)zscale=1.0f;
    }
    out[0]=1u|(cached?2u:0u);out[1]=m->clip_x;out[2]=m->clip_y;
    for(unsigned i=0;i<3;i++) {
        uint32_t *o=out+3+7*i;
        int32_t xq=lrintf(v[i].x*256.0f),yq=lrintf(v[i].y*256.0f),wq=lrintf(v[i].w*256.0f);
        uint32_t zi=reciprocal(wq<256?256u:(uint32_t)wq);
        int32_t rx=(int32_t)((int64_t)xq*zi>>16),ry=(int32_t)((int64_t)yq*zi>>16);
        int64_t sx=(int32_t)lrintf(m->cx)*16LL+((int64_t)(int32_t)lrintf(m->hw)*rx>>12);
        int64_t sy=(int32_t)lrintf(m->cy*16.0f)-((int64_t)(int32_t)lrintf(m->hh*16.0f)*ry>>16);
        int16_t x=sx<-32768?-32768:sx>32767?32767:sx,y=sy<-32768?-32768:sy>32767?32767:sy;
        o[0]=(uint16_t)x|((uint32_t)(uint16_t)y<<16);
        o[1]=(int32_t)lrintf(uv[i][0]*65536.0f);o[2]=(int32_t)lrintf(uv[i][1]*65536.0f);
        if(!cached) {int32_t z=lrintf(v[i].wi*(65536.0f*zscale));zi=z<1?1:z;}
        o[3]=zi;
        float depth=v[i].wi*1073741824.0f;
        if(depth<1.0f)depth=1.0f;else if(depth>2147483520.0f)depth=2147483520.0f;
        o[4]=v[i].exact_depth?v[i].depth:(uint32_t)lrintf(depth);
        o[5]=m->constant_rgb?m->rgb:((uint32_t)(v[i].color[0]>>3)<<11)|((uint32_t)(v[i].color[1]>>2)<<5)|(v[i].color[2]>>3);
        o[6]=0;
    }
}
