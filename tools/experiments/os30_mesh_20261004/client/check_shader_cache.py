#!/usr/bin/env python3
"""Exercise real cached shader construction against the uncached LOAD recipe."""
from pathlib import Path
import os
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
SOURCE=r'''
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include <math.h>
#include <stdio.h>
#define G_LIGHTING 1u
#define G_TEXTURE_GEN 2u
#define MESH_STATE_FLOAT_MATRIX 40
#define RSPV_TO_FLOAT(x) (x)
static struct {
    float MP_matrix[4][4],current_lights_coeffs[1][3];
    struct { unsigned char col[3]; } current_lights[8];
    unsigned geometry_mode,current_num_lights;
} rsp;
static uint32_t geometry_mvp_epoch=1,lighting_epoch=1;
#include "sm64_shader_cache.h"
static int original(uint32_t *state,float aspect) {
    memset(state,0,64*4);
    for(unsigned row=0;row<4;row++)for(unsigned k=0;k<4;k++) {
        float value=rsp.MP_matrix[k][row];
        memcpy(&state[40+row*4+k],&value,4);
        float f=value*(row==0?aspect:1.0f);
        if(!(fabsf(f)<(k==3?8000000.0f:127.0f)))return 4;
        state[row*4+k]=(int32_t)lrintf(f*(k==3?256.0f:16777216.0f));
    }
    memcpy(state+56,&aspect,4);
    unsigned lighting=(rsp.geometry_mode&G_LIGHTING)!=0;
    unsigned lights=rsp.current_num_lights?rsp.current_num_lights-1:0;
    if((rsp.geometry_mode&G_TEXTURE_GEN)||(lighting&&lights>1))return 5;
    for(unsigned k=0;k<3;k++) {
        float c=lighting&&lights?rsp.current_lights_coeffs[0][k]:0.0f;
        if(!(fabsf(c)<=1.000001f))return 5;
        state[16+k]=(int32_t)lrintf(c*(1073741824.0f/127.0f));
    }
    for(unsigned k=0;k<3;k++) {
        state[19]|=(uint32_t)rsp.current_lights[0].col[k]<<(8*k);
        state[20]|=(uint32_t)rsp.current_lights[lights].col[k]<<(8*k);
    }
    return 0;
}
static uint32_t seed=0x12874852;
static uint32_t rnd(void) {seed^=seed<<13;seed^=seed>>17;seed^=seed<<5;return seed;}
static uint32_t actual[64],expected[64];
static unsigned checks,accepted;
static void check(float aspect) {
    int a=mesh_shadow_update_shader(actual,aspect),b=original(expected,aspect);
    assert(a==b);checks++;
    if(!a) {assert(!memcmp(actual,expected,sizeof actual));accepted++;}
}
int main(void) {
    float aspect=1.0f;rsp.current_num_lights=2;
    for(unsigned i=0;i<4;i++)rsp.MP_matrix[i][i]=1.0f;
    check(aspect);check(aspect);
    /* A failed matrix conversion can partially overwrite a previous valid
     * entry; restoring its old epoch must force a fresh conversion. */
    rsp.MP_matrix[0][0]=3.0f;rsp.MP_matrix[3][3]=9000000.0f;
    geometry_mvp_epoch=2;check(aspect);
    rsp.MP_matrix[0][0]=rsp.MP_matrix[3][3]=1.0f;
    geometry_mvp_epoch=1;check(aspect);
    /* The same partial-failure pattern for light coefficients. */
    rsp.geometry_mode=G_LIGHTING;check(aspect);
    rsp.current_lights_coeffs[0][0]=0.5f;rsp.current_lights_coeffs[0][2]=2.0f;
    lighting_epoch=2;check(aspect);
    rsp.current_lights_coeffs[0][0]=rsp.current_lights_coeffs[0][2]=0;
    lighting_epoch=1;check(aspect);
    for(unsigned i=0;i<200000;i++) {
        unsigned event=rnd()%12;
        if(event==0) {
            geometry_mvp_epoch++;
            unsigned row=rnd()%4,k=rnd()%4;
            rsp.MP_matrix[k][row]=(int)(rnd()%20001-10000)*(k==3?0.125f:0.001f);
            if(!(rnd()%17))rsp.MP_matrix[k][row]=k==3?8000000.0f:128.0f;
        } else if(event==1) {
            lighting_epoch++;
            for(unsigned k=0;k<3;k++)rsp.current_lights_coeffs[0][k]=(int)(rnd()%2001-1000)*0.001f;
            if(!(rnd()%17))rsp.current_lights_coeffs[0][rnd()%3]=1.1f;
        } else if(event==2) {
            aspect=(rnd()%400+1)*0.005f;
            if(!(rnd()%11))aspect=(rnd()&1)?0.0f:-0.0f;
        } else if(event==3)rsp.geometry_mode=rnd()%4;
        else if(event==4) {rsp.current_num_lights=rnd()%5;lighting_epoch++;}
        else if(event==5) {
            /* Even an unlit LOAD captures new colors while the transformed
             * normal epoch remains unchanged. */
            for(unsigned l=0;l<8;l++)for(unsigned k=0;k<3;k++)rsp.current_lights[l].col[k]=rnd();
        }
        check(aspect);
    }
    assert(checks==200007 && accepted>10000);
    printf("PASS %u shader LOADs, %u accepted exact64-word states\n",checks,accepted);
}
'''
with tempfile.TemporaryDirectory(prefix='mesh-shader-cache-') as temp:
    path=Path(temp);(path/'test.c').write_text(SOURCE)
    for flags in (['-O3','-ffast-math'],['-O2','-fsanitize=address,undefined']):
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,
                        '-I'+str(HERE),str(path/'test.c'),'-lm','-o',str(path/'test')],check=True)
        env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=0'
        subprocess.run([str(path/'test')],check=True,env=env)
