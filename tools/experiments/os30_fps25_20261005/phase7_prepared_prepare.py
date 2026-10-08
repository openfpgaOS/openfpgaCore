#!/usr/bin/env python3
"""Create the isolated prepared-record experiment from the bound F8 files."""
from pathlib import Path
import difflib
import hashlib
import json

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase7'
BASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase6/candidate'
OUT = PHASE / 'prepared-v2'

HELPER = '''/* A prepared slot has exactly the seven immutable load payload words.
 * Preserve the cached path's float operations and rounding before storing;
 * projection uses x/y/w scaled by 1/256, but depth uses the true reciprocal.
 * This bounded run never supplies an externally computed exact depth. */
static inline void gfx_cpu_prepare_clip(struct gfx_vc_prepared *d,
                                       const struct LoadedVertex *v,
                                       float u, float tex_v, uint16_t rgb) {
    float df = RSPV_TO_FLOAT(v->w_inv) * 1073741824.0f;
    if (df < 1.0f) df = 1.0f;
    else if (df > 2147483520.0f) df = 2147483520.0f;
    int32_t qx = (int32_t)lrintf(RSPV_TO_FLOAT(v->x) * 256.0f);
    int32_t qy = (int32_t)lrintf(RSPV_TO_FLOAT(v->y) * 256.0f);
    int32_t qw = (int32_t)lrintf(RSPV_TO_FLOAT(v->w) * 256.0f);
    int32_t qu = (int32_t)lrintf(u * 65536.0f);
    int32_t qv = (int32_t)lrintf(tex_v * 65536.0f);
    uint32_t qd = (uint32_t)lrintf(df);
#if defined(__riscv)
    /* Keep the existing conversion scheduling barrier; no changed rounding. */
    __asm__ volatile ("" : "+r"(qx), "+r"(qy), "+r"(qw),
                          "+r"(qu), "+r"(qv), "+r"(qd));
#endif
    d->x = qx; d->y = qy; d->w = qw;
    d->u = qu; d->v = qv; d->rgb = rgb; d->depth = qd;
}

'''

def transform(name, text):
    if name == 'gfx_gpu.h':
        marker = '/* CPU-only unchanged-state run.'
        text = text.replace(marker, '''/* GPU-ready words after the load command header and slot number.
 * Only the bounded CPU run uses these; clipped/direct records stay floats. */
struct gfx_vc_prepared {
    int32_t x, y, w, u, v;
    uint32_t rgb, depth;
};

''' + marker, 1)
        text = text.replace('const struct gfx_vc_vtx vertices[32]',
                            'const struct gfx_vc_prepared vertices[32]')
        return text
    if name == 'gfx_pc.c':
        marker = '/* A run ends at EVERY non-triangle command, including G_VTX.'
        text = text.replace(marker, HELPER + marker, 1)
        text = text.replace('struct gfx_vc_vtx payload[32];',
                            'struct gfx_vc_prepared payload[32];', 1)
        start = text.index('                struct gfx_vc_vtx *d = &payload[s[i]];')
        end = text.index('                prepared |= bit;', start)
        text = text[:start] + '''                float u = 0.0f, tex_v = 0.0f;
                if (textured) {
                    u = (RSPV_TO_FLOAT(v->u) - uofs) / 32.0f + bias;
                    tex_v = (RSPV_TO_FLOAT(v->v) - vofs) / 32.0f + bias;
                }
                /* Preserve the float-record boundary before Q16 conversion:
                 * fast-math must not fuse/reassociate UV scale and rounding. */
#if defined(__riscv)
                __asm__ volatile ("" : "+f"(u), "+f"(tex_v));
#endif
                uint16_t rgb = 0xffff;
                if (rgb_in >= 0) {
                    struct RGBA tmp;
                    const struct RGBA *c = gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in], v, v, &tmp);
                    rgb = ((uint16_t)(c->r >> 3) << 11) |
                          ((uint16_t)(c->g >> 2) << 5) | (c->b >> 3);
                }
                gfx_cpu_prepare_clip(&payload[s[i]], v, u, tex_v, rgb);
''' + text[end:]
        return text
    assert name == 'gfx_gpu.c'
    start = text.index('void gfx_gpu_vtx_cache_batch(')
    end = text.index('\n/* Raw geometry path.', start)
    batch = text[start:end].replace('const struct gfx_vc_vtx vertices[32]',
                                    'const struct gfx_vc_prepared vertices[32]')
    lo = batch.index('    /* w PRE-SCALE')
    hi = batch.index('\n#ifndef OF_PC\n    {\n        *packet++ = ((uint32_t)GPU_CMD_DRAW_INDEXED_TRI', lo)
    batch = batch[:lo] + '''    /* The front end has quantized each unique dirty slot once. Keep the
     * original per-corner dirty mask: duplicate slots still produce duplicate
     * load commands in precisely the same place in the stream. */
    for (int k = 0; k < 3; k++) {
        if (!(dirty_mask & (1u << k))) continue;
        const struct gfx_vc_prepared *vtx = &vertices[slot[k]];
#ifndef OF_PC
        *packet++ = ((uint32_t)GPU_CMD_LOAD_VERT_CLIP << 24) | 8u;
        *packet++ = (uint32_t)(slot[k] & 0x1fu);
        *packet++ = (uint32_t)vtx->x;
        *packet++ = (uint32_t)vtx->y;
        *packet++ = (uint32_t)vtx->w;
        *packet++ = (uint32_t)vtx->u;
        *packet++ = (uint32_t)vtx->v;
        *packet++ = vtx->rgb;
        *packet++ = vtx->depth;
#else
        of_gpu_load_vert_clip(slot[k], vtx->x, vtx->y, vtx->w,
                              vtx->u, vtx->v, (uint16_t)vtx->rgb, vtx->depth);
#endif
        g_vc_words += 9;
        g_vc_loads++;
    }
''' + batch[hi:]
    return text[:start] + batch + text[end:]

def main():
    binding = json.loads((PHASE / 'baseline.json').read_text())
    diffs = []
    for name in ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h'):
        source = BASE / name
        expected = binding['source_tree_sha256'][binding['inputs'][name]]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, name
        before = source.read_text()
        after = transform(name, before)
        target = OUT / name
        if target.read_text() != before:
            assert target.read_text() == after, 'refusing to replace other edits: ' + name
        target.write_text(after)
        diffs.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       'F8/' + name, 'prepared-v2/' + name))
    (OUT / 'implementation.patch').write_text(''.join(diffs))
    print('Prepared compact records in', OUT)

if __name__ == '__main__':
    main()
