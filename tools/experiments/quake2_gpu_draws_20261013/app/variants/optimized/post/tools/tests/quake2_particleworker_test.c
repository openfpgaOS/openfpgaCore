#define main renderer_existing_main
#include "quake2_renderer_test.c"
#undef main

typedef struct { float xyz[3]; int colour; float alpha; } input_t;
static uint32_t palette[256];
static uint8_t particle_gamma[256];
static const float up[3] = {0,1.5f,0}, right[3] = {1.5f,0,0};
static const float eye[3] = {0,0,0}, forward[3] = {0,0,-1};
static void prepare(input_t *p, unsigned n)
{
    for (unsigned i=0; i<n; ++i) {
        p[i] = (input_t){{0,0,-100}, (int)(i&255), (i&1) ? .5f : 1};
    }
    setup_world_arrays(p[0].xyz);
    ofgl_tex2d_on[1] = 0;
    ofgl_TexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE);
    ofgl_Enable(GL_BLEND); ofgl_DepthMask(GL_FALSE);
    gpu_particles = 1; particle_fail = 0;
    particle_total = particle_calls = 0;
    reset_geometry_recording();
    record_order = 1; render_order_count = 0;
}
static void draw(input_t *p, unsigned n)
{
    assert((gpu_particles ? OFGL_DrawNativeParticleBillboards : OFGL_DrawParticleBillboards)(p[0].xyz, &p[0].colour, &p[0].alpha,
        sizeof(*p), n, up, right, eye, forward, palette, particle_gamma));
}
static void canonical(void)
{
    assert(ofq2_particle_rne(1,1)==0 && ofq2_particle_rne(3,1)==2);
    assert(ofq2_particle_rne(-1,1)==0 && ofq2_particle_rne(-3,1)==-2);
    assert(ofq2_particle_floor(-1,1)==-1 && ofq2_particle_floor(-3,1)==-2);
    uint32_t ctx[16] = {0,98304,0,98304,0,0,10452992,7831552,
        10485760,10485760,1048576,32768,557056,32768,557056,0};
    uint32_t rec[5] = {0,0,6553600,91750,0x80ffff};
    uint32_t words[17]; int clip[4]={0,0,320,240};
    assert(ofq2_particle_reference(ctx,rec,clip,words)==1);
    assert((int16_t)words[0]==2552 && (int16_t)(words[0]>>16)==1912);
    assert((int16_t)words[1]==2552 && (int16_t)(words[1]>>16)==1858);
    assert((int16_t)words[2]==2606 && (int16_t)(words[2]>>16)==1912);
    for (unsigned i=0;i<3;++i) assert(words[9+i]==(1u<<24) && words[14+i]==10737418);
    assert(words[12]==0xffffffffu && words[13]==65535);
    rec[4] &= 65535; assert(ofq2_particle_reference(ctx,rec,clip,words)==0);
    rec[4] |= 0x800000; rec[2]=15*65536;
    assert(ofq2_particle_reference(ctx,rec,clip,words)==-1);
    rec[2]=100*65536; ctx[2]=1;
    assert(ofq2_particle_reference(ctx,rec,clip,words)==-1);
    ctx[2]=0; ctx[8]=0xffffffff;
    assert(ofq2_particle_reference(ctx,rec,clip,words)==-1);
}
int main(void)
{
    canonical();
    assert(RI_InitContext(NULL));
    for (unsigned i=0;i<256;++i) { palette[i]=0xffffffu; particle_gamma[i]=i; }
    input_t p[132]; prepare(p,132); draw(p,132);
    assert(particle_calls==3 && particle_total==132);
    assert(particle_counts[0]==64 && particle_counts[1]==64 && particle_counts[2]==4);
    assert(!draws && render_order_count==3 && !ofgl_gpu_slots_valid);
    for (unsigned i=0;i<132;++i) {
        assert(particle_records_seen[i][2]==100*65536);
        assert((particle_records_seen[i][4]>>16)==((i&1)?127:255));
    }
    assert(!particle_context_seen[2] && !particle_context_seen[5]);
    prepare(p,5); p[2].xyz[2]=-8; draw(p,5);
    assert(particle_calls==2 && particle_counts[0]==2 && particle_counts[1]==2);
    assert(draws==1 && render_order_count==3 && !memcmp(render_order,"PCP",3));
    assert(ofgl_batch_alpha==-1); /* a later CPU draw must rebind alpha */
    prepare(p,4); particle_fail=1; draw(p,4);
    assert(!particle_calls && draws==4); /* legacy fallback on refused native job */
    assert(alpha_seen[0]==255 && alpha_seen[1]==127);
    prepare(p,4); gpu_particles=0; draw(p,4);
    assert(!particle_calls && draws==4); /* old core follows old shader */
    prepare(p,4); ofgl_DepthMask(GL_TRUE); draw(p,4);
    assert(!particle_calls && draws==4); /* unsupported depth state */
    RI_ShutdownContext();
    puts("PASS: particle ABI batching64, mixed near fallback/order, alpha rebinding, canonical CPU fallback, old-core capability and precision state guards");
    return 0;
}
