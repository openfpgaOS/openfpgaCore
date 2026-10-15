#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#define SM64_PERF_SCOPE(x) ((void)0)
#define LOAD_ALIAS_ENABLED 1
#define G_FOG 0x10000u
#define G_BL_CLR_FOG 3u
struct RGBA { uint8_t r,g,b,a; };
struct LoadedVertex {float x,y,z,w,w_inv,u,v;struct RGBA color;uint8_t clip_rej;};
static struct {struct LoadedVertex loaded_vertices[64];uint32_t geometry_mode;int16_t fog_mul,fog_offset;}rsp;
static struct {uint32_t other_mode_l;}rdp;
static bool configEnableFog;
static uint32_t raw[256],load_lazy_slots,cpu_fog_pending,gfx_front_stats[16];
static unsigned load_lazy_first;
static int16_t cpu_fog_mul,cpu_fog_offset;
#define LOAD_ALIAS_VIEW raw
static unsigned release_calls;
static void gfx_gpu_queued_front_release(void){release_calls++;}
static __attribute__((noinline)) void load_alias_copy(uint32_t *restrict d,
                                                     const uint32_t *restrict s,
                                                     unsigned count) {
    for(unsigned i=0;i<count;i++,s+=8,d+=9) {
        d[0]=s[0];d[1]=s[1];d[2]=s[2];d[3]=s[3];d[4]=s[4];
        uint32_t uv=s[5];float u=(float)(int16_t)uv,v=(float)(int16_t)(uv>>16);
        memcpy(d+5,&u,4);memcpy(d+6,&v,4);d[7]=s[6];d[8]=s[7];
    }
}

static __attribute__((noinline)) void load_alias_materialize(uint32_t mask) {
    uint32_t needed = load_lazy_slots & mask;
    if (!needed) return;
    gfx_front_stats[11]++;
    while (needed) {
        unsigned first = (unsigned)__builtin_ctz(needed), count = 1;
        while (first + count < 32 && (needed & (1u << (first + count)))) ++count;
        uint32_t copied = count == 32 ? 0xffffffffu : ((1u << count) - 1u) << first;
        load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],
                        LOAD_ALIAS_VIEW + (first - load_lazy_first) * 8u, count);
        load_lazy_slots &= ~copied;
        needed &= ~copied;
        gfx_front_stats[10] += count;
    }
}

static __attribute__((noinline)) void load_alias_materialize_all(void) {
    const uint32_t needed = load_lazy_slots;
    if (!needed) return;
    const unsigned first = (unsigned)__builtin_ctz(needed);
    const unsigned count = 32u - (unsigned)__builtin_clz(needed) - first;
    load_alias_copy((uint32_t *)&rsp.loaded_vertices[first],
                    LOAD_ALIAS_VIEW + (first - load_lazy_first) * 8u, count);
    load_lazy_slots = 0;
    gfx_front_stats[10] += count;
    gfx_front_stats[11]++;
}

static inline void load_alias_materialize_triangle(unsigned a, unsigned b, unsigned c) {
    uint32_t mask = (a < 32 ? 1u << a : 0u) | (b < 32 ? 1u << b : 0u)
                  | (c < 32 ? 1u << c : 0u);
    if (load_lazy_slots & mask) load_alias_materialize_all();
}

static __attribute__((noinline)) void gfx_cpu_materialize_fog(uint32_t needed) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);
    load_alias_materialize_all();
    cpu_fog_pending &= ~needed;
    for (unsigned slot = 0; needed; ++slot, needed >>= 1) {
        if (!(needed & 1u)) continue;
        struct LoadedVertex *d = &rsp.loaded_vertices[slot];
        float wf = d->w;
        float zf = d->z;
        float winv = (wf == 0.f) ? 1.f / 0.001f : d->w_inv;
        if (winv < 0.0f) winv = 32767.0f;
        float fog_z = zf * winv * cpu_fog_mul + cpu_fog_offset;
        int fog_i = (int)fog_z;
        d->color.a = (fog_i < 0) ? 0 : (fog_i > 255) ? 255 : fog_i;
    }
}

