/* Private mesh ABI transport. Include after of_gpu.h; never edit the SDK.
 * Four independently fenced pages own mutable DMA data until raster completion.
 * Ring retirement is deliberately not used as a memory-lifetime signal. */
#ifndef OS30_MESH_TRANSPORT_H
#define OS30_MESH_TRANSPORT_H
#include <stdint.h>
#include <string.h>
#include "mesh_program_words.h"

enum { MESH_PAGE_COUNT=4, MESH_PAGE_RECORDS=128, MESH_RECORD_WORDS=64 };
static uint32_t mesh_records[MESH_PAGE_COUNT][MESH_PAGE_RECORDS][MESH_RECORD_WORDS]
    __attribute__((aligned(64)));
static uint32_t mesh_page_fence[MESH_PAGE_COUNT];
static unsigned char mesh_page_live[MESH_PAGE_COUNT];
static unsigned mesh_page,mesh_record,mesh_active;
static of_gpu_tri_state_t mesh_current_state;
static void mesh_renderer_state_changed(uint32_t submitted_texaddr);

int gfx_gpu_mesh_enabled(void) { return mesh_active; }

#if !defined(OF_PC) || defined(MESH_TRANSPORT_TEST)
#include "mesh_tri_state.h"
static void mesh_publish(const void *address,unsigned bytes) {
    if(!bytes)return;
    _gpu_flush_cmd_cache_range((void *)address,bytes);
    /* Read through the SAME cached master after writeback, before publishing
     * any descriptor. A CPU fence alone does not drain the writeback queue. */
    volatile const uint32_t *p=address;
    uint32_t sink=0;
    for(unsigned off=0;off<bytes;off+=OF_GPU_CACHE_LINE_BYTES)sink^=p[off/4];
    sink^=p[(bytes-1)/4];
#ifndef MESH_TRANSPORT_TEST
    __asm__ volatile("" :: "r"(sink) : "memory");
    __asm__ volatile("fence" ::: "memory");
#else
    (void)sink;
#endif
}
static uint32_t *mesh_acquire_record(void) {
    if(mesh_record==MESH_PAGE_RECORDS) {
        mesh_page_fence[mesh_page]=of_gpu_fence();
        mesh_page_live[mesh_page]=1;
        of_gpu_kick_now();
        mesh_page=(mesh_page+1)&(MESH_PAGE_COUNT-1);mesh_record=0;
        if(mesh_page_live[mesh_page]) {
            of_gpu_wait(mesh_page_fence[mesh_page]);
            mesh_page_live[mesh_page]=0;
        }
    }
    return mesh_records[mesh_page][mesh_record++];
}
static void mesh_descriptor(const uint32_t *vertices,unsigned vertex_words,
                            const uint32_t *indices,unsigned index_words,unsigned triangles,
                            const uint32_t *state,unsigned state_words,unsigned entry) {
    _gpu_cmd_header(0x62,7);
    uint32_t *w=_gpu_ring_claim();
    *w++=(uint32_t)(uintptr_t)vertices;*w++=vertex_words;
    *w++=(uint32_t)(uintptr_t)indices;*w++=index_words|(triangles<<16);
    *w++=(uint32_t)(uintptr_t)state;*w++=state_words;*w++=entry;
    _gpu_ring_commit(7);
}
static void mesh_transport_boot(void) {
    if(!(GPU_STATUS&(1u<<13)))return;
    mesh_page=mesh_record=0;memset(mesh_page_fence,0,sizeof(mesh_page_fence));
    memset(mesh_page_live,0,sizeof(mesh_page_live));
    _gpu_cmd_header(0x63,MESH_PROGRAM_WORD_COUNT+1);
    uint32_t *w=_gpu_ring_claim();*w++=0;
    memcpy(w,mesh_program_words,sizeof(mesh_program_words));
    _gpu_ring_commit(MESH_PROGRAM_WORD_COUNT+1);
    of_gpu_kick_now();mesh_active=1;
}
void gfx_gpu_mesh_publish_cached(const void *address,unsigned bytes) {
    if(mesh_active)mesh_publish(address,bytes);
}
int gfx_gpu_mesh_submit(const uint32_t *vertices,unsigned vertex_count,
                       const uint32_t *indices,unsigned triangles,const uint32_t state[64]) {
    if(!mesh_active)return 0;
    if(!vertex_count||vertex_count>32||!triangles||triangles>64)__builtin_trap();
    uint32_t *record=mesh_acquire_record();memcpy(record,state,64*4);
    mesh_publish(record,64*4);
    mesh_descriptor(vertices,vertex_count*4,indices,(triangles+1)/2,triangles,
                    record,64,MESH_PROGRAM_ENTRY);
    /* The engine emits 0x4A, which overwrites the same shared staging as a
     * normal SDK state command. Invalidate its span memo as that API does. */
    _gpu_span_hdr_valid=0;
    mesh_renderer_state_changed(state[27]);
    of_gpu_kick();
    return 1;
}
static void mesh_projected_triangle(const int16_t x[3],const int16_t y[3],
                                   const int32_t s[3],const int32_t t[3],const int32_t zi[3],
                                   const uint16_t rgb[3],const int32_t depth[3],const uint16_t *additive,
                                   unsigned normalize) {
    uint32_t *record=mesh_acquire_record();
    record[0]=(mesh_current_state.subpix_y?1u:0u)|(additive?4u:0u)|(normalize?2u:0u);
    record[1]=(uint16_t)mesh_current_state.clip_x0|((uint32_t)(uint16_t)mesh_current_state.clip_x1<<16);
    record[2]=(uint16_t)mesh_current_state.clip_y0|((uint32_t)(uint16_t)mesh_current_state.clip_y1<<16);
    for(unsigned i=0;i<3;i++) {
        uint32_t *v=record+3+i*7;
        v[0]=(uint16_t)x[i]|((uint32_t)(uint16_t)y[i]<<16);
        v[1]=s[i];v[2]=t[i];v[3]=zi[i];v[4]=depth?depth[i]:zi[i];
        v[5]=rgb[i];v[6]=additive?additive[i]:0;
    }
    record[24]=0;mesh_publish(record,25*4);
    /* Legacy descriptor count, one dummy index/state word. The fallback
     * program copies the projected record directly into the triangle kernel. */
    mesh_descriptor(record,24,record+24,1,0,record+24,1,MESH_PROJECTED_ENTRY);
}
#else
static void mesh_transport_boot(void) { }
void gfx_gpu_mesh_publish_cached(const void *address,unsigned bytes) {(void)address;(void)bytes;}
int gfx_gpu_mesh_submit(const uint32_t *v,unsigned n,const uint32_t *i,unsigned t,const uint32_t s[64]) {
    (void)v;(void)n;(void)i;(void)t;(void)s;return 0;
}
#endif

static void mesh_set_tri_state(const of_gpu_tri_state_t *state) {
    mesh_current_state=*state;
#if !defined(OF_PC) || defined(MESH_TRANSPORT_TEST)
    if(mesh_active) {mesh_emit_tri_state(state);return;}
#endif
    of_gpu_set_tri_state(state);
}
static void mesh_draw_vert_tri_rgb(const int16_t x[3],const int16_t y[3],
                                  const int32_t s[3],const int32_t t[3],const int32_t zi[3],
                                  const uint16_t rgb[3],uint32_t q29,const int32_t depth[3],
                                  const uint16_t *additive) {
#if !defined(OF_PC) || defined(MESH_TRANSPORT_TEST)
    if(mesh_active) {
        if(q29||mesh_current_state.attr_q29)__builtin_trap();
        mesh_projected_triangle(x,y,s,t,zi,rgb,depth,additive,0);return;
    }
#endif
    of_gpu_draw_vert_tri_rgb(x,y,s,t,zi,rgb,q29,depth,additive);
}
#endif
