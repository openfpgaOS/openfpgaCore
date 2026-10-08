#!/usr/bin/env python3
"""Compare cached-bound guard eligibility with original geometry_prepare C."""
from pathlib import Path
import os
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
original=(ROOT/'docs/measurements/os30-frontend-20261004/SM64/src/sm64/sm64/src/pc/gfx/gfx_geometry.h').read_text()
start=original.index('static void geometry_update_planes(')
end=original.index('static unsigned geometry_outside(',start)
planes=original[start:end]
start=original.index('    // Intro/file-select',original.index('int geometry_prepare('))
end=original.index(' bounds_inside:',start)+len(' bounds_inside:')
body=original[start:end]
source=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#define G_ZBUFFER 1
#define G_TEXTURE_GEN 0x40000
#define LEVEL_UNKNOWN_3 3
typedef struct { int unused; } Vtx;
static struct { unsigned geometry_mode;float MP_matrix[4][4]; } rsp;
struct MeshVertexEntry { int16_t bounds[6]; };
struct GeometryPlane { float c[4];unsigned corner[3]; };
static struct GeometryPlane geometry_planes[8],geometry_frustum_planes[6];
static unsigned geometry_planes_epoch,geometry_mvp_epoch,gCurrLevelNum;
static float geometry_planes_aspect,current_box[6];
static bool geometry_inputs_valid=true;
static const float *geometry_bounds(const Vtx *v,unsigned n,float b[6]){(void)v;(void)n;(void)b;return current_box;}
static unsigned geometry_outside(const float *box){(void)box;return 0;}
static void vertex_cull_capture(const Vtx*v,unsigned n,unsigned first,float aspect,unsigned outside){(void)v;(void)n;(void)first;(void)aspect;(void)outside;}
PLANES
#include "sm64_guardband.h"
static int original_guard(unsigned count,unsigned first,float aspect) {
    Vtx vertex={0};const Vtx *vertices=&vertex;
BODY
    return 1;
}
static unsigned random_state=0x1937ea01;
static unsigned rnd(void){random_state^=random_state<<13;random_state^=random_state>>17;random_state^=random_state<<5;return random_state;}
int main(void){
    unsigned accepted=0;
    for(unsigned n=0;n<200000;n++) {
        struct MeshVertexEntry entry;
        for(unsigned k=0;k<3;k++){
            int16_t a=(int16_t)(rnd()%8192)-4096,b=(int16_t)(rnd()%8192)-4096;
            entry.bounds[k*2]=a<b?a:b;entry.bounds[k*2+1]=a>b?a:b;
        }
        if(n%11==0)entry.bounds[0]=entry.bounds[1];
        for(unsigned k=0;k<6;k++)current_box[k]=entry.bounds[k];
        for(unsigned k=0;k<4;k++)for(unsigned row=0;row<4;row++)
            rsp.MP_matrix[k][row]=(float)((int)(rnd()%2048)-1024)/1024.0f;
        for(unsigned k=0;k<3;k++)rsp.MP_matrix[k][2]=rsp.MP_matrix[k][3]*0.98f;
        rsp.MP_matrix[3][0]=(int)(rnd()%4096)-2048;
        rsp.MP_matrix[3][1]=(int)(rnd()%4096)-2048;
        rsp.MP_matrix[3][3]=(rnd()%32000)+4;
        rsp.MP_matrix[3][2]=rsp.MP_matrix[3][3]*0.98f;
        if(n%13==0)rsp.MP_matrix[0][3]=rsp.MP_matrix[1][3]=rsp.MP_matrix[2][3]=0;
        rsp.geometry_mode=(n%17?G_ZBUFFER:0)|(n%19?0:G_TEXTURE_GEN);
        gCurrLevelNum=n%23?9:3;
        unsigned first=rnd()%8,count=1+rnd()%32;
        float aspect=(n%2)?1.0f:1.3333334f;geometry_mvp_epoch++;
        int expected=original_guard(count,first,aspect)==1;
        int actual=mesh_guardband_accepted(&entry,count,first,aspect);
        if(expected!=actual){fprintf(stderr,"case%u expected%d actual%d\n",n,expected,actual);abort();}
        accepted+=actual;
    }
    assert(accepted>10000);
    printf("PASS 200000 original float LOAD predicates; %u guardband accepted\n",accepted);
}
'''.replace('PLANES',planes).replace('BODY',body)
with tempfile.TemporaryDirectory(prefix='mesh-guardband-') as name:
    dest=Path(name);(dest/'test.c').write_text(source)
    subprocess.run(['cc','-std=c11','-O2','-Wall','-Wextra','-Werror','-fno-fast-math','-ffp-contract=off',
                    '-fsanitize=address,undefined','-I'+str(HERE),str(dest/'test.c'),'-lm','-o',str(dest/'test')],check=True)
    env=os.environ.copy();env['ASAN_OPTIONS']='detect_leaks=0'
    subprocess.run([str(dest/'test')],check=True,env=env)
