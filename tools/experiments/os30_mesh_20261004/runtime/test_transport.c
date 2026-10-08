/* Delayed DMA validates lifetime through page reuse and fence-token wrap. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define MESH_TRANSPORT_TEST 1
#define OF_GPU_CACHE_LINE_BYTES 64
static uint32_t status=1u<<13;
#define GPU_STATUS status
typedef struct {
    unsigned subpix_y,attr_q29;
    int16_t clip_x0,clip_x1,clip_y0,clip_y1;
    uint32_t fb_base,tex_addr,z_base;
    int32_t fb_major_step,fb_minor_step,z_major_step,z_minor_step;
    uint16_t tex_width,tex_w_mask,tex_h_mask;
    uint8_t flags,colormap_id,z_mode,mirror_s,mirror_t,cd_combine,const_alpha;
    int32_t clamp_min[2],clamp_max[2];
} of_gpu_tri_state_t;
#define OF_GPU_PARAM_ATTR_PERSP 1
#define OF_GPU_PARAM_ATTR_PERSP_Q29 3
#define OF_GPU_PARAM_AXIS_X 0
#define OF_GPU_PARAM_RECORD_U16V16_COUNT16 0
static uint32_t command[1024],command_words,command_expected;
static unsigned descriptors,waits,states,legacy,dirty,flushes,programs;
static int _gpu_span_hdr_valid;
static const void *published;
static unsigned published_bytes;
static uint32_t next_fence=0xfffffffeu,completed_fence=0xfffffffdu;
struct Pending {const uint32_t *address;unsigned words;uint32_t data[64];};
static struct Pending pending[1024];
static unsigned pending_count;
static void _gpu_flush_cmd_cache_range(void *p,unsigned n) {
    published=p;published_bytes=n;flushes++;
}
static void _gpu_cmd_header(unsigned op,unsigned n) {
    assert(n+1<=1024);command[0]=(op<<24)|n;command_words=1;command_expected=n+1;
}
static uint32_t *_gpu_ring_claim(void) { return command+command_words; }
static void _gpu_ring_commit(unsigned n) {
    command_words+=n;assert(command_words==command_expected);
    if(command[0]>>24==0x63) {assert(command[1]==0);programs++;return;}
    if(command[0]==0x4a000011u) {states++;return;}
    assert(command[0]==0x62000007u);descriptors++;
    const uint32_t *ptr=(const uint32_t *)(uintptr_t)command[5];
    unsigned words=command[6];
    if(words==1) {ptr=(const uint32_t *)(uintptr_t)command[1];words=25;}
    assert(published==ptr&&published_bytes==words*4);
    assert(pending_count<1024);
    struct Pending *p=&pending[pending_count++];p->address=ptr;p->words=words;
    memcpy(p->data,ptr,words*4);
}
static void of_gpu_kick(void) { }
static void of_gpu_kick_now(void) { }
static uint32_t of_gpu_fence(void) { return next_fence++; }
static void of_gpu_wait(uint32_t token) {
    waits++;
    /* All work queued here is allowed to complete; no producer may have
     * overwritten any outstanding record, even when its ring retired. */
    for(unsigned i=0;i<pending_count;i++) {
        struct Pending *p=&pending[i];assert(!memcmp(p->data,p->address,p->words*4));
    }
    pending_count=0;completed_fence=token;
}
static void of_gpu_set_tri_state(const of_gpu_tri_state_t *s) {(void)s;states++;}
static void of_gpu_draw_vert_tri_rgb(const int16_t *x,const int16_t *y,const int32_t *s,
    const int32_t *t,const int32_t *zi,const uint16_t *rgb,uint32_t q29,
    const int32_t *depth,const uint16_t *d) {
    (void)x;(void)y;(void)s;(void)t;(void)zi;(void)rgb;(void)q29;(void)depth;(void)d;legacy++;
}
#include "mesh_transport.h"
struct TestTexture { uint32_t addr,slot_epoch[2];unsigned cur; };
static struct TestTexture test_texture={0x100000,{0,0},0};
static struct TestTexture *g_cur_tex[1]={&test_texture};
static uint32_t g_tex_epoch=7;
static int g_st_cache_valid;
#include "mesh_state_changed.h"
int main(void) {
    static uint32_t raw[128],indices[32],state[64];
    const int16_t x[3]={-300,67,511},y[3]={32,-300,0};
    const int32_t s[3]={-100,200,300},t[3]={400,-500,600},zi[3]={999,555,33},depth[3]={3,4,5};
    const uint16_t rgb[3]={0xabcd,0x1234,0xffff},add[3]={0,0x9fff,3};
    of_gpu_tri_state_t st={.subpix_y=1,.clip_x0=-2,.clip_x1=320,.clip_y0=-5,.clip_y1=240,
        .fb_base=0x12345678,.fb_major_step=640,.fb_minor_step=2,
        .tex_addr=0x11223344,.tex_width=32,.tex_w_mask=31,.tex_h_mask=63,
        .flags=0xa5,.colormap_id=15,.z_mode=3,.mirror_s=1,.mirror_t=1,.cd_combine=1,.const_alpha=93,
        .clamp_min={-1,-3},.clamp_max={2,4},.z_base=0x55667788,.z_major_step=642,.z_minor_step=2};
    mesh_draw_vert_tri_rgb(x,y,s,t,zi,rgb,0,depth,add);assert(legacy==1);
    status=0;mesh_transport_boot();assert(!mesh_active);
    status=1u<<13;mesh_transport_boot();assert(mesh_active&&programs==1);
    for(unsigned i=0;i<100000;i++) {
        if(i%3) {
            for(unsigned j=0;j<64;j++)state[j]=i*64+j;
            /* Alternate real submitted texture versus white/other addresses;
             * test the exact callback included by the renderer. */
            test_texture.cur=(i>>2)&1;test_texture.slot_epoch[0]=test_texture.slot_epoch[1]=0;
            state[27]=(i&1)?test_texture.addr:0x100002;g_st_cache_valid=1;_gpu_span_hdr_valid=1;
            assert(gfx_gpu_mesh_submit(raw,32,indices,64,state));
            assert(!g_st_cache_valid&&!_gpu_span_hdr_valid);dirty++;
            assert(test_texture.slot_epoch[test_texture.cur]==((i&1)?g_tex_epoch:0));
            assert(test_texture.slot_epoch[test_texture.cur^1]==0);
            assert(command[2]==128&&command[4]==((64u<<16)|32u));
            assert(command[7]==MESH_PROGRAM_ENTRY);
        } else {
            st.subpix_y=(i>>2)&1;mesh_set_tri_state(&st);
            assert(command[0]==0x4a000011u&&command[15]==((320u<<16)|65534u));
            assert(command[16]==((240u<<16)|65531u)&&!_gpu_span_hdr_valid);
            const uint32_t fixed[]={0x12345678,640,2,0x11223344,32,0x003f001f};
            assert(!memcmp(command+1,fixed,sizeof fixed));
            assert(command[7]==(0x73001fa5u|(st.subpix_y<<31)));
            const uint32_t tail[]={0xffffffffu,2,0xfffffffdu,4,0x55667788,642,2};
            assert(!memcmp(command+8,tail,sizeof tail)&&command[17]==93);
            unsigned normalize=(i>>3)&1;
            if(normalize)mesh_projected_triangle(x,y,s,t,zi,rgb,depth,(i&1)?add:NULL,1);
            else mesh_draw_vert_tri_rgb(x,y,s,t,zi,rgb,0,depth,(i&1)?add:NULL);
            const uint32_t *p=(const uint32_t *)(uintptr_t)command[1];
            assert(command[2]==24&&command[4]==1&&command[7]==MESH_PROJECTED_ENTRY);
            assert(p[0]==(st.subpix_y|((i&1)?4u:0u)|(normalize?2u:0u)));
            assert(p[1]==((320u<<16)|65534u)&&p[2]==((240u<<16)|65531u));
            for(unsigned j=0;j<3;j++) {
                const uint32_t *v=p+3+j*7;
                assert(v[0]==((uint16_t)x[j]|((uint32_t)(uint16_t)y[j]<<16)));
                assert(v[1]==(uint32_t)s[j]&&v[2]==(uint32_t)t[j]&&v[3]==(uint32_t)zi[j]);
                assert(v[4]==(uint32_t)depth[j]&&v[5]==rgb[j]&&v[6]==((i&1)?add[j]:0));
            }
        }
    }
    of_gpu_wait(of_gpu_fence());
    assert(descriptors==100000&&dirty==66666&&waits>700&&legacy==1);
    printf("PASS %u ordered descriptors, %u delayed-DMA waits, fence wrap, %u publications\n",
           descriptors,waits,flushes);
}
