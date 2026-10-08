/* Extracted production old/new traversal plus actual BSP loader and helpers. */
#include "src/client/refresh/gl1/header/local.h"
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>

enum { NODES=31, LEAVES=32, SURFS=62, IMAGES=8 };
static model_t world;
model_t *r_worldmodel=&world;
static cvar_t cull;
cvar_t *r_cull=&cull;
int r_visframecount=11,r_framecount=27;
cplane_t frustum[4];
refdef_t r_newrefdef;
glconfig_t gl_config;
static vec3_t modelorg;
msurface_t *r_alpha_surfaces;
gllightmapstate_t gl_lms;
static mleaf_t leaves[LEAVES];
static msurface_t surfaces[SURFS],*marks[LEAVES][4];
static mtexinfo_t texinfo[SURFS];
static image_t images[IMAGES];
static dnode_t disk[NODES];
static cplane_t bsp_planes[NODES];
static byte areas[4];
static int trace[16384],trace_count,errors,cases,edges,unsafe_edges;
static uint32_t seed=0x34529618,checksum;
static unsigned rnd(void) {seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static int sid(msurface_t *s) {return s ? (int)(s-surfaces)+1 : 0;}
static void emit(int n) {assert(trace_count<(int)(sizeof(trace)/sizeof(trace[0])));trace[trace_count++]=n;}
void R_AddSkySurface(msurface_t *s) {emit(1000+sid(s));}
void *Hunk_Alloc(int bytes) {void *p=calloc(1,bytes);assert(p);return p;}
void Com_Error(int code,const char *fmt,...) {va_list a;va_start(a,fmt);vfprintf(stderr,fmt,a);va_end(a);abort();}
void Com_Printf(const char *fmt,...) {(void)fmt;}
#include "plane_functions.h"

static void bounds(int i,float *b)
{
    if(i>=0) for(int j=0;j<3;j++){b[j]=disk[i].mins[j];b[j+3]=disk[i].maxs[j];}
    else memcpy(b,leaves[-1-i].minmaxs,6*sizeof(float));
}
static void tree(void)
{
    memset(disk,0,sizeof(disk));memset(leaves,0,sizeof(leaves));
    for(int j=0;j<3;j++){disk[0].mins[j]=-1024;disk[0].maxs[j]=1024;}
    for(int i=0;i<NODES;i++) {
        disk[i].planenum=i;disk[i].firstface=2*i;disk[i].numfaces=2;
        bsp_planes[i]=(cplane_t){{0}};bsp_planes[i].type=i%4;
        bsp_planes[i].normal[i%3]=1;bsp_planes[i].dist=(disk[i].mins[i%3]+disk[i].maxs[i%3])*0.5f;
        int axis=i%3,mid=(disk[i].mins[axis]+disk[i].maxs[axis])/2;
        for(int side=0;side<2;side++) {
            int q=2*i+1+side,child=q<NODES?q:-1-(q-NODES);
            disk[i].children[side]=child;
            for(int j=0;j<3;j++) {
                int lo=disk[i].mins[j],hi=disk[i].maxs[j];
                if(j==axis){if(side)lo=mid;else hi=mid;}
                if(child>=0){disk[child].mins[j]=lo;disk[child].maxs[j]=hi;}
                else {leaves[-1-child].minmaxs[j]=lo;leaves[-1-child].minmaxs[j+3]=hi;}
            }
        }
    }
    /* Real maps contain some bounds that are not contained in the parent. */
    for(int i=1;i<NODES;i++) if((rnd()&15)==0) disk[i].maxs[rnd()%3]+=2048;
    for(int i=0;i<LEAVES;i++) {
        if((rnd()&15)==0)leaves[i].minmaxs[rnd()%3]-=2048;
        leaves[i].contents=(rnd()%8==0)?CONTENTS_SOLID:0;
        leaves[i].area=i%16;leaves[i].firstmarksurface=marks[i];leaves[i].nummarksurfaces=4;
        for(int j=0;j<4;j++)marks[i][j]=&surfaces[rnd()%SURFS];
    }
    lump_t lump={.fileofs=0,.filelen=sizeof(disk)};
    Mod_LoadNodes("mask-test",bsp_planes,NODES,leaves,LEAVES,&world.nodes,&world.numnodes,(byte*)disk,&lump);
    world.leafs=leaves;world.numleafs=LEAVES;world.surfaces=surfaces;
    for(int i=0;i<NODES;i++)for(int c=0;c<2;c++) {
        float p[6],b[6];bounds(i,p);bounds(disk[i].children[c],b);int good=1;
        for(int j=0;j<3;j++)if(p[j]>p[j+3]||b[j]>b[j+3]||b[j]<p[j]||b[j+3]>p[j+3])good=0;
        assert(!!(world.nodes[i].clip_children&(1u<<c))==good);edges++;unsafe_edges+=!good;
    }
    for(int i=0;i<SURFS;i++) {
        texinfo[i]=(mtexinfo_t){0};texinfo[i].image=&images[i%IMAGES];texinfo[i].numframes=1;
        switch(rnd()%8){case 0:texinfo[i].flags=SURF_SKY;break;case 1:texinfo[i].flags=SURF_TRANS33;break;
          case 2:texinfo[i].flags=SURF_TRANS66;break;case 3:texinfo[i].flags=SURF_WARP;break;}
        surfaces[i]=(msurface_t){0};surfaces[i].texinfo=&texinfo[i];surfaces[i].flags=(i&1)?SURF_PLANEBACK:0;
        surfaces[i].lightmaptexturenum=i%16;
    }
}
static float frombits(uint32_t bits) {float v;memcpy(&v,&bits,4);return v;}
static void setup_frame(int f)
{
    cull.value=f!=3;gl_config.multitexture=f&1;
    for(int j=0;j<3;j++)modelorg[j]=(int)(rnd()%8193)-4096;
    for(int j=0;j<4;j++)areas[j]=(byte)rnd();r_newrefdef.areabits=(f&1)?areas:NULL;
    for(int i=0;i<NODES;i++)world.nodes[i].visframe=(f>=2&&(rnd()&7)==0)?10:11;
    for(int i=0;i<LEAVES;i++)leaves[i].visframe=(f>=2&&(rnd()&7)==0)?10:11;
    for(int i=0;i<4;i++) {
        frustum[i]=(cplane_t){{0}};frustum[i].type=3;
        for(int j=0;j<3;j++) {
            frustum[i].normal[j]=((int)(rnd()%2049)-1024)*(1.0f/1024);
            if(f==0)frustum[i].normal[j]=(i%3==j)?1:0;
            if(frustum[i].normal[j]<0)frustum[i].signbits|=1u<<j;
        }
        frustum[i].dist=f==0?-8192:(int)(rnd()%4097)-2048;
        if(f==1) {
            mnode_t *n=&world.nodes[rnd()%NODES];
            vec3_t corner;for(int j=0;j<3;j++)corner[j]=n->minmaxs[j+((rnd()&1)?3:0)];
            float d=DotProduct(frustum[i].normal,corner);uint32_t b;memcpy(&b,&d,4);
            /* Exact representable boundary and its neighboring floats. */
            if((b&0x7fffffff)>1)b+=(int)(rnd()%3)-1;
            frustum[i].dist=frombits(b);
            if((rnd()&3)==0){frustum[i].type=rnd()%3;frustum[i].dist=n->minmaxs[frustum[i].type+((rnd()&1)?3:0)];}
        }
    }
    if(f==5) {
        switch(rnd()%5) {case 0:frustum[0].dist=frombits(0x7fc01234);break;
          case 1:frustum[0].normal[0]=frombits(0x7f800000);break;
          case 2:frustum[0].normal[1]=frombits(0x7f800001);break;
          case 3:frustum[0].signbits^=1;break;
          case 4:frustum[0].normal[0]=frombits(0x7f000000);break;}
        assert(R_WorldPlaneMask()==-1);
    }
}
static void reset_draw(void)
{
    trace_count=0;r_alpha_surfaces=NULL;memset(gl_lms.lightmap_surfaces,0,sizeof(gl_lms.lightmap_surfaces));
    for(int i=0;i<IMAGES;i++)images[i].texturechain=NULL;
    for(int i=0;i<SURFS;i++){surfaces[i].visframe=0;surfaces[i].texturechain=NULL;surfaces[i].lightmapchain=NULL;}
}
static void capture(void)
{
    emit(2000);for(int i=0;i<SURFS;i++){emit(surfaces[i].visframe);emit(sid(surfaces[i].texturechain));emit(sid(surfaces[i].lightmapchain));}
    emit(sid(r_alpha_surfaces));for(int i=0;i<IMAGES;i++)emit(sid(images[i].texturechain));
    for(int i=0;i<16;i++)emit(sid(gl_lms.lightmap_surfaces[i]));
}
int main(void)
{
    entity_t ent={0};static int expected[16384];
    Swap_Init();
    for(int mode=0;mode<5;mode++) {
#if defined(__riscv)
        __asm__ volatile("csrw frm,%0"::"r"(mode):"memory");
#endif
        for(int trial=0;trial<1000;trial++) {
            tree();
            for(int frame=0;frame<6;frame++) {
                setup_frame(frame);reset_draw();original_traversal(&ent,world.nodes);capture();
                int n=trace_count;memcpy(expected,trace,n*sizeof(int));
                reset_draw();candidate_traversal(&ent,world.nodes,R_WorldPlaneMask());capture();cases++;
                if(n!=trace_count||memcmp(expected,trace,n*sizeof(int))) {
                    if(errors<8)printf("mismatch mode=%d trial=%d frame=%d n=%d got=%d\n",mode,trial,frame,n,trace_count);errors++;
                }
                for(int j=0;j<trace_count;j++)checksum=checksum*33+(unsigned)trace[j];
            }
            free(world.nodes);world.nodes=NULL;
        }
    }
#if defined(__riscv)
    __asm__ volatile("csrw frm,zero":::"memory");
#endif
    printf("PLANE_TEST cases=%d edges=%d unsafe=%d errors=%d checksum=%08x\n",cases,edges,unsafe_edges,errors,checksum);
    return errors?1:0;
}
