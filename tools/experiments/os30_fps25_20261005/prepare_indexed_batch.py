#!/usr/bin/env python3
"""Prepare a private known-index CPU batch; no shared build/stage changes."""
import difflib
import hashlib
import json
from pathlib import Path
from prepare_lighting_layout import ROOT, OUT, BASE, HASHES

DEST = OUT / 'indexed-batch-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

HELPER = r'''
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
/* Return the original clip/cull rejection class, without counters or draws.
 * The caller commits counters only after accepting the WHOLE TRI command. */
static inline int gfx_cpu_batch_reject(const struct LoadedVertex *v1,
                                      const struct LoadedVertex *v2,
                                      const struct LoadedVertex *v3) {
    if (v1->clip_rej & v2->clip_rej & v3->clip_rej) return 1;
    if ((rsp.geometry_mode & G_CULL_BOTH) != 0) {
        rspv_t w1_inv = v1->w_inv;
        rspv_t w2_inv = v2->w_inv;
        rspv_t w3_inv = v3->w_inv;
        rspv_t dx1 = RSPV_MUL(v1->x, w1_inv) - RSPV_MUL(v2->x, w2_inv);
        rspv_t dy1 = RSPV_MUL(v1->y, w1_inv) - RSPV_MUL(v2->y, w2_inv);
        rspv_t dx2 = RSPV_MUL(v3->x, w3_inv) - RSPV_MUL(v2->x, w2_inv);
        rspv_t dy2 = RSPV_MUL(v3->y, w3_inv) - RSPV_MUL(v2->y, w2_inv);
        float cross = dx1 * dy2 - dy1 * dx2;
        if ((v1->w < 0) ^ (v2->w < 0) ^ (v3->w < 0)) cross = -cross;
        switch (rsp.geometry_mode & G_CULL_BOTH) {
            case G_CULL_FRONT: if (cross <= 0.0f) return 2; break;
            case G_CULL_BACK: if (cross >= 0.0f) return 2; break;
            case G_CULL_BOTH: return 2;
        }
    }
    return 0;
}

/* A run ends at EVERY non-triangle command, including G_VTX. Loaded slots,
 * material, viewport, texture and alpha are therefore immutable throughout.
 * Store the ORIGINAL per-triangle dirty masks, not one reordered union of
 * uploads: backend packets remain load[0],load[1],load[2],draw for each tri. */
static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(Gfx *cmd) {
    SM64_PERF_SCOPE(SM64_PERF_TRI);
    struct gfx_vc_vtx payload[32];
    uint32_t jobs[64];
    unsigned count = 0, consumed = 0;
    uint32_t stale = 0, prepared = 0;
    struct ColorCombiner *comb = NULL;
    int textured = 0, rgb_in = -1;
    uint8_t alpha = 255;
    float uofs = 0.0f, vofs = 0.0f, bias = 0.0f;
    for (;;) {
        unsigned slot[6], reject[2], n = gfx_geometry_decode(cmd, slot);
        if (!n || consumed + n > 64) break;
        unsigned live = 0;
        for (unsigned t = 0; t < n; ++t) {
            unsigned *s = &slot[3*t];
            if ((s[0] | s[1] | s[2]) >= 32) goto submit;
            const struct LoadedVertex *v1 = &rsp.loaded_vertices[s[0]];
            const struct LoadedVertex *v2 = &rsp.loaded_vertices[s[1]];
            const struct LoadedVertex *v3 = &rsp.loaded_vertices[s[2]];
            reject[t] = gfx_cpu_batch_reject(v1, v2, v3);
            if (reject[t]) continue;
            if (v1->clip_rej | v2->clip_rej | v3->clip_rej) goto submit;
            const float w1 = RSPV_TO_FLOAT(v1->w);
            const float w2 = RSPV_TO_FLOAT(v2->w);
            const float w3 = RSPV_TO_FLOAT(v3->w);
            if (!(w1 > 0.0f && w1 < 32767.0f &&
                  w2 > 0.0f && w2 < 32767.0f &&
                  w3 > 0.0f && w3 < 32767.0f) ||
                (w1 == w2 && w1 == w3)) goto submit;
            ++live;
        }
        if (live && !comb) {
            bool use_fog, use_alpha, use_texture;
            comb = gfx_prepare_triangle_material(&use_fog, &use_alpha, &use_texture);
            const int elig = triangle_vc_elig;
            rgb_in = triangle_vc_rgb_input;
            const int alpha_in = triangle_vc_alpha_input;
            if (!elig || (rgb_in >= 0 && comb->shader_input_mapping[0][rgb_in] == CC_LOD))
                goto submit;
            if (alpha_in >= 0) {
                const uint8_t source = comb->shader_input_mapping[1][alpha_in];
                if (source == CC_LOD || (source == CC_SHADE && !use_fog)) goto submit;
                if (source != CC_SHADE) {
                    struct RGBA tmp;
                    const struct LoadedVertex *v = &rsp.loaded_vertices[slot[0]];
                    alpha = gfx_vc_resolve(source, v, v, &tmp)->a;
                }
            }
            textured = elig & GFX_VC_TEXTURED;
            uofs = textured ? (float)(rdp.texture_tile.uls * 8) : 0.0f;
            vofs = textured ? (float)(rdp.texture_tile.ult * 8) : 0.0f;
            bias = (textured &&
                (rdp.other_mode_h & (3U << G_MDSFT_TEXTFILT)) != G_TF_POINT) ? 0.5f : 0.0f;
            gfx_vc_texture_state(comb, textured, bias);
            stale = vc_slot_stale;
        }
        for (unsigned t = 0; t < n; ++t) {
#ifdef TARGET_POCKET
            tri_diag.total++;
            if (reject[t] == 1) tri_diag.clip_reject++;
            else if (reject[t] == 2) tri_diag.cull_reject++;
            else tri_diag.direct++;
#endif
            if (reject[t]) continue;
            unsigned *s = &slot[3*t];
            unsigned dirty = ((stale >> s[0]) & 1u) |
                             (((stale >> s[1]) & 1u) << 1) |
                             (((stale >> s[2]) & 1u) << 2);
            for (unsigned i = 0; i < 3; ++i) {
                const uint32_t bit = 1u << s[i];
                if (!(dirty & (1u << i)) || (prepared & bit)) continue;
                const struct LoadedVertex *v = &rsp.loaded_vertices[s[i]];
                struct gfx_vc_vtx *d = &payload[s[i]];
                d->cx = RSPV_TO_FLOAT(v->x);
                d->cy = RSPV_TO_FLOAT(v->y);
                d->cw = RSPV_TO_FLOAT(v->w);
                d->w_inv = RSPV_TO_FLOAT(v->w_inv);
                d->use_exact_depth = 0;
                if (textured) {
                    d->u = (RSPV_TO_FLOAT(v->u) - uofs) / 32.0f + bias;
                    d->v = (RSPV_TO_FLOAT(v->v) - vofs) / 32.0f + bias;
                } else d->u = d->v = 0.0f;
                if (rgb_in >= 0) {
                    struct RGBA tmp;
                    const struct RGBA *c = gfx_vc_resolve(comb->shader_input_mapping[0][rgb_in], v, v, &tmp);
                    d->r = c->r; d->g = c->g; d->b = c->b;
                } else d->r = d->g = d->b = 255;
                d->a = alpha;
                prepared |= bit;
            }
            jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15);
            stale &= ~((1u << s[0]) | (1u << s[1]) | (1u << s[2]));
        }
        consumed += n;
        ++cmd;
    }
submit:
    if (count) {
        if (buf_vbo_len) gfx_flush();
        gfx_gpu_vtx_cache_batch(payload, jobs, count, alpha);
        vc_slot_stale = stale;
    }
    return cmd;
}
#endif
'''


