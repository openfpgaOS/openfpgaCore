/* Standalone worker cost probe. Exact private 0x61 arithmetic, not a renderer.
 * Input: job count, then 24 projected words/job. Output: length, packet words.
 * Host and RV32 execute the same implementation; no reference answers in ROM. */
#include <stdint.h>
#ifdef HOST
#include <stdio.h>
#include <stdlib.h>
static uint32_t input(void) { uint32_t v;if(fread(&v,4,1,stdin)!=1)exit(2);return v; }
static void output(uint32_t v) { if(fwrite(&v,4,1,stdout)!=1)exit(3); }
#else
static uint32_t input(void) { return *(volatile uint32_t *)0x20010; }
static void output(uint32_t v) { *(volatile uint32_t *)0x20004=v; }
#endif

struct Vertex { int32_t x,y,s,t,zi,depth;uint32_t rgb,add; };
static int32_t sat32(int64_t v) {
    return v>INT32_MAX?INT32_MAX:v<INT32_MIN?INT32_MIN:(int32_t)v;
}
static int32_t gradient(int64_t n,uint32_t reciprocal,unsigned scale,int sign) {
    /* Exact floor(n*r/2^scale) without an unavailable RV32 128-bit type.
     * Projected signed16 coordinates bound the high product below 2^60. */
    int64_t v=(n>>24)*(int64_t)reciprocal
             +(int64_t)(((uint64_t)n&0xffffff)*reciprocal>>24);
    return sat32((v>>(scale-24))*sign);
}
static int64_t slope(struct Vertex a,struct Vertex b,unsigned subpixel) {
    int32_t dx=b.x-a.x;uint32_t dy=(uint32_t)(b.y-a.y);
    if(!dy)return 0;
    uint32_t magnitude=dx<0?(uint32_t)-dx:(uint32_t)dx;
    uint32_t q=(magnitude<<(subpixel?16:12))/dy;
    return dx<0?-(int64_t)q:(int64_t)q;
}
static int32_t distance(int32_t scan,int32_t y,unsigned subpixel) {
    int32_t d=subpixel?scan*16+8-y:scan-y;
    /* The raster edge unit uses a signed17 prestep distance. */
    return (int32_t)((uint32_t)d<<15)>>15;
}
static unsigned packet(const uint32_t in[24],uint32_t out[40]) {
    struct Vertex v[3];unsigned sub=in[0]&1,normalize=in[0]&2,combine=in[0]&4;
    int32_t cy0=(int16_t)in[2],cy1=(int16_t)(in[2]>>16);
    for(unsigned i=0;i<3;i++) {
        const uint32_t *p=in+3+7*i;
        v[i]=(struct Vertex){(int16_t)p[0],(int16_t)(p[0]>>16),
            (int32_t)p[1],(int32_t)p[2],(int32_t)p[3],(int32_t)p[4],p[5],p[6]};
    }
    for(unsigned i=0;i<3;i++) {
        unsigned a=i==1?1:0,b=a+1;
        if(v[b].y<v[a].y) {struct Vertex t=v[a];v[a]=v[b];v[b]=t;}
    }
    int32_t start=sub?(v[0].y+7)>>4:v[0].y;
    int32_t middle=sub?(v[1].y+7)>>4:v[1].y;
    int32_t end=sub?(v[2].y+7)>>4:v[2].y;
    if(start<cy0)start=cy0;if(end>cy1)end=cy1;
    if(start>=end)return 0;
    int32_t dx1=v[1].x-v[0].x,dx2=v[2].x-v[0].x;
    int32_t dy1=v[1].y-v[0].y,dy2=v[2].y-v[0].y;
    int64_t det=(int64_t)dx1*dy2-(int64_t)dx2*dy1;
    unsigned left=det>0,bottom=start>=middle;
    int64_t slopes[3]={slope(v[0],v[2],sub),slope(v[0],v[1],sub),slope(v[1],v[2],sub)};
    unsigned sv=bottom?1:0,shift=sub?4:0;
    int64_t xl=(int64_t)v[0].x*4096+(slopes[0]*distance(start,v[0].y,sub)>>shift);
    int64_t xs=(int64_t)v[sv].x*4096+(slopes[bottom?2:1]*distance(start,v[sv].y,sub)>>shift);
    unsigned planes=combine?10:7;
    out[0]=0x61000000u|(9+3*planes);out[1]=left?xl:xs;out[2]=left?xs:xl;
    for(unsigned i=0;i<3;i++)out[3+i]=(uint32_t)slopes[i];
    out[6]=(uint32_t)((int64_t)v[1].x*4096+
        (sub&&!bottom?(slopes[2]*distance(middle,v[1].y,1)>>4):0));
    out[7]=((uint32_t)(uint16_t)middle<<16)|(uint16_t)start;
    out[8]=(sub<<18)|(left<<17)|(bottom<<16)|(uint16_t)end;out[9]=in[1];
    int32_t attr[3][10];
    for(unsigned i=0;i<3;i++) {
        attr[i][0]=(int32_t)((int64_t)v[i].s*v[i].zi>>16);
        attr[i][1]=(int32_t)((int64_t)v[i].t*v[i].zi>>16);attr[i][2]=v[i].zi;
        attr[i][3]=((v[i].rgb>>5)&63)*65536;attr[i][4]=((v[i].rgb>>11)&31)*65536;
        attr[i][5]=(v[i].rgb&31)*65536;attr[i][6]=v[i].depth;
        attr[i][7]=((v[i].add>>11)&31)*65536;attr[i][8]=((v[i].add>>5)&63)*65536;
        attr[i][9]=(v[i].add&31)*65536;
    }
    if(normalize)for(unsigned step=0;step<16;step++) {
        unsigned stop=0;
        for(unsigned i=0;i<3;i++)for(unsigned k=0;k<3;k++)
            stop|=attr[i][k]<-268435456||attr[i][k]>=268435456;
        if(stop)break;
        for(unsigned i=0;i<3;i++)for(unsigned k=0;k<3;k++)attr[i][k]=(uint32_t)attr[i][k]*2u;
    }
    uint64_t magnitude=det<0?(uint64_t)-det:(uint64_t)det;
    if(!magnitude)magnitude=1;
    unsigned scale=sub&&magnitude<=8192?31:44;
    uint64_t r=((UINT64_C(1)<<scale)+(magnitude>>1))/magnitude;
    uint32_t reciprocal=r>INT32_MAX?INT32_MAX:(uint32_t)r;
    int sign=det<0?-1:1;
    for(unsigned k=0;k<planes;k++) {
        int32_t da1=sat32((int64_t)attr[1][k]-attr[0][k]);
        int32_t da2=sat32((int64_t)attr[2][k]-attr[0][k]);
        int64_t nx=((int64_t)da1*dy2-(int64_t)da2*dy1)*16;
        int64_t ny=((int64_t)da2*dx1-(int64_t)da1*dx2)*(sub?16:1);
        int32_t du=gradient(nx,reciprocal,scale,sign),dv=gradient(ny,reciprocal,scale,sign);
        int32_t origin=(int32_t)((int64_t)attr[0][k]-((int64_t)du*v[0].x>>4)
                -((int64_t)dv*v[0].y>>shift)+(du>>1)+(dv>>1));
        if(k!=0&&k!=1&&k!=2&&k!=6) {
            origin=(int32_t)((uint32_t)origin<<8)>>8;
            du=(int32_t)((uint32_t)du<<8)>>8;dv=(int32_t)((uint32_t)dv<<8)>>8;
        }
        out[10+3*k]=origin;out[11+3*k]=du;out[12+3*k]=dv;
    }
    return 10+3*planes;
}
int main(void) {
    uint32_t in[24],out[40];unsigned jobs=input();
    for(unsigned j=0;j<jobs;j++) {
        for(unsigned i=0;i<24;i++)in[i]=input();
        unsigned n=packet(in,out);output(n);
        for(unsigned i=0;i<n;i++)output(out[i]);
    }
#ifndef HOST
    *(volatile uint32_t *)0x20000=1;
    for(;;) {}
#else
    return 0;
#endif
}
