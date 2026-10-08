#!/usr/bin/env python3
"""Prepare a private RGB565 resident-texture candidate; no builds/stage edits."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CAMPAIGN = ROOT/'docs/measurements/os30-fps25-20261005'
BASE = CAMPAIGN/'phase2/exact-v1'
OUT = CAMPAIGN/'phase4/texmem-app'
FILES = ['gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h',
         'perf_pocket.c','perf_pocket.h','Makefile.sm64']

HELPERS = r'''
/* ---- Dedicated RGB565 texture storage, with permanent SDRAM fallback ----
 * Every texture keeps its original SDRAM copy. The fetch route is global,
 * so it stays fast for the whole application until allocation exhausts the
 * bounded arena; then one drain switches ALL subsequent draws to SDRAM.
 * Per-generation ownership remains gpu_tex_slot_begin's existing contract. */
static uint32_t g_fast_tex_top, g_fast_tex_size;
static int g_fast_tex_active;

static void gpu_fast_tex_fallback(void) {
    if (!g_fast_tex_active) return;
    /* This can run inside a lazy alpha-mask creation. Already emitted draws
     * must retire; remaining CPU-side triangles select SDRAM at emission. */
    of_gpu_finish();
    _of_gpu_route_fast_tex(0);
    g_fast_tex_active = 0;
    g_white_addr = (uint32_t)(uintptr_t)g_white_tex;
    g_st_cache_valid = 0;
    gpu_material_changed();
}

static int gpu_fast_tex_upload(uint32_t *address, uint32_t *capacity,
                               const void *source, uint32_t bytes) {
    if (!g_fast_tex_active) return 0;
    if (bytes == 0 || bytes > UINT32_MAX - 15u) {
        gpu_fast_tex_fallback();
        return 0;
    }
    const uint32_t rounded = (bytes + 15u) & ~15u;
    if (*capacity < rounded) {
        if (rounded > g_fast_tex_size - g_fast_tex_top) {
            gpu_fast_tex_fallback();
            return 0;
        }
        *address = g_fast_tex_top;
        *capacity = rounded;
        g_fast_tex_top += rounded;
    }
    /* CPU texel conversion writes the uncached SDRAM alias. Its cached alias
     * may still hold old clean data, so upload MUST read the uncached copy.
     * Slot allocation owns64-byte padding; ceil(bytes/4) is inside that object.
     * The polled upload drains its last word before any new draw can use it.
     * Disjoint live textures may continue rendering during this upload. */
    _of_gpu_fast_tex_upload(*address,
        (const uint32_t *)of_uncached(source), (bytes + 3u) >> 2);
    return 1;
}

static void gpu_fast_tex_init(const struct of_capabilities *caps) {
    g_fast_tex_active = 0;
    g_fast_tex_top = g_fast_tex_size = 0;
#ifndef OF_PC
    if (!g_truecolor || !caps || caps->version < 3 ||
        !(caps->hw_features & OF_HW_GPU_FAST_TEX) ||
        caps->tex_fast_size < sizeof(g_white_tex) ||
        caps->tex_fast_size > 0x01000000u)
        return;
    g_fast_tex_size = caps->tex_fast_size & ~15u;
    g_fast_tex_active = 1;
    uint32_t address = 0, capacity = 0;
    /* Boot has no queued draw; upload the permanent white texel before routing
     * untextured triangles and colored rectangles to the dedicated chip. */
    if (gpu_fast_tex_upload(&address, &capacity, g_white_tex, sizeof(g_white_tex))) {
        g_white_addr = address;
        _of_gpu_route_fast_tex(1);
        g_st_cache_valid = 0;
        gpu_material_changed();
    }
#else
    (void)caps;
#endif
}

static uint32_t gpu_tex_active_address(const struct GTex *t, int alpha_mask) {
    if (g_fast_tex_active)
        return alpha_mask ? t->fast_mask_addr[t->cur] : t->fast_addr[t->cur];
    return alpha_mask ? (uint32_t)(uintptr_t)t->alpha_mask[t->cur] : t->addr;
}

static void gpu_tex_clear_epochs(void) {
    /* g_tex_count is an ID sequence, not an array bound, and may wrap. */
    for (uint32_t i = 0; i < MAX_TEX; i++)
        g_tex[i].slot_epoch[0] = g_tex[i].slot_epoch[1] = 0;
}
/* ---- End resident-texture helpers ---- */
'''


def replace_once(source, old, new):
    assert source.count(old) == 1, old[:90]
    return source.replace(old, new)


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    for name in FILES:
        (OUT/name).write_bytes((BASE/name).read_bytes())
    old = (BASE/'gfx_gpu.c').read_text()
    s = replace_once(old,
        '    uint8_t   cms, cmt;     /* N64 wrap mode (G_TX_MIRROR=1 / G_TX_CLAMP=2) per axis */',
        '    uint8_t   cms, cmt;     /* N64 wrap mode (G_TX_MIRROR=1 / G_TX_CLAMP=2) per axis */\n'
        '    /* Keep the original hot fields at their established offsets. */\n'
        '    uint32_t  fast_addr[2], fast_capacity[2];\n'
        '    uint32_t  fast_mask_addr[2], fast_mask_capacity[2];')
    marker = '/* ================================================================\n * Boot: palette, colormap, ramp, z-buffer, first draw buffer'
    s = replace_once(s, marker, HELPERS+'\n'+marker)
    s = replace_once(s, '        of_gpu_init();',
        '        of_gpu_init();\n'
        '        /* The route register survives GPU soft reset. Start in SDRAM\n'
        '         * even if a prior application left the fast domain selected. */\n'
        '        of_gpu_finish();\n'
        '        _of_gpu_route_fast_tex(0);')
    s = replace_once(s, '                g_white_addr = (uint32_t)(uintptr_t)g_white_tex;',
        '                g_white_addr = (uint32_t)(uintptr_t)g_white_tex;\n'
        '                gpu_fast_tex_init(caps);')
    epoch_loop = ('        for (uint32_t i = 0; i < g_tex_count; i++)\n'
                  '            g_tex[i].slot_epoch[0] = g_tex[i].slot_epoch[1] = 0;')
    assert s.count(epoch_loop) == 2
    s = s.replace(epoch_loop, '        gpu_tex_clear_epochs();')
    s = replace_once(s, '    t->alpha_mask[t->cur] = p;\n    gpu_tex_publish();',
        '    t->alpha_mask[t->cur] = p;\n'
        '    gpu_fast_tex_upload(&t->fast_mask_addr[t->cur],\n'
        '                        &t->fast_mask_capacity[t->cur], p, n * 2u);\n'
        '    gpu_tex_publish();')
    s = replace_once(s, '    t->addr = (uint32_t)(uintptr_t)t->ci8;\n    gpu_tex_publish();',
        '    t->addr = (uint32_t)(uintptr_t)t->ci8;\n'
        '    gpu_fast_tex_upload(&t->fast_addr[t->cur], &t->fast_capacity[t->cur],\n'
        '                        t->ci8, (uint32_t)width * (uint32_t)height * g_fb_bpp);\n'
        '    gpu_tex_publish();')
    s = replace_once(s, '            st.tex_addr  = g_alpha_mask ? (uint32_t)(uintptr_t)t->alpha_mask[t->cur] : t->addr;',
        '            st.tex_addr  = gpu_tex_active_address(t, g_alpha_mask);')
    s = replace_once(s, '    if (g_has_gpu) of_gpu_shutdown();',
        '    if (g_has_gpu) {\n'
        '        of_gpu_shutdown(); /* drains all draws before restoring route */\n'
        '        _of_gpu_route_fast_tex(0);\n'
        '        g_fast_tex_active = 0;\n'
        '    }')
    (OUT/'gfx_gpu.c').write_text(s)
    (OUT/'change.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), s.splitlines(True), fromfile='exact-v1/gfx_gpu.c', tofile='texmem-app/gfx_gpu.c')))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    manifest = dict(candidate='texmem-app', baseline='phase2/exact-v1',
                    production_build_performed=False, runtime_qualified=False,
                    source_sha256={n:sha(OUT/n) for n in FILES},
                    baseline_sha256={n:sha(BASE/n) for n in FILES},
                    prepare_sha256=sha(Path(__file__)),
                    contract=['RGB565 only; capability bounded to16MiB addressable chip.',
                              'Retains SDRAM copies and two-generation reuse epochs.',
                              'Global fast route; on exhaustion one drain switches permanently toSDRAM.',
                              'Each upload polls completion and reads original uncached SDRAM copy.',
                              'Current software adds no RTL and requires concurrent upload/fill qualification.'])
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__ == '__main__': main()
