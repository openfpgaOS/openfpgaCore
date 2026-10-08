#!/usr/bin/env python3
"""Add a non-emitting concrete-material capture to the private SM64 app."""
from pathlib import Path
import shutil
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
APP=ROOT/'docs/measurements/os30-mesh-20261004/SM64'
GFX=APP/'src/sm64/sm64/src/pc/gfx'
# Keep the old fixtures reproducible after this private app is rebuilt.
old=APP/'mesh-shadow/shadow-v1.elf'
if not old.exists():shutil.copy2(APP/'.obj/sm64-perfdiag/app.elf',old)
for name in ('sm64_material.h','sm64_material_gpu.h'):
    shutil.copy2(HERE/name,GFX/name)
p=GFX/'gfx_pc.c';s=p.read_text()
if 'static bool triangle_mesh_material_ready;' not in s:
    before='static bool triangle_material_ready;'
    assert s.count(before)==1
    s=s.replace(before,before+'\nstatic bool triangle_mesh_material_ready;')
if 'triangle_mesh_material_ready=false;' not in s:
    before='        triangle_material_ready=true;'
    assert s.count(before)==1
    s=s.replace(before,'        triangle_mesh_material_ready=false;\n'+before)
p.write_text(s)
p=GFX/'gfx_gpu.c';s=p.read_text()
if 'static void mesh_build_tri_state(' not in s:
    begin=s.index('    struct GTex *t = textured ? g_cur_tex[0] : NULL;',s.index('static void emit_tri_state'))
    end=s.index('    /* Skip redundant sticky-state commands',begin)
    body=s[begin:end]
    for a,b in [('g_cd_active','cd'),('g_subpix_tri','subpix'),('g_surf_alpha','alpha'),('g_alpha_mask','alpha_mask')]:
        body=body.replace(a,b)
    helper='static void mesh_build_tri_state(int textured,int cd,int subpix,unsigned alpha,int alpha_mask,of_gpu_tri_state_t *out) {\n'+body+'    *out=st;\n}\n\n#include "sm64_material_gpu.h"\n\n'
    s=s[:begin]+'    of_gpu_tri_state_t st;\n    mesh_build_tri_state(textured,g_cd_active,g_subpix_tri,g_surf_alpha,g_alpha_mask,&st);\n\n'+s[end:]
    start=s.index('/* Emit the sticky surface state for the current batch. */')
    s=s[:start]+helper+s[start:];p.write_text(s)
p=GFX/'gfx_gpu.h';s=p.read_text()
if 'gfx_gpu_mesh_select' not in s:
    s=s.replace('int  gfx_gpu_vtx_cache_begin',
        'int gfx_gpu_mesh_select(int *rgb,int *alpha);\nvoid gfx_gpu_mesh_snapshot(uint32_t out[64],int textured,uint8_t alpha);\n\nint  gfx_gpu_vtx_cache_begin')
    p.write_text(s)
p=GFX/'sm64_shadow.h';s=p.read_text()
if '#include "sm64_material.h"' not in s:
    s=s.replace('static void mesh_shadow_capture_run(', '#include "sm64_material.h"\n\nstatic void mesh_shadow_capture_run(')
    s=s.replace('    mesh_shadow_stats[11]+=run->triangles;',
        '    mesh_material_status[mesh_shadow_count-1]=mesh_capture_material(job,mesh_material_state[mesh_shadow_count-1]);\n    mesh_shadow_stats[11]+=run->triangles;')
    p.write_text(s)
print(APP)
