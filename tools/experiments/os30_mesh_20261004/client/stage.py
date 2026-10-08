#!/usr/bin/env python3
"""Stage the non-rendering mesh shadow client in an isolated SM64 copy."""
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent
BASE=ROOT/'docs/measurements/os30-frontend-20261004/SM64'
APP=ROOT/'docs/measurements/os30-mesh-20261004/SM64'
if not APP.exists():shutil.copytree(BASE,APP)
GFX=APP/'src/sm64/sm64/src/pc/gfx'
for name in ('mesh_client.h','sm64_shadow.h','sm64_guardband.h','sm64_shader_cache.h'):shutil.copy2(HERE/name,GFX/name)
s=(BASE/'src/sm64/sm64/src/pc/gfx/gfx_pc.c').read_text()
def rep(old,new):
    global s
    assert s.count(old)==1,(old,s.count(old))
    s=s.replace(old,new)
# Capture is defined near the decoder; forward declarations keep original
# renderer function ordering unchanged.
rep('static __attribute__((noinline)) void gfx_sp_vertex(',
    'static void mesh_shadow_capture_vertex(size_t,size_t,const Vtx *,float);\n'
    'static void mesh_shadow_capture_run(const Gfx *);\n'
    'static __attribute__((noinline)) void gfx_sp_vertex(')
rep('    const int geometry_result=geometry_prepare(vertices,n_vertices,dest_index,aspect_scale);',
    '    mesh_shadow_capture_vertex(n_vertices,dest_index,vertices,aspect_scale);\n'
    '    const int geometry_result=geometry_prepare(vertices,n_vertices,dest_index,aspect_scale);')
rep('static void gfx_geometry_submit(', '#include "sm64_shadow.h"\n\nstatic void gfx_geometry_submit(')
rep('        const bool material_opcode=!triangle_opcode',
    '        if(triangle_opcode)mesh_shadow_capture_run(cmd);\n'
    '        else mesh_shadow_next=NULL;\n'
    '        const bool material_opcode=!triangle_opcode')
rep('void gfx_start_frame(void) {','void gfx_start_frame(void) {\n    mesh_shadow_count=0;mesh_shadow_next=NULL;')
(GFX/'gfx_pc.c').write_text(s)
print(APP)