def gpu_transform(source):
    start = source.index('void gfx_gpu_vtx_cache_tri(')
    end = source.index('\n/* Raw geometry path.', start)
    old = source[start:end]
    pre, loop = old.split('#ifndef OF_PC\n    /* One reservation', 1)
    pre = pre.replace('void gfx_gpu_vtx_cache_tri(const struct gfx_vc_vtx v[3], const uint8_t slot[3],\n                           unsigned dirty_mask)',
        'void gfx_gpu_vtx_cache_batch(const struct gfx_vc_vtx vertices[32],\n                             const uint32_t *jobs, unsigned count, uint8_t alpha)')
    assert pre.startswith('void gfx_gpu_vtx_cache_batch(')
    pre = pre.replace('g_surf_alpha = v[0].a;', 'g_surf_alpha = alpha;')
    loop = '#ifndef OF_PC\n    /* One reservation' + loop
    assert loop.endswith('\n}\n')
    loop = loop[:-3]
    anchor = '        if (!(dirty_mask & (1u << k)))\n            continue;\n'
    assert loop.count(anchor) == 1
    loop = loop.replace(anchor, anchor + '        const struct gfx_vc_vtx *vtx = &vertices[slot[k]];\n')
    loop = loop.replace('v[k].', 'vtx->')
    batch = pre + '''    /* No state mutation occurs inside this bounded CPU-only run. */
    for (unsigned tri = 0; tri < count; ++tri) {
        const uint32_t job = jobs[tri];
        const uint8_t slot[3] = {job & 31u, (job >> 5) & 31u, (job >> 10) & 31u};
        const unsigned dirty_mask = (job >> 15) & 7u;
''' + loop + '\n    }\n}\n'
    return source[:end] + '\n/* Known-index CPU run: same per-triangle packet arithmetic and order. */\n' + batch + source[end:]


