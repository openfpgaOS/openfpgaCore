#!/usr/bin/env python3
"""Prepare direct LoadedVertex consumption for cached GPU uploads only."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4'
STAGE = ROOT / 'docs/measurements/os30-fps25-20261005/SM64'
DEST = ROOT / 'docs/measurements/os30-fps25-20261005/fused-cache-v1'
HASHES = {
    'gfx_pc.c': '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0',
    'gfx_gpu.c': 'c6655bac3072cef63cfcfee8656fa4e302f9c1f07778225c241c81b7daf27e0d',
    'gfx_gpu.h': '0d67e4fdc8025d7cc113f4e1add3c8ac5327fc834918cfd35abc1326d51c3e5d',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(s, old, new):
    assert s.count(old) == 1, (s.count(old), old[:120])
    return s.replace(old, new, 1)


def struct_body(s, name):
    start = s.index('struct ' + name + ' {')
    end = s.index('\n};', start) + len('\n};')
    return s[start:end]


PROTOTYPE = '''void gfx_gpu_vtx_cache_loaded(const struct LoadedVertex *const v[3],
                               const struct RGBA *const colors[3],
                               const uint8_t slot[3], unsigned dirty_mask,
                               float uofs, float vofs, float bias, uint8_t alpha)'''


def header_transform(s, pc):
    rgba = struct_body(pc, 'RGBA')
    vertex = struct_body(pc, 'LoadedVertex').replace('rspv_t ', 'float ')
    shared = '''/* CPU renderer and GPU upload share one vertex layout. This API consumes
 * the pointers synchronously; it does not retain them or upload a clean slot.
 * The desktop renderer keeps its original local declarations. */
''' + rgba + '\n\n' + vertex + '''
_Static_assert(sizeof(struct LoadedVertex) == 36, "LoadedVertex size");
_Static_assert(offsetof(struct LoadedVertex, w) == 12, "LoadedVertex w");
_Static_assert(offsetof(struct LoadedVertex, w_inv) == 16, "LoadedVertex reciprocal");
_Static_assert(offsetof(struct LoadedVertex, u) == 20, "LoadedVertex u");
_Static_assert(offsetof(struct LoadedVertex, v) == 24, "LoadedVertex v");
_Static_assert(offsetof(struct LoadedVertex, color) == 28, "LoadedVertex colour");
_Static_assert(offsetof(struct LoadedVertex, clip_rej) == 32, "LoadedVertex clip mask");

'''
    s = once(s, '#include "gfx_rendering_api.h"', '#include <stddef.h>\n#include "gfx_rendering_api.h"')
    s = once(s, 'struct gfx_vc_vtx {', shared + 'struct gfx_vc_vtx {')
    s = once(s, 'int gfx_gpu_geometry_enabled(void);', PROTOTYPE + ';\nint gfx_gpu_geometry_enabled(void);')
    return s


def pc_transform(s):
    for name in ('RGBA', 'LoadedVertex'):
        body = struct_body(s, name)
        s = once(s, body, '#ifndef TARGET_OPENFPGA\n' + body + '\n#endif')
    start = s.index('    vc[0].a = surf_a;')
    end = s.index('\n    /* Ring draw order:', start)
    legacy = s[start:end]
    fused = '''    /* The backend consumes these sources before this function returns.
     * Each temporary has its own lifetime even for triangle-dependent LOD. */
    for (int i = 0; i < 3; i++) {
        if (!(dirty & (1u << i))) continue;
        colors[i] = rgb_in >= 0
            ? gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in],
                             v_arr[i], v1, &temporary_colors[i])
            : NULL;
    }
'''
    s = s[:start] + '#if GFX_GPU_GEOMETRY_WORKER_APP\n' + legacy + '\n#else\n' + fused + '#endif\n' + s[end:]
    s = once(s, '    struct gfx_vc_vtx vc[3];\n    const int   textured', '''#if GFX_GPU_GEOMETRY_WORKER_APP
    struct gfx_vc_vtx vc[3];
#else
    const struct RGBA *colors[3];
    struct RGBA temporary_colors[3];
#endif
    const int   textured''')
    start = s.index('    if(raw_geometry) {\n        const int16_t *positions[3];')
    end = s.index('\n    if (dirty & 1u)', start)
    legacy = s[start:end]
    fused = '''    gfx_gpu_vtx_cache_loaded(v_arr, colors, slot, dirty,
                              uofs, vofs, bias, surf_a);'''
    s = s[:start] + '#if GFX_GPU_GEOMETRY_WORKER_APP\n' + legacy + '\n#else\n' + fused + '\n#endif' + s[end:]
    return s


def gpu_transform(s):
    start = s.index('void gfx_gpu_vtx_cache_tri(')
    end = s.index('\n}\n', start) + 3
    old = s[start:end]
    new = once(old, '''void gfx_gpu_vtx_cache_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],
                           unsigned dirty_mask)''', PROTOTYPE)
    new = once(new, '    g_surf_alpha = v[0].a;', '    g_surf_alpha = alpha;')
    new = once(new, 'rgba_to_565(v[k].r, v[k].g, v[k].b)', 'rgba_to_565(colors[k]->r, colors[k]->g, colors[k]->b)')
    new = new.replace('v[k].w_inv', 'v[k]->w_inv')
    new = once(new, '''        of_gpu_load_vert_clip(slot[k],''', '''        /* LoadedVertex UVs are integer-derived floats. Keep the same
         * tile subtraction, 1/32 scaling and half-texel bias before Q16.16. */
        const float u = g_vc_textured ? (v[k]->u - uofs) / 32.0f + bias : 0.0f;
        const float tex_v = g_vc_textured ? (v[k]->v - vofs) / 32.0f + bias : 0.0f;
        of_gpu_load_vert_clip(slot[k],''')
    for old_field, new_field in (('cx', 'x'), ('cy', 'y'), ('cw', 'w')):
        new = new.replace('v[k].' + old_field, 'v[k]->' + new_field)
    new = new.replace('v[k].u * 65536.0f', 'u * 65536.0f')
    new = new.replace('v[k].v * 65536.0f', 'tex_v * 65536.0f')
    new = once(new, 'v[k].use_exact_depth ? v[k].exact_depth : (uint32_t)lrintf(df)', '(uint32_t)lrintf(df)')
    return s[:end] + '''

/* CPU-only fused upload. Geometry-worker builds retain the original record
 * API above, including the worker's exact_depth contract. */
''' + new + s[end:]


def main():
    originals = {name: (BASE / name).read_bytes() for name in ('gfx_pc.c', 'gfx_gpu.c')}
    originals['gfx_gpu.h'] = (STAGE / 'src/sm64/sm64/src/pc/gfx/gfx_gpu.h').read_bytes()
    for name, data in originals.items():
        assert sha(data) == HASHES[name], name
    changed = {
        'gfx_pc.c': pc_transform(originals['gfx_pc.c'].decode()).encode(),
        'gfx_gpu.c': gpu_transform(originals['gfx_gpu.c'].decode()).encode(),
        'gfx_gpu.h': header_transform(originals['gfx_gpu.h'].decode(), originals['gfx_pc.c'].decode()).encode(),
    }
    DEST.mkdir(exist_ok=False)
    files = {}
    for name, data in changed.items():
        (DEST / name).write_bytes(data)
        (DEST / (name + '.patch')).write_text(''.join(difflib.unified_diff(
            originals[name].decode().splitlines(True), data.decode().splitlines(True),
            fromfile='a/' + name, tofile='b/' + name)))
        files[name] = {'baseline_sha256': sha(originals[name]), 'candidate_sha256': sha(data)}
    (DEST / 'baseline-gfx_gpu.h').write_bytes(originals['gfx_gpu.h'])
    (DEST / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    record = {
        'candidate': DEST.name, 'qualified': False, 'files': files,
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'scope': 'Consume original LoadedVertex and resolved RGB pointers synchronously in cached uploads.',
        'preserved': [
            'Original cached packet opcodes, order, staleness and ring reservation.',
            'Original float rounding, texture bias, RGB565, depth and viewport math.',
            'All generic fallback and deferred-worker paths.',
            'LoadedVertex field offsets and size; shared declaration with static assertions.',
        ],
        'required_checks': [
            'Independent pointer-lifetime/layout and shader mapping review.',
            'Code generation audit, full guest scene comparison and exact GPU replay.',
            'Independent modeled CPU comparison before composition.',
        ],
        'stage_changed': False, 'sibling_changed': False, 'card_changed': False,
    }
    (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
