/* Persistent packet refreshes, duplicates, capacity and CPU ordering. */
#define main renderer_existing_main
#include "quake2_renderer_test.c"
#undef main

int main(void)
{
    assert(RI_InitContext(NULL));
    float raw[4][7] = {{-4,-4,-64,0,0,0,0},{4,-4,-64,1,0,0,0},
        {4,4,-64,1,1,0,0},{-4,4,-64,0,1,0,0}};
    float frac[4][7]; memcpy(frac,raw,sizeof(raw)); frac[0][0]=.125f;
    void *storage[2] = {malloc(OFGL_WorldPolyBytes(4)),malloc(OFGL_WorldPolyBytes(4))};
    setup_world_arrays(raw[0]);
    ofgl_world_poly_t *a = OFGL_BuildWorldPoly(storage[0],raw[0],4);
    ofgl_world_poly_t *b = OFGL_BuildWorldPoly(storage[1],frac[0],4);
    assert(a->raw_ok && !b->raw_ok);
    gpu_geometry = gpu_worker = 1;
    ofgl_world_poly_t *list[128]; for (int i=0;i<128;i++) list[i]=a;
    reset_geometry_recording(); packet_calls=0;
    OFGL_DrawWorldPolys(list,128);
    assert(geometry_draws==128 && draws==0 && packet_calls==7); /* 20*25 <=512 */
    assert(a->upload_header==0x59000014u);
    assert(a->words[20]==0x5b000001u && a->words[22]==0x5b000001u);
    /* A CPU polygon breaks a queued GPU run without reordering either side. */
    list[0]=a;list[1]=a;list[2]=b;list[3]=a;
    reset_geometry_recording(); packet_calls=render_order_count=0;record_order=1;
    OFGL_DrawWorldPolys(list,4);record_order=0;
    assert(packet_calls==2 && geometry_draws==3 && draws==2);
    assert(render_order_count==5 && !memcmp(render_order,"GGCCG",5));
    /* Refused packet groups retain ordered CPU fallback. */
    list[0]=a;list[1]=a;
    reset_geometry_recording(); packet_calls=0;packet_fail=1;
    OFGL_DrawWorldPolys(list,2);packet_fail=0;
    assert(packet_calls==1 && geometry_draws==0 && draws==4);
    /* Cull changes refresh just fan words, and repeated same-key calls reuse. */
    for (unsigned mode=0;mode<3;mode++) {
        setup_world_arrays(raw[0]);gpu_geometry=gpu_worker=1;
        if (mode) { ofgl_Enable(GL_CULL_FACE);ofgl_CullFace(mode==1?GL_FRONT:GL_BACK); }
        else ofgl_Disable(GL_CULL_FACE);
        reset_geometry_recording(); OFGL_DrawWorldPolys(list,2);
        unsigned expected=ofgl_cull_on?((ofgl_cull_face==GL_FRONT)==(ofgl_front_face==GL_CCW)?1:2):0;
        assert(a->packet_cull==expected && (a->words[21]>>15)==expected);
        uint32_t saved[24];memcpy(saved,a->words,sizeof(saved));
        reset_geometry_recording();OFGL_DrawWorldPolys(list,2);
        assert(!memcmp(saved,a->words,sizeof(saved)));
    }
    /* Material changes update the cached vertex payload before it is copied. */
    setup_world_arrays(raw[0]);gpu_geometry=gpu_worker=1;ofgl_Disable(GL_CULL_FACE);
    reset_geometry_recording();OFGL_DrawWorldPolys(list,2);
    uint32_t u=a->words[8];ofgl_world_material(a,16,16);
    assert(a->tex_w==16 && a->words[8]!=u);
    /* Inline cores keep the established emitter; capability-off stays CPU. */
    gpu_worker=0;reset_geometry_recording();packet_calls=0;
    OFGL_DrawWorldPolys(list,2);assert(!packet_calls && geometry_draws==2);
    gpu_geometry=0;reset_geometry_recording();OFGL_DrawWorldPolys(list,2);
    assert(!packet_calls && geometry_draws==0 && draws==4);
    free(storage[0]);free(storage[1]);RI_ShutdownContext();
    puts("PASS: cached worker packets, 128 duplicate polygons/512-word bound, CPU barriers, refused batch fallback, cull/material refresh and inline/absent capability");
    return 0;
}
