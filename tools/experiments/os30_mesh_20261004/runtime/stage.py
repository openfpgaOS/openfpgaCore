#!/usr/bin/env python3
"""Stage private SM64 mesh transport. No SDK or sibling-app mutation."""
from pathlib import Path
import shutil,sys
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
GFX=ROOT/'docs/measurements/os30-mesh-20261004/SM64/src/sm64/sm64/src/pc/gfx'
sys.path.insert(0,str(HERE.parent/'client'))
from mesh_program import build

def replace(s,old,new):
    assert s.count(old)==1,(old[:80],s.count(old))
    return s.replace(old,new,1)

def main():
    p=build();words=p.finish()
    (GFX/'mesh_program_words.h').write_text(
        '/* Generated private geometry program; regenerate with runtime/stage.py. */\n'
        f'#define MESH_PROGRAM_WORD_COUNT {len(words)}\n#define MESH_PROGRAM_ENTRY 0\n'
        f'#define MESH_PROJECTED_ENTRY {p.labels["projected_fallback"]}\n'
        'static const uint32_t mesh_program_words[MESH_PROGRAM_WORD_COUNT]={\n'+
        ''.join('    '+','.join(f'0x{x:08x}u' for x in words[i:i+8])+',\n' for i in range(0,len(words),8))+'};\n')
    shutil.copy2(GFX/'mesh_program_words.h',HERE/'mesh_program_words.h')
    shutil.copy2(HERE.parent/'client/mesh_client.h',GFX/'mesh_client.h')
    shutil.copy2(HERE.parent/'client/sm64_guardband.h',GFX/'sm64_guardband.h')
    shutil.copy2(HERE/'mesh_transport.h',GFX/'mesh_transport.h')
    shutil.copy2(HERE/'mesh_state_changed.h',GFX/'mesh_state_changed.h')
    shutil.copy2(HERE/'mesh_tri_state.h',GFX/'mesh_tri_state.h')
    shutil.copy2(HERE/'mesh_cached_projection.h',GFX/'mesh_cached_projection.h')
    shutil.copy2(HERE/'mesh_producer.h',GFX/'mesh_producer.h')
    path=GFX/'gfx_gpu.c';s=path.read_text()
    if '#include "mesh_transport.h"' not in s:
        s=replace(s,'#include "gfx_gpu.h"','#include "gfx_gpu.h"\n#include "mesh_transport.h"')
        s=s.replace('of_gpu_set_tri_state(', 'mesh_set_tri_state(')
        s=s.replace('of_gpu_draw_vert_tri_rgb(', 'mesh_draw_vert_tri_rgb(')
        at=s.index('static void build_palette(')
        s=s[:at]+'#include "mesh_state_changed.h"\n\n'+s[at:]
        old='''    g_has_gpu = caps && caps->gpu_base &&
                (caps->hw_features & OF_HW_GPU_SPAN) &&
                of_has_feature(OF_HW_GPU_VERT_TRI);'''
        new='''    g_has_gpu = caps && caps->gpu_base && (caps->hw_features & OF_HW_GPU_SPAN);
    if(g_has_gpu)of_gpu_init();
    int mesh_capability=0;
#ifndef OF_PC
    if(g_has_gpu)mesh_capability=(GPU_STATUS&(1u<<13))!=0;
#endif
    g_has_gpu=g_has_gpu&&(of_has_feature(OF_HW_GPU_VERT_TRI)||mesh_capability);'''
        s=replace(s,old,new)
        s=replace(s,'    if (g_has_gpu) {\n        of_gpu_init();','    if (g_has_gpu) {')
        s=replace(s,'        of_gpu_use_cpu_ring();','        of_gpu_use_cpu_ring();\n        mesh_transport_boot();')
        s=replace(s,'                  of_has_feature(OF_HW_GPU_CLIP_LOAD);','                  of_has_feature(OF_HW_GPU_CLIP_LOAD) && !mesh_capability;')
        path.write_text(s)
    elif 'static void mesh_renderer_state_changed(void) { g_st_cache_valid=0; }' in s:
        s=replace(s,'static void mesh_renderer_state_changed(void) { g_st_cache_valid=0; }',
                  '#include "mesh_state_changed.h"')
        path.write_text(s)
    if 'const int vc_live = g_clip_load && g_truecolor;' in s:
        s=replace(s,'const int vc_live = g_clip_load && g_truecolor;',
            'const int vc_live = (g_clip_load || gfx_gpu_mesh_enabled()) && g_truecolor;')
        path.write_text(s)
    old='    if (!g_clip_load || !g_truecolor || !g_cur_shader)'
    if old in s:
        s=replace(s,old,'    if ((!g_clip_load && !gfx_gpu_mesh_enabled()) || !g_truecolor || !g_cur_shader)')
        before='void gfx_gpu_vtx_cache_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],'
        s=replace(s,before,'#include "mesh_cached_projection.h"\n\n'+before)
        needle='    emit_tri_state(g_vc_textured);\n\n    /* X projection returns Q12.4.'
        s=replace(s,needle,'    emit_tri_state(g_vc_textured);\n'
            '    if(gfx_gpu_mesh_enabled()) {mesh_draw_cached_triangle(v,slot,dirty_mask);return;}\n\n'
            '    /* X projection returns Q12.4.')
        path.write_text(s)
    path=GFX/'gfx_pc.c';s=path.read_text()
    if '#include "mesh_producer.h"' not in s:
        s=replace(s,'#include "sm64_shadow.h"','#include "sm64_shadow.h"\n#include "mesh_producer.h"')
        s=replace(s,'        if(triangle_opcode)mesh_shadow_capture_run(cmd);',
            '        if(triangle_opcode && !gfx_gpu_mesh_enabled())mesh_shadow_capture_run(cmd);')
        at='        if(!GFX_GPU_GEOMETRY_REFERENCE && triangle_opcode && geometry_valid) {'
        s=replace(s,at,
            '        if(triangle_opcode && gfx_gpu_mesh_enabled()) {\n'
            '            Gfx *next=gfx_mesh_try_run(cmd);\n'
            '            if(next!=cmd) {cmd=next;continue;}\n'
            '        }\n'+at)
        path.write_text(s)
    path=GFX/'gfx_gpu.h';s=path.read_text()
    if 'gfx_gpu_mesh_submit' not in s:
        s=s.replace('int gfx_gpu_mesh_select',
            'int gfx_gpu_mesh_enabled(void);\n'
            'void gfx_gpu_mesh_publish_cached(const void *address,unsigned bytes);\n'
            'int gfx_gpu_mesh_submit(const uint32_t *vertices,unsigned vertex_count,\n'
            '                        const uint32_t *indices,unsigned triangles,const uint32_t state[64]);\n'
            'int gfx_gpu_mesh_select')
        path.write_text(s)
    print(GFX)
if __name__=='__main__':main()
