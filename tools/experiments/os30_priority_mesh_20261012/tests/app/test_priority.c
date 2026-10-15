
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <setjmp.h>
#include <math.h>
#include "async_mesh_repair.h"
static unsigned checks;
#define C(x) do{++checks;assert(x);}while(0)
#define SM64_PERF_SCOPE(x) ((void)0)
struct Vertex {uint32_t word[9];};
static struct {struct Vertex loaded_vertices[32];} rsp;
static uint32_t raw[32][8];
#define LOAD_ALIAS_VIEW ((const uint32_t *)raw)
static uint32_t load_lazy_slots,load_lazy_first,load_alias_transaction;
static uint32_t gfx_front_stats[16],gfx_async_stats[16];
static uint32_t async_sequence,async_ticket,async_epoch,async_epoch_valid;
static uint32_t async_servicing,async_yielding,async_finished_repair_valid;
static unsigned async_legacy_held,async_legacy_quiescent,sm64_async_front_capability;
static uint32_t hw_epoch,hw_completed,hw_ticket,hw_grant_ticket,hw_target;
static unsigned hw_lease,hw_pending,hw_fault,grant_delay,raw_delay;
static unsigned reads,writes,releases,acquires,fences,services,repair_services,recursion;
static unsigned trap_count,commands,commit_count,reserve_count,kicks,forced,staged,published;
static unsigned force_reserve_wait;
static uint32_t packet[8192],wire[8192];
static jmp_buf jumped;
static void test_trap(void){++trap_count;longjmp(jumped,1);}
static void gpu_async_fault(void){test_trap();}
static void host_fence(void){++fences;}
static uint32_t read_mmio(unsigned r){
 ++reads;
 if(hw_pending&&grant_delay&&!--grant_delay){hw_pending=0;hw_lease=1;hw_grant_ticket=hw_ticket;}

 if(r==0x2e)return hw_epoch;
 if(r==0x23)return hw_completed;
 if(r==0x21)return(hw_lease?16u:0u)|(hw_pending?8u:0u)|(hw_fault?4u:0u);
 if(r==0x2f)return hw_lease?hw_grant_ticket:0;
 abort();
}
static void write_mmio(unsigned r,uint32_t v){
 ++writes;
 if(r==0x2b){hw_target=v;return;}
 if(r==0x2c){hw_ticket=v;return;}
 if(r==0x2d&&v==1){C(!hw_lease&&!hw_pending);hw_pending=1;++acquires;if(!grant_delay)grant_delay=4;return;}
 if(r==0x2d&&v==2){C(hw_lease);C(hw_grant_ticket==async_ticket);C(!raw_delay);hw_lease=0;++releases;return;}
 abort();
}
static uint32_t raw_status(void){if(raw_delay){--raw_delay;return 1;}return 6;}
void gfx_gpu_async_raw_release(void);
void gfx_gpu_async_before_wait(void);
void gfx_gpu_async_raw_release_if_pending(void);
void gfx_async_materialize_raw(void);
void gfx_gpu_wait_service(void);
static void gfx_gpu_queued_front_service(void){++services;}
static void gfx_gpu_async_service(void){++repair_services;if(recursion){recursion=0;gfx_gpu_wait_service();}}
static void of_gpu_kick_now(void){++forced;memcpy(wire+published,packet,staged*4);published+=staged;staged=0;}
static void of_gpu_kick(void){++kicks;if(staged>=512)of_gpu_kick_now();}
static void _gpu_stream_reserve_words(unsigned n){
 C(n==42);++reserve_count;
 if(force_reserve_wait){unsigned old=async_sequence;gfx_gpu_wait_service();C(async_sequence==old);force_reserve_wait=0;}
 C(staged+n<8192);
}
static uint32_t *_gpu_ring_claim(void){return packet+staged;}
static void _gpu_ring_commit(unsigned n){C(n==42);C(packet[staged]==0x6300001d);C(packet[staged+1]==0);C(packet[staged+30]==0x6400000b);staged+=n;++commit_count;}
static int g_cd_active,g_subpix_tri,g_surf_alpha,g_vc_textured,g_vc_vp_valid,g_geometry_matrix_valid;
static int32_t g_clip_cx,g_clip_cy,g_clip_hw,g_clip_hh;
static float g_cx,g_cy,g_hw,g_hh,g_vc_vp[4];
static uint32_t g_vc_words;
struct gfx_bram_import {int32_t u_offset,v_offset;uint32_t flags_rgb;};
typedef struct {int32_t xcenter,ycenter,xscale,yscale,near_clip,rows;} of_gpu_object_state_t;
static void emit_tri_state(int a){(void)a;++commands;}
static void of_gpu_set_object_state(const of_gpu_object_state_t *s){(void)s;++commands;}
static int gpu_async_epoch_check(void) {
#ifndef OF_PC
    const uint32_t now=read_mmio(0x2e);
    if(!async_epoch_valid||now!=async_epoch) {
        const int changed=async_epoch_valid;
        async_epoch=now;async_epoch_valid=1;
        async_sequence=read_mmio(0x23);
        async_finished_repair_valid=0;
        /* Consumer reset preserves a granted legacy lease and raw bank. */
        if(async_legacy_held && (!(read_mmio(0x21)&16u)||
           read_mmio(0x2f)!=async_ticket))gpu_async_fault();
        return !changed;
    }
#endif
    return 1;
}
static int gpu_async_raw_blocks_jobs(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u||!async_legacy_held||async_legacy_quiescent)return 0;
    gpu_async_epoch_check();
    if(read_mmio(0x23)!=async_sequence)return 1;
    /* No new async job can be submitted while this same lease is held:
     * enqueue explicitly releases first. Consumer reset preserves the lease
     * and cancels async work, so it cannot invalidate this no-blocker fact. */
    async_legacy_quiescent=1;
    return 0;
