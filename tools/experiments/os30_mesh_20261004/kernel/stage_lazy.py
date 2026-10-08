#!/usr/bin/env python3
"""Stage exact lazy CPU vertices in a PRIVATE SM64 gfx directory.

This transformation composes with client/stage_material.py and runtime/stage.py.
It never edits gfx_gpu.c.  The ordinary floating-point loop is extracted, with
only its state reads redirected to a const snapshot. No math is reassociated.
"""
from pathlib import Path
import argparse
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
DEFAULT = ROOT / 'docs/measurements/os30-mesh-20261004/SM64/src/sm64/sm64/src/pc/gfx'


def transform(source):
    if '#include "sm64_lazy_cpu.h"' in source:
        raise ValueError('lazy CPU transformation already applied')
    start = source.index('    { /* Time the complete fallback loop,')
    end = source.index('\n#ifdef TARGET_POCKET\n    prof_vtx', start)
    original = source[start:end]
    body = original.replace('rsp.', 'vertex_rsp->')
    body = body.replace('&vertex_rsp->loaded_vertices[dest_index]',
                        '&rsp.loaded_vertices[dest_index]')
    body = body.replace('lighting_epoch', 'vertex_lighting_epoch')
    body = body.replace('configEnableFog', 'vertex_fog_enabled')
    cpu = '''/* Generated directly from gfx_sp_vertex's original fallback loop.
 * Keep the expression order unchanged: deferred and eager vertices must match
 * in every float bit, UV, color and clip flag. */
static __attribute__((noinline)) void gfx_mesh_cpu_vertices(
        size_t n_vertices,size_t dest_index,const Vtx *vertices,float aspect_scale,
        const struct RSP *vertex_rsp,bool vertex_fog_enabled,
        uint32_t vertex_lighting_epoch) {
''' + body + '\n}\n'
    replacement = '''#ifdef TARGET_OPENFPGA
    mesh_lazy_stats[5]++;mesh_lazy_stats[6]+=n_vertices;
    gfx_mesh_cpu_vertices(n_vertices,dest_index,vertices,aspect_scale,
                          &rsp,configEnableFog,lighting_epoch);
#else
''' + original + '\n#endif'
    source = source[:start] + replacement + source[end:]

    def rep(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError((old, source.count(old)))
        source = source.replace(old, new)

    rep('static void mesh_shadow_capture_vertex(size_t,size_t,const Vtx *,float);',
        '#ifdef TARGET_OPENFPGA\n#include "sm64_lazy_cpu.h"\n'
        '#include "sm64_lazy_state.h"\n#endif\n\n'
        'static void mesh_shadow_capture_vertex(size_t,size_t,const Vtx *,float);')
    rep('    gfx_flush_vertex_attributes(vertex_attributes_pending&~overwritten);',
        '    gfx_mesh_lazy_before_load(overwritten);\n'
        '    gfx_flush_vertex_attributes(vertex_attributes_pending&~overwritten);')
    rep('    mesh_shadow_capture_vertex(n_vertices,dest_index,vertices,aspect_scale);',
        '    mesh_shadow_capture_vertex(n_vertices,dest_index,vertices,aspect_scale);\n'
        '    if(gfx_mesh_lazy_capture(n_vertices,dest_index,vertices,aspect_scale)) {\n'
        '        SM64_PERF_COUNT(SM64_PERF_RAW,n_vertices);return;\n    }')
    rep('#include "sm64_shadow.h"',
        '#include "sm64_shadow.h"\n#include "sm64_lazy_capture.h"')
    rep('        if(!GFX_GPU_GEOMETRY_REFERENCE && triangle_opcode && geometry_valid) {',
        '        if(triangle_opcode)gfx_mesh_lazy_fallback(cmd);\n'
        '        if(!GFX_GPU_GEOMETRY_REFERENCE && triangle_opcode && geometry_valid) {')
    rep('    struct LoadedVertex *v1 = &rsp.loaded_vertices[vtx1_idx];',
        '#ifdef TARGET_OPENFPGA\n'
        '    gfx_mesh_lazy_materialize_slot(vtx1_idx);\n'
        '    gfx_mesh_lazy_materialize_slot(vtx2_idx);\n'
        '    gfx_mesh_lazy_materialize_slot(vtx3_idx);\n'
        '#endif\n    struct LoadedVertex *v1 = &rsp.loaded_vertices[vtx1_idx];')
    rep('if(geometry_valid & (1u<<slot[i]))',
        'if((geometry_valid|mesh_lazy_depth_valid) & (1u<<slot[i]))')
    rep('if(geometry_valid&(1u<<slot))',
        'if((geometry_valid|mesh_lazy_depth_valid)&(1u<<slot))')
    return source, cpu


def stage(gfx):
    path = gfx / 'gfx_pc.c'
    source, cpu = transform(path.read_text())
    for name in ('sm64_lazy_state.h', 'sm64_lazy_capture.h'):
        shutil.copy2(HERE / name, gfx / name)
    (gfx / 'sm64_lazy_cpu.h').write_text(cpu)
    path.write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gfx-dir', type=Path, default=DEFAULT)
    args = parser.parse_args()
    stage(args.gfx_dir)
    print(args.gfx_dir)
