#!/usr/bin/env python3
"""Compile real material helper and verify vertex LOAD versus DRAW fog toggles."""
from pathlib import Path
import os
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
shadow = (HERE / 'sm64_shadow.h').read_text()
start = shadow.index('    state[22]=rsp.geometry_mode;')
end = shadow.index('    state[23]=', start)
capture = shadow[start:end]
source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
#define MESH_SHADOW_JOBS 1
#define G_FOG 0x10000
#define G_CULL_FRONT 0x1000
#define G_CULL_BACK 0x2000
#define G_MDSFT_TEXTFILT 0
#define G_TF_POINT 0
#define CC_SHADE 1
#define CC_LOD 2
struct MeshOwnedJob { uint32_t state[64]; };
struct RGBA { uint8_t r,g,b,a; };
struct ColorCombiner { unsigned shader_input_mapping[2][4]; };
static struct { unsigned geometry_mode; } rsp;
static struct { unsigned other_mode_h; struct { int uls,ult; } texture_tile; } rdp;
static int configEnableFog, select_alpha;
static bool triangle_mesh_material_ready;
static struct ColorCombiner comb;
static struct ColorCombiner *gfx_prepare_triangle_material(bool *fog,bool *alpha,bool *texture) {
    *fog=false;*alpha=select_alpha;*texture=false;return &comb;
}
static int gfx_gpu_mesh_select(int *rgb,int *alpha) { *rgb=-1;*alpha=select_alpha?0:-1;return 1; }
static const struct RGBA *gfx_vc_resolve(unsigned m,void *a,void *b,struct RGBA *c) {
    (void)m;(void)a;(void)b;*c=(struct RGBA){255,255,255,255};return c;
}
static void gfx_gpu_mesh_snapshot(uint32_t *o,int texture,uint8_t alpha) {
    (void)texture;o[40]=alpha;
}
#include "sm64_material.h"
static void capture_load(uint32_t *state) {
CAPTURE
}
int main(void) {
    comb.shader_input_mapping[1][0]=CC_SHADE;
    unsigned tests=0;
    for(unsigned geometry=0;geometry<2;geometry++)
    for(unsigned load=0;load<2;load++)
    for(unsigned draw=0;draw<2;draw++)
    for(unsigned shade=0;shade<2;shade++) {
        struct MeshOwnedJob job={0};uint32_t out[64]={0};
        rsp.geometry_mode=G_CULL_BACK|(geometry?G_FOG:0);
        configEnableFog=load;capture_load(job.state);
        configEnableFog=draw;select_alpha=shade;
        triangle_mesh_material_ready=false;
        unsigned expected_fog=geometry&&load;
        unsigned status=mesh_capture_material(&job,out);
        assert(status==(shade&&expected_fog?MESH_MATERIAL_FOG_ALPHA:MESH_MATERIAL_OK));
        if(status==MESH_MATERIAL_OK)assert(!!(out[22]&G_FOG)==expected_fog);
        assert(!!(job.state[22]&G_FOG)==expected_fog);
        tests++;
    }
    assert(tests==16);
}
'''.replace('CAPTURE', capture)
with tempfile.TemporaryDirectory(prefix='mesh-fog-state-') as temp:
    dest = Path(temp)
    (dest / 'test.c').write_text(source)
    subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                    '-fsanitize=address,undefined', '-I' + str(HERE), str(dest / 'test.c'),
                    '-o', str(dest / 'test')], check=True)
    env = os.environ.copy()
    env['ASAN_OPTIONS'] = 'detect_leaks=0'
    subprocess.run([str(dest / 'test')], check=True, env=env)
print('PASS: 16 load/draw/config/geometry/shade fog combinations, real material helper')