static inline __attribute__((always_inline)) void gfx_cpu_flush_fog(uint32_t mask) {
    uint32_t needed = cpu_fog_pending & mask;
    if (needed) gfx_cpu_materialize_fog(needed);
}
static void before_load(size_t n_vertices,size_t dest_index){
    uint32_t overwritten = dest_index >= 32 ? 0 : n_vertices >= 32 - dest_index
        ? (0xffffffffu << dest_index) : ((1u << n_vertices) - 1u) << dest_index;
#if LOAD_ALIAS_ENABLED
    /* Do this for EVERY G_VTX, including software-only/ineligible LOADs.
     * Preserve old survivors before raw ownership or fog coefficients change;
     * overwritten lazy values are dead and can be dropped without reading. */
    load_alias_materialize(~overwritten);
    load_lazy_slots &= ~overwritten;
    /* All preceding runs have captured this generation in the ring. End
     * snapshot ownership before replacing any producer vertex state. */
    gfx_gpu_queued_front_release();
#endif
    gfx_cpu_flush_fog(~overwritten);
    cpu_fog_pending &= ~overwritten;
    const bool fog_active = configEnableFog && (rsp.geometry_mode & G_FOG);
    const bool defer_fog = fog_active && (rdp.other_mode_l >> 30) == G_BL_CLR_FOG
        && dest_index < 32 && n_vertices <= 32 - dest_index;
    const bool eager_fog = fog_active && !defer_fog;
    if (defer_fog) {
        cpu_fog_mul = rsp.fog_mul;
        cpu_fog_offset = rsp.fog_offset;
        cpu_fog_pending = overwritten;
    }
(void)eager_fog;
}
static struct LoadedVertex eager[64];
static unsigned checks,loads,materializations;static uint32_t seed=0x54465031;
static uint32_t rnd(void){seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static void require(bool yes){checks++;if(!yes){fprintf(stderr,"mismatch check%u load%u pending%08x fog%08x\n",checks,loads,load_lazy_slots,cpu_fog_pending);abort();}}
static uint32_t bits(float f){uint32_t v;memcpy(&v,&f,4);return v;}
static void reference_fog(struct LoadedVertex*d,int16_t m,int16_t o){float inv=d->w==0?1.f/0.001f:d->w_inv;if(inv<0)inv=32767.f;float z=d->z*inv*m+o;int i=(int)z;d->color.a=i<0?0:i>255?255:i;}
static void compare_slot(unsigned s){require(memcmp(&rsp.loaded_vertices[s],&eager[s],sizeof eager[s])==0);}
static void contiguous(void){uint32_t m=load_lazy_slots;if(m){unsigned lo=__builtin_ctz(m),hi=32u-__builtin_clz(m),n=hi-lo;require(m==(n==32?~0u:((1u<<n)-1u)<<lo));}}
static void consume(uint32_t mask){contiguous();if(load_lazy_slots&mask)load_alias_materialize_all();gfx_cpu_flush_fog(mask);for(unsigned i=0;i<32;i++)if(mask>>i&1)compare_slot(i);materializations++;}
int main(void){
 require(sizeof(struct LoadedVertex)==36);
 memset(rsp.loaded_vertices,0,sizeof rsp.loaded_vertices);memset(eager,0,sizeof eager);
 for(unsigned it=0;it<8000;it++){
  unsigned first=it%17==0?0:rnd()%32,count=it%17==0?32:1+rnd()%(32-first);
  bool hardware=it%5!=0,lazy=hardware&&it%7!=0;
  configEnableFog=it%3!=0;rsp.geometry_mode=configEnableFog?G_FOG:0;rdp.other_mode_l=G_BL_CLR_FOG<<30;
  rsp.fog_mul=(int16_t)(rnd()%129-64);rsp.fog_offset=(int16_t)(rnd()%512-128);
  uint32_t overwritten=count==32?~0u:((1u<<count)-1)<<first;
  // Actual pre-load block materializes old survivors before fog coefficients
  // or raw-bank contents change; dead overwritten vertices need no reads.
  before_load(count,first);require(!load_lazy_slots);for(unsigned s=0;s<32;s++)if(!(overwritten>>s&1))compare_slot(s);
  memset(raw,0xa7,sizeof raw);
  for(unsigned j=0;j<count;j++){
   unsigned s=first+j;float x=(int32_t)(rnd()%8192)-4096,y=(int32_t)(rnd()%8192)-4096;
   float w=1.f+(rnd()%1024),z=(int32_t)(rnd()%256)-128,inv=1.f/w;
   uint32_t uv=rnd(),color=rnd();uint8_t clip=rnd()%64;
   raw[8*j]=bits(x);raw[8*j+1]=bits(y);raw[8*j+2]=bits(z);raw[8*j+3]=bits(w);raw[8*j+4]=bits(inv);raw[8*j+5]=uv;raw[8*j+6]=color;raw[8*j+7]=clip;
   struct LoadedVertex e;memset(&e,0,sizeof e);e.x=x;e.y=y;e.z=z;e.w=w;e.w_inv=inv;e.u=(float)(int16_t)uv;e.v=(float)(int16_t)(uv>>16);memcpy(&e.color,&color,4);e.clip_rej=clip;
   eager[s]=e;if(configEnableFog)reference_fog(&eager[s],rsp.fog_mul,rsp.fog_offset);
   if(!lazy)rsp.loaded_vertices[s]=e;else memset(&rsp.loaded_vertices[s],0x5c,sizeof e);
  }
  if(lazy){load_lazy_first=first;load_lazy_slots=overwritten;}
  loads++;
  // Irrelevant new transform/lighting state cannot affect a recovered row.
  for(unsigned trial=0;trial<3;trial++){
   unsigned a=rnd()%64,b=rnd()%64,c=rnd()%64;
   uint32_t mask=(a<32?1u<<a:0)|(b<32?1u<<b:0)|(c<32?1u<<c:0);
   contiguous();load_alias_materialize_triangle(a,b,c);gfx_cpu_flush_fog(mask);
   for(unsigned s=0;s<32;s++)if(mask>>s&1)compare_slot(s);
  }
  if(it%11==0)consume(~0u);else if(it%7==0)consume(rnd());
  // Once fog alpha is produced, a repeated materialization must preserve it.
  if(it%5==0){uint32_t mask=rnd();consume(mask);consume(mask);}
 }
 consume(~0u);require(!load_lazy_slots&&!cpu_fog_pending);
 printf("{\"pass\":true,\"loads\":%u,\"checks\":%u,\"explicit_consumes\":%u,\"materialized_slots\":%u,\"copy_calls\":%u}\n",loads,checks,materializations,gfx_front_stats[10],gfx_front_stats[11]);return 0;
}