def pc_transform(source):
    anchor = '\n#define C0(pos, width)'
    assert source.count(anchor) == 1
    source = source.replace(anchor, '\n' + HELPER + anchor, 1)
    for tag in ('G_TRI1', 'G_TRI2'):
        anchor = f'            case (uint8_t){tag}:\n'
        assert source.count(anchor) == 1
        source = source.replace(anchor, anchor + '''#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
                {
                    Gfx *next = gfx_cpu_indexed_run(cmd);
                    if (next != cmd) { cmd = next; continue; }
                }
#endif
''', 1)
    return source


def main():
    assert not DEST.exists(), DEST
    for name, expected in HASHES.items(): assert sha(BASE / name) == expected, name
    originals = {name: (BASE / name).read_text() for name in HASHES}
    sources = dict(originals)
    sources['gfx_pc.c'] = pc_transform(originals['gfx_pc.c'])
    sources['gfx_gpu.c'] = gpu_transform(originals['gfx_gpu.c'])
    anchor = 'int gfx_gpu_geometry_enabled(void);'
    sources['gfx_gpu.h'] = originals['gfx_gpu.h'].replace(anchor, '''/* CPU-only unchanged-state run. Each job packs slots at bits0/5/10 and
 * its ORIGINAL per-corner dirty mask at bit15. Only dirty payloads are read.
 * Caller keeps material, viewport, alpha and all32slots fixed during the run. */
void gfx_gpu_vtx_cache_batch(const struct gfx_vc_vtx vertices[32],
                             const uint32_t *jobs, unsigned count, uint8_t alpha);
''' + anchor, 1)
    assert sources['gfx_gpu.h'] != originals['gfx_gpu.h']
    DEST.mkdir()
    for name, source in sources.items(): (DEST / name).write_text(source)
    (DEST / 'prepare.py').write_bytes(Path(__file__).read_bytes())
    (DEST / 'change.patch').write_text(''.join(''.join(difflib.unified_diff(
        originals[name].splitlines(True), sources[name].splitlines(True),
        fromfile='baseline/' + name, tofile='indexed-batch-v1/' + name))
        for name in HASHES if originals[name] != sources[name]))
    manifest = dict(candidate='indexed-batch-v1', parent='render-v6', qualified=False,
        runtime_qualified=False, parent_files=HASHES,
        source_files={name:sha(DEST/name) for name in HASHES}, generator_sha256=sha(Path(__file__)),
        change='Bounded known-index CPU triangle batching. Hoist material/alpha/UV and backend sticky state setup; preserve each triangle original dirty-load order and indexed draw.',
        limits=['CPU geometry only; original worker and generic fallback handlers retained.',
                'At most64input triangles; stops before every nontriangle command and whole TRI command requiring clipping, unstable indices, inadmissible/equalw, LOD or non-fog SHADE alpha.',
                'Same original CPU culling expression, UV expression and GPU quantization loop. No projection, texture, gameplay, audio, quality or RTL changes.',
                'One1152byte payload table plus256byte job array per active run. No persistent new cache or invalidation protocol.',
                'GPU uploads remain pertriangle in original corner order, even duplicate slot indices; no union-upload reordering. Original ring reserve and32draw kick cadence retained.'],
        proof_status='Source candidate only; independent review, codegen, paired scene/cycle capture and exact GPU replay required.',
        risks=['Batch scanning and local table indexing may erase savings, especially short runs.',
               'Same source floating expressions still require target packet verification because compiler scheduling/contraction can differ.',
               'Deferred emission shifts command arrival timing within a bounded run; no actual hardware FPS or unchanged overlap claim.'])
    (DEST/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'candidate':str(DEST),'source_files':manifest['source_files'],'qualified':False},indent=2))

if __name__ == '__main__': main()