#else
    return 0;
#endif
}
void gfx_gpu_async_raw_release_if_pending(void) {
    if(gpu_async_raw_blocks_jobs())gfx_gpu_async_raw_release();
}
void gfx_gpu_async_before_wait(void) {
#ifndef OF_PC
    if(async_yielding||!gpu_async_raw_blocks_jobs())return;
    async_yielding=1;
    /* Pure frontend copy: no command emission, allocation or GPU wait. The
     * driver has completed and registered its raw result before any such wait. */
    gfx_async_materialize_raw();
    gfx_gpu_async_raw_release();
    async_yielding=0;
#endif
}
void gfx_gpu_wait_service(void) {
    gfx_gpu_async_before_wait();
    gfx_gpu_queued_front_service();
}
void gfx_gpu_async_raw_release(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u||!async_legacy_held)return;
    gpu_async_epoch_check();
    host_fence();
    write_mmio(0x2d,2);
    async_legacy_held=0;async_legacy_quiescent=0;
#endif
}
int gfx_gpu_async_raw_acquire(void) {
#ifndef OF_PC
    if(sm64_async_front_capability!=1u)return 0;
    if(async_legacy_held&&async_legacy_quiescent)return 0;
    gpu_async_epoch_check();
    if(async_legacy_held)return 0;
    if(read_mmio(0x23)!=async_sequence)of_gpu_kick_now();
retry:
    gpu_async_epoch_check();
    if(!++async_ticket)++async_ticket;
    write_mmio(0x2b,async_sequence);
    write_mmio(0x2c,async_ticket);
    write_mmio(0x2d,1);
    for(;;) {
        if(!gpu_async_epoch_check())goto retry;
        const uint32_t status=read_mmio(0x21);
        if(status&4u)gpu_async_fault();
        if((status&16u)&&read_mmio(0x2f)==async_ticket)break;
        if(status&2u)gfx_gpu_async_service();
        ++gfx_async_stats[11];
    }
    async_legacy_held=1;async_legacy_quiescent=0;
    ++gfx_async_stats[10];return 1;
#else
    return 0;
#endif
}
void gfx_gpu_async_job(uint32_t vertex_offset,uint32_t vtx_word,
                      uint32_t command_offset,unsigned command_count,unsigned cull,
                      const void *vertices,const void *commands,
                      const struct am_transform *transform,uint8_t alpha,
                      const struct gfx_bram_import *material) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    g_cd_active=0;g_subpix_tri=1;g_surf_alpha=alpha;
    emit_tri_state(g_vc_textured);
    if(!g_vc_vp_valid) {
        of_gpu_object_state_t os={0};
        os.xcenter=(int32_t)lrintf(g_cx);os.ycenter=(int32_t)lrintf(g_cy*16.0f);
        os.xscale=(int32_t)lrintf(g_hw);os.yscale=(int32_t)lrintf(g_hh*16.0f);
        os.near_clip=256;os.rows=3;of_gpu_set_object_state(&os);
        g_geometry_matrix_valid=0;g_vc_words+=27;
        g_vc_vp[0]=g_cx;g_vc_vp[1]=g_cy;g_vc_vp[2]=g_hw;g_vc_vp[3]=g_hh;
        g_vc_vp_valid=1;
    }
