#!/usr/bin/env python3
"""Prepare a fixed-record, exact 0x4E fallback; never touch stage or sibling."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4'
DEST = ROOT / 'docs/measurements/os30-fps25-20261005/direct-record-v1'
HASHES = {
    'gfx_pc.c': '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0',
    'gfx_gpu.c': 'c6655bac3072cef63cfcfee8656fa4e302f9c1f07778225c241c81b7daf27e0d',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def once(source, old, new):
    assert source.count(old) == 1, (source.count(old), old[:120])
    return source.replace(old, new, 1)


def pc_transform(s):
    s = once(s, 'static float buf_vbo[MAX_BUFFERED * (26 * 3)]; // 3 vertices in a triangle and 26 values per vtx', '''#ifdef TARGET_OPENFPGA
/* Both representations have the same batch lifetime. The fixed records keep
 * byte colours and original clip positions; they still emit the exact 0x4E
 * fallback packet, never an indexed-triangle substitute. */
static union {
    float legacy[MAX_BUFFERED * (26 * 3)];
    struct gfx_vc_vtx direct[MAX_BUFFERED * 3];
} buf_vbo_storage;
#define buf_vbo (buf_vbo_storage.legacy)
static bool buf_vbo_direct;
extern void gfx_gpu_draw_direct_records(const struct gfx_vc_vtx *vertices,
                                        size_t num_tris);
#else
static float buf_vbo[MAX_BUFFERED * (26 * 3)];
#endif''')
    s = once(s, '        gfx_rapi->draw_triangles(buf_vbo, buf_vbo_len, buf_vbo_num_tris);', '''#ifdef TARGET_OPENFPGA
        if (buf_vbo_direct)
            gfx_gpu_draw_direct_records(buf_vbo_storage.direct, buf_vbo_num_tris);
        else
#endif
            gfx_rapi->draw_triangles(buf_vbo, buf_vbo_len, buf_vbo_num_tris);''')
    s = once(s, '        buf_vbo_len = 0;\n        buf_vbo_num_tris = 0;', '''        buf_vbo_len = 0;
        buf_vbo_num_tris = 0;
#ifdef TARGET_OPENFPGA
        buf_vbo_direct = false;
#endif''')
    s = once(s, '    const int elig = triangle_vc_elig;\n    const struct LoadedVertex *v_arr[3]', '''    const int elig = triangle_vc_elig;
    bool direct = false;
    const struct LoadedVertex *v_arr[3]''')
    s = once(s, '            goto per_tri_fallback;          /* no stable slot */', '            goto per_tri_direct;            /* no stable slot */')
    s = once(s, '''            if (!(w > 0.0f && w < 32767.0f))
                goto per_tri_fallback;''', '''            if (!(w > 0.0f && w < 32767.0f))
                goto per_tri_direct;''')
    s = once(s, '''    if (!raw_geometry && w_uniform)
        goto per_tri_fallback;

    /* Surface alpha:''', '''    if (!raw_geometry && w_uniform)
        goto per_tri_direct;
    goto prepare_payload;

per_tri_direct:
    /* Deferred worker vertices require the original materialization path.
     * This optimization only changes the CPU-computed representation. */
#if GFX_GPU_GEOMETRY_WORKER_APP
    goto per_tri_fallback;
#endif
    if (raw_geometry) goto per_tri_fallback;
    direct = true;
    dirty = 7u;
    if (buf_vbo_len && !buf_vbo_direct) gfx_flush();

prepare_payload: ;
    /* Surface alpha:''')
    s = once(s, '    struct gfx_vc_vtx vc[3];\n    const int   textured = elig', '''    struct gfx_vc_vtx cached_vertices[3];
    struct gfx_vc_vtx *vc = direct
        ? &buf_vbo_storage.direct[3 * buf_vbo_num_tris] : cached_vertices;
    const int   textured = elig''')
    s = once(s, '            if(geometry_valid & (1u<<slot[i])) {', '            if(!direct && (geometry_valid & (1u<<slot[i]))) {')
    s = once(s, '''    /* Ring draw order: buffered 0x4E triangles must land before this one''', '''    if (direct) {
        /* Keep exactly the old fallback batching and flush boundaries.
         * len remains an occupied-float count for the shared pending test. */
        buf_vbo_direct = true;
        buf_vbo_len += 3 * sizeof(struct gfx_vc_vtx) / sizeof(float);
        if (++buf_vbo_num_tris == MAX_BUFFERED) gfx_flush();
        return 1;
    }

    /* Ring draw order: buffered 0x4E triangles must land before this one''')
    s = once(s, '''per_tri_fallback:
    if(raw_geometry)return 0;
    /* Preserve original coordinates''', '''per_tri_fallback:
    if(raw_geometry)return 0;
    if (buf_vbo_len && buf_vbo_direct) gfx_flush();
    /* Preserve original coordinates''')
    return s


DIRECT = r'''
/* Fixed-record fallback for the same simple material class as 0x54. This
 * preserves the existing screen-space 0x4E packet, including its per-triangle
 * perspective scale and explicit depth. C+D, decals, XLU and alpha-mask
 * materials keep using gpu_draw_triangles and its complete combiner logic. */
void gfx_gpu_draw_direct_records(const struct gfx_vc_vtx *vertices,
                                 size_t num_tris) {
    SM64_PERF_SCOPE(SM64_PERF_SEND);
    if (!g_has_gpu || !g_cur_shader || num_tris == 0) return;
    const int textured = g_vc_textured;
    if (textured) g_cd_fallbacks++;
    g_cd_active = 0;
    g_subpix_tri = 1;
    for (size_t tri = 0; tri < num_tris; tri++) {
        const struct gfx_vc_vtx *v = vertices + 3 * tri;
        int16_t x[3], y[3];
        int32_t s[3], t[3], zi[3], depth[3];
        uint16_t rgb[3];
        g_surf_alpha = v[0].a;
        emit_tri_state(textured);

        /* Same operation order, comparisons and rounding as the legacy loop.
         * In particular, do not use the cached-slot reciprocal for zi. */
        float zscale = (float)ZI_SCALE;
        if (textured) {
            float m = 0.0f;
            for (int k = 0; k < 3; k++) {
                float a3 = fabsf(v[k].w_inv);
                float a4 = fabsf(v[k].u * v[k].w_inv);
                float a5 = fabsf(v[k].v * v[k].w_inv);
                if (a3 > m) m = a3;
                if (a4 > m) m = a4;
                if (a5 > m) m = a5;
            }
            if (m > 0.0f) zscale = ZI_DERIVE_TARGET / m;
            if (zscale < 1.0f) zscale = 1.0f;
        }
        for (int k = 0; k < 3; k++) {
            gpu_project_clip(v[k].cx, v[k].cy, v[k].cw, &x[k], &y[k]);
            int z = (int)lrintf(v[k].w_inv * (65536.0f * zscale));
            if (z < 1) z = 1;
            zi[k] = z;
            float df = v[k].w_inv * 1073741824.0f;
            if (df < 1.0f) df = 1.0f;
            else if (df > 2147483520.0f) df = 2147483520.0f;
            depth[k] = (int32_t)lrintf(df);
            if (textured) {
                s[k] = (int32_t)lrintf(v[k].u * 65536.0f);
                t[k] = (int32_t)lrintf(v[k].v * 65536.0f);
            } else {
                s[k] = t[k] = 0;
            }
            rgb[k] = (g_vc_rgb_input < 0) ? 0xFFFF
                      : rgba_to_565(v[k].r, v[k].g, v[k].b);
        }
        of_gpu_draw_vert_tri_rgb(x, y, s, t, zi, rgb, gpu_q29_word(zi),
                                 depth, NULL);
        g_vc_words += 18u;
    }
    g_vc_tris_legacy += (uint32_t)num_tris;
    of_gpu_kick();
}

'''


def gpu_transform(s):
    return once(s, 'static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,', DIRECT + 'static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,')


def main():
    originals = {name: (BASE / name).read_bytes() for name in HASHES}
    for name, data in originals.items():
        assert sha(data) == HASHES[name], name
    changed = {
        'gfx_pc.c': pc_transform(originals['gfx_pc.c'].decode()).encode(),
        'gfx_gpu.c': gpu_transform(originals['gfx_gpu.c'].decode()).encode(),
    }
    DEST.mkdir(exist_ok=False)
    files = {}
    for name, data in changed.items():
        (DEST / name).write_bytes(data)
        (DEST / (name + '.patch')).write_text(''.join(difflib.unified_diff(
            originals[name].decode().splitlines(True), data.decode().splitlines(True),
            fromfile='a/' + name, tofile='b/' + name)))
        files[name] = {'baseline_sha256': sha(originals[name]), 'candidate_sha256': sha(data)}
    (DEST / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    record = {
        'candidate': DEST.name,
        'qualified': False,
        'files': files,
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'scope': 'Fixed-record fallback for already-cache-eligible CPU triangles only.',
        'preserved': [
            'Ordinary CPU transform, culling, clipping, lighting and fog.',
            'GPU packet opcodes, per-triangle zi scale, depth, RGB565 and projection arithmetic.',
            'Original generic VBO path for C+D, decals, translucency, alpha-mask and old GPU capabilities.',
            'Existing batch limits, ordering, flush and kick cadence.',
            'No new BSS allocation: direct records share a union with the existing VBO.',
        ],
        'required_checks': [
            'Independent eligibility/colour/alpha and batch ordering source review.',
            'Full guest capture scene equivalence and exact GPU pixel/depth replay.',
            'Whole-trace normal/diagnostic CPU modeled-cycle comparison.',
        ],
        'stage_changed': False, 'sibling_changed': False, 'card_changed': False,
    }
    (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
