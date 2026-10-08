#include <assert.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define G_LIGHTING 1
#define SM64_PERF_SCOPE(x) ((void)0)
struct RGBA {uint8_t r,g,b,a;};
struct LoadedVertex {float u,v;struct RGBA color;};
struct Light {uint8_t col[3];};
static struct {
    struct {uint16_t s,t;} texture_scaling_factor;
    unsigned geometry_mode,current_num_lights;
    struct Light current_lights[3];float current_lights_coeffs[2][3];
    struct LoadedVertex loaded_vertices[32];
} rsp;
static uint32_t lighting_epoch=1;
struct GeometryVertex {int16_t tc[2];uint32_t cn;uint8_t attributes;};
static struct GeometryVertex geometry_vertices[32];
struct Input {struct {int16_t tc[2];uint8_t cn[4];}v;};
static struct Input geometry_inputs[32];
static int capability=1;
static int gfx_gpu_attributes_enabled(void) {return capability;}
#include "gfx_geometry_attributes.h"

static uint32_t seed=0x30127004;
static uint32_t random_word(void) {seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static struct LoadedVertex reference(unsigned i) {
    struct LoadedVertex d={0};
    d.u=(int16_t)((int32_t)geometry_inputs[i].v.tc[0]*rsp.texture_scaling_factor.s>>16);
    d.v=(int16_t)((int32_t)geometry_inputs[i].v.tc[1]*rsp.texture_scaling_factor.t>>16);
    const uint8_t *cn=geometry_inputs[i].v.cn;
    int color[3]={cn[0],cn[1],cn[2]};
    if(rsp.geometry_mode&G_LIGHTING) {
        float intensity=0;
        if(rsp.current_num_lights>1)
            intensity=((int8_t)cn[0]*rsp.current_lights_coeffs[0][0]
                      +(int8_t)cn[1]*rsp.current_lights_coeffs[0][1]
                      +(int8_t)cn[2]*rsp.current_lights_coeffs[0][2])/127.0f;
        for(unsigned k=0;k<3;k++) {
            color[k]=rsp.current_lights[rsp.current_num_lights-1].col[k];
            if(intensity>0)color[k]+=intensity*rsp.current_lights[0].col[k];
            if(color[k]>255)color[k]=255;
        }
    }
    d.color=(struct RGBA){color[0],color[1],color[2],cn[3]};return d;
}
int main(void) {
    struct LoadedVertex expected[32];uint32_t valid=0;
    for(unsigned batch=0;batch<10000;batch++) {
        unsigned first=random_word()%32,count=1+random_word()%(32-first);
        uint32_t mask=(0xffffffffu>>(32-count))<<first;
        geometry_attributes_release(mask);valid&=~mask;
        rsp.geometry_mode=(random_word()&1)?G_LIGHTING:0;
        rsp.current_num_lights=1+(random_word()&1);
        rsp.texture_scaling_factor.s=random_word();rsp.texture_scaling_factor.t=random_word();
        for(unsigned k=0;k<3;k++) {
            rsp.current_lights_coeffs[0][k]=((int32_t)(random_word()%2001)-1000)/1733.0f;
            rsp.current_lights[0].col[k]=random_word();rsp.current_lights[1].col[k]=random_word();
        }
        lighting_epoch++;
        for(unsigned i=0;i<count;i++) {
            geometry_inputs[i].v.tc[0]=random_word();geometry_inputs[i].v.tc[1]=random_word();
            for(unsigned k=0;k<4;k++)geometry_inputs[i].v.cn[k]=random_word();
            expected[first+i]=reference(i);
        }
        assert(geometry_attributes_capture(count,first,mask));valid|=mask;
        // Destroy source and current state before arbitrary partial recovery.
        memset(geometry_inputs,0xa5,sizeof(geometry_inputs));
        memset(rsp.current_lights,0,sizeof(rsp.current_lights));
        rsp.texture_scaling_factor.s=rsp.texture_scaling_factor.t=0;
        uint32_t recover=random_word();
        geometry_attributes_materialize(recover);
        for(unsigned i=0;i<32;i++)if((valid&recover)&(1u<<i)) {
            assert(rsp.loaded_vertices[i].u==expected[i].u);
            assert(rsp.loaded_vertices[i].v==expected[i].v);
            assert(!memcmp(&rsp.loaded_vertices[i].color,&expected[i].color,sizeof(struct RGBA)));
        }
    }
    geometry_attributes_release(~0u);
    capability=0;assert(!geometry_attributes_capture(1,0,1));
    capability=1;rsp.geometry_mode=G_LIGHTING;rsp.current_num_lights=3;
    assert(!geometry_attributes_capture(1,0,1));
    puts("PASS: 10000 captured-state batches, partial overwrites/recovery, mutable input, ambient/directional/unlit and capability fallbacks");
}