#ifndef OF_PC
    gpu_async_epoch_check();
    _Static_assert(sizeof(struct am_transform)==24u*4u,"AJ01 transform context");
    /* Priority legacy LOAD may overwrite every shared context field while
     * older jobs remain queued. Each job therefore restores one complete
     * immutable context; hardware locks this bundle through JOB acceptance. */
    _gpu_stream_reserve_words(42u);
    uint32_t *packet=_gpu_ring_claim();
    *packet++=0x6300001du;*packet++=0;
    for(unsigned i=0;i<16;++i)*packet++=transform->matrix[i];
    *packet++=transform->aspect;
    for(unsigned i=0;i<3;++i)*packet++=transform->coefficient[i];
    *packet++=transform->ambient;*packet++=transform->diffuse;
    *packet++=transform->scales;*packet++=transform->lighting;
    *packet++=(uint32_t)g_clip_cx;*packet++=(uint32_t)g_clip_cy;
    *packet++=(uint32_t)g_clip_hw;*packet++=(uint32_t)g_clip_hh;
    if(!++async_sequence)++async_sequence;
    *packet++=0x6400000bu;*packet++=async_sequence;*packet++=vertex_offset;
    *packet++=vtx_word;*packet++=command_offset;
    *packet++=command_count|((cull&3u)<<8);*packet++=material->flags_rgb;
    *packet++=(uint32_t)material->u_offset;*packet++=(uint32_t)material->v_offset;
    *packet++=(uint32_t)(uintptr_t)vertices;*packet++=(uint32_t)(uintptr_t)commands;
    *packet++=alpha;
    _gpu_ring_commit(42u);g_vc_words+=42u;gfx_async_stats[6]+=28u;
    /* Publish asynchronous work once complete. This is an input job, not a
     * packet-copy marker: no per-job raw ownership or ARM wait follows. */
    of_gpu_kick();
#else
    (void)vertex_offset;(void)vtx_word;(void)command_offset;(void)command_count;
    (void)cull;(void)vertices;(void)commands;(void)transform;(void)material;
#endif
}
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
    if(!load_lazy_slots)gfx_gpu_async_raw_release_if_pending();
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
    gfx_gpu_async_raw_release_if_pending();
}
void gfx_async_materialize_raw(void) {
    /* Progress waits are forbidden between installing legacy context and
     * registering its completed result. Fail closed if a future edit violates
     * that boundary; otherwise a drained but unregistered result could be lost. */
    if(load_alias_transaction)test_trap();
    while(raw_status()&1u)gfx_gpu_async_service();
    load_alias_materialize_all();
}

