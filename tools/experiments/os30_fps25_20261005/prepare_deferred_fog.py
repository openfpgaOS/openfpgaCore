#!/usr/bin/env python3
"""Freeze CPU fog-alpha deferral while the GPU ignores the fog channel."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PARENT = ROOT / 'docs/measurements/os30-fps25-20261005/baseline/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/deferred-fog-v1'
EXPECTED = '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


HELPER = '''
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define GFX_CPU_DEFERRED_FOG 1
/* This backend ignores the fog channel and substitutes opaque shade alpha
 * while use_fog is active. Preserve the original alpha lazily for any later
 * non-fog reuse. Positions/reciprocals are already owned by loaded_vertices;
 * only one pending batch and its two fog coefficients need to survive. */
static uint32_t cpu_fog_pending;
static int16_t cpu_fog_mul, cpu_fog_offset;

static __attribute__((noinline)) void gfx_cpu_materialize_fog(uint32_t needed) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);
    cpu_fog_pending &= ~needed;
    for (unsigned slot = 0; needed; ++slot, needed >>= 1) {
        if (!(needed & 1u)) continue;
        struct LoadedVertex *d = &rsp.loaded_vertices[slot];
        float wf = d->w;
        float zf = d->z;
        float winv = (wf == 0.f) ? 1.f / 0.001f : d->w_inv;
        if (winv < 0.0f) winv = 32767.0f;
        float fog_z = zf * winv * cpu_fog_mul + cpu_fog_offset;
        int fog_i = (int)fog_z;
        d->color.a = (fog_i < 0) ? 0 : (fog_i > 255) ? 255 : fog_i;
    }
}

static inline __attribute__((always_inline)) void gfx_cpu_flush_fog(uint32_t mask) {
    uint32_t needed = cpu_fog_pending & mask;
    if (needed) gfx_cpu_materialize_fog(needed);
}
#else
#define GFX_CPU_DEFERRED_FOG 0
#endif
'''


def transform(original):
    assert sha(original.encode()) == EXPECTED
    candidate = original
    edits = []

    def replace(old, new):
        nonlocal candidate
        assert candidate.count(old) == 1, old
        candidate = candidate.replace(old, new, 1)
        edits.append((old, new))

    vertex = 'static __attribute__((noinline)) void gfx_sp_vertex(size_t n_vertices, size_t dest_index, const Vtx *vertices) {'
    replace(vertex, HELPER + vertex)
    replace('''    if (n_vertices == 0)
        return;
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP''', '''    if (n_vertices == 0)
        return;
#if GFX_CPU_DEFERRED_FOG
    uint32_t overwritten = dest_index >= 32 ? 0 : n_vertices >= 32 - dest_index
        ? (0xffffffffu << dest_index) : ((1u << n_vertices) - 1u) << dest_index;
    gfx_cpu_flush_fog(~overwritten);
    cpu_fog_pending &= ~overwritten;
    const bool fog_active = configEnableFog && (rsp.geometry_mode & G_FOG);
    const bool defer_fog = fog_active && dest_index < 32 && n_vertices <= 32 - dest_index;
    const bool eager_fog = fog_active && !defer_fog;
    if (defer_fog) {
        cpu_fog_mul = rsp.fog_mul;
        cpu_fog_offset = rsp.fog_offset;
        cpu_fog_pending = overwritten;
    }
#endif
#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP''')
    replace('''        if (configEnableFog && (rsp.geometry_mode & G_FOG)) {
            float wf = w;''', '''#if GFX_CPU_DEFERRED_FOG
        if (eager_fog) {
#else
        if (configEnableFog && (rsp.geometry_mode & G_FOG)) {
#endif
            float wf = w;''')
    clip_anchor = '''#ifdef GFX_MANUAL_CLIPPING
    if (!gfx_clip_triangle(v1, v2, v3, clip_and))'''
    replace(clip_anchor, '''#if GFX_CPU_DEFERRED_FOG
    /* A non-fog draw can consume shade alpha, including through clipping.
     * Resolve original loaded vertices before the clipper creates its fan.
     * Fog draws ignore this channel in both cached and legacy GPU emission. */
    if (cpu_fog_pending && !(configEnableFog && (rdp.other_mode_l >> 30) == G_BL_CLR_FOG)) {
        uint32_t mask = (vtx1_idx < 32 ? 1u << vtx1_idx : 0)
                      | (vtx2_idx < 32 ? 1u << vtx2_idx : 0)
                      | (vtx3_idx < 32 ? 1u << vtx3_idx : 0);
        gfx_cpu_flush_fog(mask);
    }
#endif

''' + clip_anchor)
    # No frame/config flush is necessary: every dependency of delayed alpha
    # is stored in the vertex or in the saved pair. Later G_VTX preserves any
    # surviving old slots before replacing either the pair or coordinates.
    restored = candidate
    for old, new in reversed(edits):
        assert restored.count(new) == 1
        restored = restored.replace(new, old, 1)
    assert restored == original
    return candidate


def main():
    original = PARENT.read_text()
    candidate = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / 'gfx_pc.c'
    assert not dest.exists() or dest.read_text() == candidate
    dest.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'qualified': False,
        'reverse_source_exact': True,
        'scope': 'Defer fog alpha only for CPU-loaded slots 0..31 while preserving exact fog coefficients and clip-space inputs for later non-fog reuse. No source pointers, position caches or immutable-asset metadata.',
        'lifetime': [
            'Each G_VTX materializes pending slots that survive its overwrite, discards overwritten pending slots, then optionally installs the new batch and saved signed fog coefficients.',
            'Delayed alpha uses only d->z/w/w_inv and the saved fog_mul/offset. All other RSP state may change freely, and no mutable source pointer is retained.',
            'Every surviving non-fog triangle resolves its loaded vertices before clipping, so clipping interpolates original alpha whenever the backend can consume it.',
            'Fogged triangles may interpolate placeholder alpha, but both GPU submission paths ignore fog intensity and force shade alpha opaque. Their generated scratch vertices are consumed only by the same draw and never retained.',
            'Scratch rectangles occupy slots 64..67 and do not overwrite the pending range; mixed indices are guarded before shifts.',
            'Worker-enabled, desktop and out-of-range vertex loads preserve the eager path. The general fog formulas and all geometry, lighting, UV and clip work remain unchanged.',
        ],
        'remaining': 'Independent consumer/lifetime review, target build arithmetic audit, paired scene captures and exact GPU color/depth replays. No speedup claimed.'
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
