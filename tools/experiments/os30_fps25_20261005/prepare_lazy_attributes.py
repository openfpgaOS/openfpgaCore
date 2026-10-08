#!/usr/bin/env python3
"""Freeze exact CPU attribute deferral after triangle rejection, from simple-v4."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PARENT = ROOT / 'docs/measurements/os30-simple-20261005/simple-v4/gfx_pc.c'
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/lazy-attributes-v1'
EXPECTED = '9226056cd448aa70a7ee0b558bf6b351a5e1bb0c7ce3be918ab2b089744338d0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(original):
    assert sha(original.encode()) == EXPECTED
    start = original.index('static __attribute__((noinline)) void gfx_sp_vertex(')
    end = original.index('\nstatic inline struct ColorCombiner *gfx_pick_combiner', start)
    vertex = original[start:end]
    attr_start = vertex.index('        short U = v->tc[0]')
    attr_end = vertex.index('\n        // trivial clip rejection', attr_start)
    fog_start = vertex.index('        if (configEnableFog && (rsp.geometry_mode & G_FOG))', attr_end)
    fog_end = vertex.index('\n    }\n    }\n#ifdef TARGET_POCKET', fog_start)
    attributes = vertex[attr_start:attr_end]
    fog = vertex[fog_start:fog_end]
    loop_start = vertex.index('    for (size_t i = 0; i < n_vertices; i++, dest_index++)')
    geometry = vertex[loop_start:attr_start] + vertex[attr_end:fog_start] + '    }\n'
    # v/vn declarations are retained: the positions and reciprocal expressions
    # are copied literally, including FMA contraction boundaries.
    helper = '''
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP \\
    && !defined(OF_PC) && !defined(OF_DBG_FACE)
#define GFX_CPU_LAZY_ATTRIBUTES 1
/* A single immutable source batch owns pending attributes. All RSP state
 * changes flush them first, and a later G_VTX discards overwritten slots.
 * Clip coordinates/reciprocals stay eager, so rejection uses the original
 * arithmetic and no uninitialized attribute can reach the clipper. */
static uint32_t cpu_attributes_pending;
static const Vtx *cpu_attributes_source;
static unsigned cpu_attributes_first;

static __attribute__((noinline)) void gfx_cpu_load_attributes(uint32_t needed) {
    SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);
    cpu_attributes_pending &= ~needed;
    while (needed) {
        unsigned slot = (unsigned)__builtin_ctz(needed);
        needed &= needed - 1;
        const Vtx *source = &cpu_attributes_source[slot - cpu_attributes_first];
        const Vtx_t *v = &source->v;
        const Vtx_tn *vn = &source->n;
        struct LoadedVertex *d = &rsp.loaded_vertices[slot];
        const float z = d->z, w = d->w;
''' + attributes + '\n' + fog + '''
    }
}

static inline __attribute__((always_inline)) void gfx_cpu_flush_attributes(uint32_t mask) {
    uint32_t needed = cpu_attributes_pending & mask;
    if (needed) gfx_cpu_load_attributes(needed);
}

static int gfx_cpu_can_defer(const Vtx *vertices, size_t count, size_t first) {
    if (first >= 32 || count > 32 - first) return 0;
    uintptr_t address = (uintptr_t)vertices;
    uintptr_t end = (uintptr_t)__EH_FRAME_BEGIN__;
    return address >= (uintptr_t)&_start && address < end
        && count <= (end - address) / sizeof(Vtx);
}
#else
#define GFX_CPU_LAZY_ATTRIBUTES 0
#endif
'''
    vertex = vertex.replace('    if (n_vertices == 0)\n        return;\n', '''    if (n_vertices == 0)
        return;
#if GFX_CPU_LAZY_ATTRIBUTES
    uint32_t cpu_overwritten = dest_index >= 32 ? 0 : n_vertices >= 32 - dest_index
        ? (0xffffffffu << dest_index) : ((1u << n_vertices) - 1u) << dest_index;
    gfx_cpu_flush_attributes(~cpu_overwritten);
    cpu_attributes_pending &= ~cpu_overwritten;
    const int cpu_defer = gfx_cpu_can_defer(vertices, n_vertices, dest_index);
#endif
''', 1)
    fast_loop = '''#if GFX_CPU_LAZY_ATTRIBUTES
    if (cpu_defer) {
        cpu_attributes_source = vertices;
        cpu_attributes_first = dest_index;
        cpu_attributes_pending = cpu_overwritten;
''' + geometry + '''        goto cpu_vertex_complete;
    }
#endif
'''
    anchor = '    for (size_t i = 0; i < n_vertices; i++, dest_index++)'
    assert vertex.count(anchor) == 1
    vertex = vertex.replace(anchor, fast_loop + anchor, 1)
    closing = '\n    }\n    }\n#ifdef TARGET_POCKET'
    assert vertex.count(closing) == 1
    vertex = vertex.replace(closing, '''
    }
    }
#if GFX_CPU_LAZY_ATTRIBUTES
cpu_vertex_complete:
#endif
#ifdef TARGET_POCKET''', 1)
    candidate = original[:start] + helper + vertex + original[end:]

    # Materialize only after the unchanged trivial and backface rejection.
    tri_anchor = '''#ifdef GFX_MANUAL_CLIPPING
    if (!gfx_clip_triangle(v1, v2, v3, clip_and))'''
    tri_insert = '''#if GFX_CPU_LAZY_ATTRIBUTES
    if (cpu_attributes_pending) {
        uint32_t mask = (vtx1_idx < 32 ? 1u << vtx1_idx : 0)
                      | (vtx2_idx < 32 ? 1u << vtx2_idx : 0)
                      | (vtx3_idx < 32 ? 1u << vtx3_idx : 0);
        gfx_cpu_flush_attributes(mask);
    }
#endif

'''
    assert candidate.count(tri_anchor) == 1
    candidate = candidate.replace(tri_anchor, tri_insert + tri_anchor, 1)

    dl_anchor = '        if(material_opcode)triangle_material_ready=false;\n#if GFX_GPU_GEOMETRY_WORKER_APP\n'
    dl_insert = '''#if GFX_CPU_LAZY_ATTRIBUTES
        if(cpu_attributes_pending && (opcode==(uint8_t)G_MTX || opcode==(uint8_t)G_POPMTX
           || opcode==(uint8_t)G_MOVEMEM || opcode==(uint8_t)G_MOVEWORD
           || opcode==(uint8_t)G_TEXTURE
#ifdef F3DEX_GBI_2
           || opcode==(uint8_t)G_GEOMETRYMODE
#else
           || opcode==(uint8_t)G_SETGEOMETRYMODE || opcode==(uint8_t)G_CLEARGEOMETRYMODE
#endif
           ))gfx_cpu_flush_attributes(0xffffffffu);
#endif
'''
    assert candidate.count(dl_anchor) == 1
    candidate = candidate.replace(dl_anchor, dl_insert + dl_anchor, 1)
    reset_anchor = 'static void gfx_sp_reset() {\n'
    reset_insert = '''#if GFX_CPU_LAZY_ATTRIBUTES
    gfx_cpu_flush_attributes(0xffffffffu);
#endif
'''
    assert candidate.count(reset_anchor) == 1
    candidate = candidate.replace(reset_anchor, reset_anchor + reset_insert, 1)
    end_anchor = '    { SM64_PERF_SCOPE(SM64_PERF_DL); gfx_run_dl(commands); }\n'
    end_insert = '''#if GFX_CPU_LAZY_ATTRIBUTES
    /* Complete the externally visible RSP state before settings can change
     * between frames. Usually only unused vertices in the last batch remain. */
    gfx_cpu_flush_attributes(0xffffffffu);
#endif
'''
    assert candidate.count(end_anchor) == 1
    candidate = candidate.replace(end_anchor, end_anchor + end_insert, 1)

    # Reverse every edit to prove the ordinary eager path remained literal.
    restored = candidate.replace(helper, '', 1)
    restored = restored.replace(vertex, original[start:end], 1)
    restored = restored.replace(tri_insert, '', 1)
    restored = restored.replace(dl_insert, '', 1)
    restored = restored.replace(reset_anchor + reset_insert, reset_anchor, 1)
    restored = restored.replace(end_anchor + end_insert, end_anchor, 1)
    assert restored == original
    return candidate, attributes, fog, geometry


def main():
    original = PARENT.read_text()
    candidate, attributes, fog, geometry = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / 'gfx_pc.c'
    assert not dest.exists() or dest.read_text() == candidate
    dest.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {
        'parent': str(PARENT.relative_to(ROOT)),
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'qualified': False,
        'scope': 'CPU-only immutable G_VTX attribute deferral after original trivial clip and backface rejection; positions, clip flags and reciprocal remain eager.',
        'literal_block_sha256': {'attributes': sha(attributes.encode()), 'fog': sha(fog.encode()), 'geometry_loop': sha(geometry.encode())},
        'reverse_source_exact': True,
        'lifetime': [
            'Only complete immutable linker .text ranges and in-range <=32-slot loads defer; mutable inputs, other targets and worker-enabled builds keep the original eager function body.',
            'Before each G_VTX, old pending slots surviving the overwrite are materialized against their original RSP state. Overwritten slots are discarded before assigning the new source pointer.',
            'Matrix/light/texture-scale/geometry-mode/fog mutations flush pending attributes before the opcode. Frame completion and reset flush pending state as well.',
            'A surviving triangle materializes all of its in-range pending slots before clipping. Rejected triangles do not consume attributes. Scratch rectangle vertices remain outside the pending range.',
            'Original UV, lighting accumulation, texture generation and fog statements are copied literally into the deferred helper; original position and reciprocal expressions are copied literally into the eager geometry loop.',
        ],
        'remaining': 'Independent source review, compile and emitted FMA comparison, paired scene capture/cycles and exact GPU color/depth replay. No speedup is claimed.'
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