static void reset(void){
 memset(&rsp,0xa5,sizeof(rsp));memset(raw,0,sizeof(raw));memset(gfx_front_stats,0,sizeof(gfx_front_stats));
 async_sequence=10;async_ticket=7;async_epoch=hw_epoch=2;async_epoch_valid=1;
 hw_completed=9;hw_ticket=hw_grant_ticket=7;hw_lease=async_legacy_held=1;hw_pending=hw_fault=0;
 async_yielding=async_servicing=load_alias_transaction=async_legacy_quiescent=0;sm64_async_front_capability=1;
 load_lazy_slots=0;load_lazy_first=0;raw_delay=grant_delay=0;
 reads=writes=releases=acquires=fences=services=repair_services=recursion=0;
 trap_count=commands=commit_count=reserve_count=kicks=forced=staged=published=force_reserve_wait=0;
 g_vc_vp_valid=1;g_vc_words=0;
 for(unsigned i=0;i<32;i++){for(unsigned j=0;j<8;j++)raw[i][j]=0x40000000+i*256+j;raw[i][5]=(uint16_t)(-32768+(int)i*2000)|((uint32_t)(uint16_t)(32767-(int)i*1900)<<16);}
}
static void expected(struct Vertex *v,unsigned source){
 memcpy(v->word,raw[source],5*4);float u=(int16_t)raw[source][5],w=(int16_t)(raw[source][5]>>16);
 memcpy(v->word+5,&u,4);memcpy(v->word+6,&w,4);v->word[7]=raw[source][6];v->word[8]=raw[source][7];
}
int main(void){
 reset();async_legacy_held=0;gfx_gpu_async_before_wait();C(!reads&&!releases&&!commands);
 reset();sm64_async_front_capability=2;gfx_gpu_async_before_wait();C(!reads&&!releases);
 reset();hw_completed=async_sequence;load_lazy_slots=31;gfx_gpu_async_before_wait();C(async_legacy_held&&load_lazy_slots==31&&!releases);
 unsigned old_reads=reads;for(unsigned i=0;i<1000;i++){gfx_gpu_async_before_wait();C(!gfx_gpu_async_raw_acquire());}C(reads==old_reads);
 /* Quiescent ownership remains safe after a consumer reset: no job can be
  * introduced without releasing; the actual release still checks the epoch. */
 hw_epoch++;gfx_gpu_async_before_wait();C(reads==old_reads&&async_legacy_held);gfx_gpu_async_raw_release();C(!async_legacy_held&&!async_legacy_quiescent&&releases==1);
 reset();load_lazy_first=3;load_lazy_slots=31u<<3;gfx_gpu_async_before_wait();
 C(!async_legacy_held&&!load_lazy_slots&&releases==1&&!commands&&!staged);
 for(unsigned i=0;i<32;i++){struct Vertex e;memset(&e,0xa5,sizeof e);if(i>=3&&i<8)expected(&e,i-3);C(!memcmp(&e,&rsp.loaded_vertices[i],sizeof e));}
 /* Delayed already-accepted raw reads drain; a reentrant wait cannot recurse
  * into copying/releasing the same raw owner. */
 reset();load_lazy_slots=15;raw_delay=8;recursion=1;gfx_gpu_async_before_wait();C(repair_services==8&&services==1&&releases==1&&!async_yielding);
 reset();load_lazy_slots=15;load_alias_transaction=1;if(!setjmp(jumped)){gfx_gpu_async_before_wait();abort();}C(trap_count==1&&!releases&&load_lazy_slots==15);
 reset();hw_epoch++;hw_completed=10;load_lazy_slots=7;gfx_gpu_async_before_wait();C(async_legacy_held&&load_lazy_slots==7&&!releases);
 reset();hw_epoch++;hw_lease=0;if(!setjmp(jumped)){gfx_gpu_async_before_wait();abort();}C(trap_count==1&&!releases);
 /* Materialize masks preserve untouched canonical/fog words and leave a
  * sparse intermediate mask owned. The remaining runs finish before release. */
 for(unsigned first=0;first<32;first++)for(unsigned count=1;count<=32-first;count++){
  reset();load_lazy_first=first;uint32_t mask=(count==32?~0u:((1u<<count)-1u))<<first;load_lazy_slots=mask;
  uint32_t half=mask&0x55555555u;load_alias_materialize(half);C(load_lazy_slots==(mask&~half));
  C(releases==(load_lazy_slots==0));load_alias_materialize(~half);C(!load_lazy_slots&&releases==1);
  for(unsigned i=0;i<32;i++){struct Vertex e;memset(&e,0xa5,sizeof e);if(mask&(1u<<i))expected(&e,i-first);C(!memcmp(&e,&rsp.loaded_vertices[i],sizeof e));}
 }
 /* Partial overwrite barrier: copy survivors, drop overwritten raw rows;
  * no discarded row may leak into unrelated canonical/fog slots. */
 for(unsigned first=0;first<32;first++)for(unsigned count=1;count<=32-first;count++)for(unsigned dest=0;dest<32;dest+=3){
  reset();load_lazy_first=first;uint32_t mask=(count==32?~0u:((1u<<count)-1u))<<first;load_lazy_slots=mask;
  uint32_t overwritten=0xffffffffu<<dest;load_alias_materialize(~overwritten);load_lazy_slots&=~overwritten;if(!load_lazy_slots)gfx_gpu_async_raw_release_if_pending();
  C(!load_lazy_slots&&releases==1);for(unsigned i=0;i<32;i++){struct Vertex e;memset(&e,0xa5,sizeof e);if((mask&~overwritten)&(1u<<i))expected(&e,i-first);C(!memcmp(&e,&rsp.loaded_vertices[i],sizeof e));}
 }
 /* Entire context+JOB is one staged unit. An injected reserve wait happens
  * before sequence advancement/any context stores; source pointers and all
  * context words survive unchanged. Legacy overwrite does not use a delta. */
 for(unsigned trial=0;trial<1024;trial++){
  reset();hw_lease=async_legacy_held=0;struct am_transform t;uint32_t *tw=(uint32_t *)&t;
  for(unsigned i=0;i<24;i++)tw[i]=trial*65537u+i*101u;
  struct gfx_bram_import m={.u_offset=(int)trial*8,.v_offset=(int)trial*16,.flags_rgb=trial|0x10000};
  g_clip_cx=trial;g_clip_cy=trial+1;g_clip_hw=trial+2;g_clip_hh=trial+3;force_reserve_wait=1;
  gfx_gpu_async_job(0x1000,0x01003006,0x2000,7,2,(void *)(uintptr_t)0x10000000,(void *)(uintptr_t)0x10000100,&t,77,&m);
  C(staged==42&&published==0&&async_sequence==11&&reserve_count==1&&commit_count==1);
  for(unsigned i=0;i<24;i++)C(packet[i+2]==tw[i]);
  C(packet[26]==trial&&packet[27]==trial+1&&packet[28]==trial+2&&packet[29]==trial+3);
  C(packet[31]==11&&packet[32]==0x1000&&packet[33]==0x01003006&&packet[34]==0x2000&&packet[35]==(7|(2<<8)));
  C(packet[36]==m.flags_rgb&&packet[37]==(uint32_t)m.u_offset&&packet[38]==(uint32_t)m.v_offset&&packet[39]==0x10000000&&packet[40]==0x10000100&&packet[41]==77);
  /* Acquire publishes the staged bundle but can be granted while completed
   * remains nine: grant is deliberately not a source-lifetime fence. */
  grant_delay=11;C(gfx_gpu_async_raw_acquire()==1);C(async_legacy_held&&published==42&&hw_completed==9&&hw_target==11);
  C(!memcmp(wire,packet,42*4));load_lazy_slots=7;gfx_gpu_async_before_wait();C(!async_legacy_held&&!load_lazy_slots&&releases==1&&hw_completed==9);
 }
 printf("{\"status\":\"PASS\",\"checks\":%u,\"atomic_bundle_cases\":1024,\"mask_cases\":6336}\n",checks);
}
