#!/usr/bin/env python3
"""Reuse exact geometry within immutable vertex batches; never build or deploy."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MEASUREMENTS = ROOT / 'docs/measurements/os30-simple-20261005'
BASE = MEASUREMENTS / 'baseline/gfx_pc.c'
OUT = MEASUREMENTS / 'batch-position-plan-v1'
EXPECTED = '4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'
GUARD = '#if defined(TARGET_OPENFPGA) && defined(GBI_FLOATS) && !defined(OF_PC)'
HELPER = GUARD + '''
/* A plan records only equal object positions in one immutable G_VTX batch.
 * Reused geometry always comes from an earlier output in this same call, so
 * matrices/aspect can change freely between calls. Attributes remain per Vtx. */
struct BatchPositionPlan {
    uintptr_t source;
    size_t count;
    uint8_t previous[32];       /* earlier input index + 1; zero means unique */
};
static struct BatchPositionPlan batch_position_plans[256];

static const struct BatchPositionPlan *gfx_batch_position_plan(
        const Vtx *vertices, size_t count, size_t first) {
    if (!count || first >= 32 || count > 32 - first)
        return NULL;
    const uintptr_t address = (uintptr_t)vertices;
    const uintptr_t end = (uintptr_t)__EH_FRAME_BEGIN__;
    if (address < (uintptr_t)&_start || address >= end ||
        count > (end - address) / sizeof(Vtx))
        return NULL;

    const unsigned hash = ((address >> 3) ^ (count * 40503u)) & 255u;
    struct BatchPositionPlan *plan = &batch_position_plans[hash];
    if (plan->source == address && plan->count == count)
        return plan;

    uint32_t keys[32][3];
    for (size_t i = 0; i < count; ++i) {
        for (unsigned axis = 0; axis < 3; ++axis) {
            union { float value; uint32_t bits; } key;
            key.value = vertices[i].v.ob[axis];
            keys[i][axis] = key.bits;
        }
        plan->previous[i] = 0;
        for (size_t j = 0; j < i; ++j) {
            if (keys[j][0] == keys[i][0] && keys[j][1] == keys[i][1] &&
                keys[j][2] == keys[i][2]) {
                plan->previous[i] = (uint8_t)(j + 1);
                break;
            }
        }
    }
    plan->source = address;
    plan->count = count;
    return plan;
}
#endif

'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(source):
    assert sha(source.encode()) == EXPECTED
    candidate = source
    substitutions = []

    def replace(old, new):
        nonlocal candidate
        assert candidate.count(old) == 1
        candidate = candidate.replace(old, new)
        substitutions.append((old, new))

    anchor = 'static __attribute__((noinline)) void gfx_sp_vertex('
    replace(anchor, HELPER + anchor)
    anchor = '    const float m00 = rsp.MP_matrix[0][0]'
    replace(anchor, GUARD + '''
    const struct BatchPositionPlan *position_plan =
        gfx_batch_position_plan(vertices, n_vertices, dest_index);
    const size_t position_first = dest_index;
#endif
''' + anchor)

    old = '''        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
        float x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
        float y = ox * m01 + oy * m11 + oz * m21 + m31;
        float z = ox * m02 + oy * m12 + oz * m22 + m32;
        float w = ox * m03 + oy * m13 + oz * m23 + m33;
'''
    new = '''        float x, y, z, w;
''' + GUARD + '''
        const struct LoadedVertex *position_previous = NULL;
        if (position_plan && position_plan->previous[i])
            position_previous = &rsp.loaded_vertices[
                position_first + position_plan->previous[i] - 1];
        if (position_previous) {
            x = position_previous->x; y = position_previous->y;
            z = position_previous->z; w = position_previous->w;
        } else
#endif
        {
            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
            x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
            y = ox * m01 + oy * m11 + oz * m21 + m31;
            z = ox * m02 + oy * m12 + oz * m22 + m32;
            w = ox * m03 + oy * m13 + oz * m23 + m33;
        }
'''
    replace(old, new)

    old = '''        // trivial clip rejection
        d->clip_rej = 0;
        if (x < -w) d->clip_rej |= CLIP_LEFT;
        if (x >  w) d->clip_rej |= CLIP_RIGHT;
        if (y < -w) d->clip_rej |= CLIP_BOTTOM;
        if (y >  w) d->clip_rej |= CLIP_TOP;
        if (z < -w) d->clip_rej |= CLIP_FAR;
        if (z >  w) d->clip_rej |= CLIP_NEAR;
'''
    new = '''        // trivial clip rejection
''' + GUARD + '''
        if (position_previous) d->clip_rej = position_previous->clip_rej;
        else {
#endif
''' + old.split('\n', 1)[1] + GUARD + '''
        }
#endif
'''
    replace(old, new)
    old = '        d->w_inv = RSPV_RCP(w);\n'
    new = GUARD + '''
        d->w_inv = position_previous ? position_previous->w_inv : RSPV_RCP(w);
#else
''' + old + '#endif\n'
    replace(old, new)

    restored = candidate
    for old, new in reversed(substitutions):
        assert restored.count(new) == 1
        restored = restored.replace(new, old)
    assert restored == source
    attrs_start = source.index('        short U = v->tc[0]')
    attrs_end = source.index('        // trivial clip rejection', attrs_start)
    assert candidate.count(source[attrs_start:attrs_end]) == 1
    fog_start = source.index('        if (configEnableFog && (rsp.geometry_mode & G_FOG)) {', attrs_end)
    fog_end = source.index('\n    }\n    }\n#ifdef TARGET_POCKET', fog_start)
    assert candidate.count(source[fog_start:fog_end]) == 1
    return candidate


def main():
    original = BASE.read_text()
    candidate = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'gfx_pc.c'
    assert not path.exists() or path.read_text() == candidate
    path.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'source_review_passed': True,
        'qualified': False,
        'entries': 256,
        'target_plan_bytes': 40,
        'target_total_cache_bytes': 10240,
        'source_scope': 'One immutable-batch position plan helper, one batch '
                        'lookup, and optional within-call reuse of xyzw, '
                        'reciprocal and clip mask. Attributes are unchanged.',
        'guards': [
            'TARGET_OPENFPGA, GBI_FLOATS, non-OF_PC build.',
            'Nonempty batch entirely within loaded vertex slots 0..31.',
            'Original Vtx byte range entirely inside _start..__EH_FRAME_BEGIN__.',
            'Cache hit requires the complete source address and count.',
        ],
        'proof': [
            'Every nonzero plan byte is j+1 for j<i after exact comparison of '
            'all three raw object-coordinate float words.',
            'The previous output is initialized by an earlier iteration of '
            'the same call, whose MVP and aspect locals are invariant.',
            'Plans describe only immutable input equality; they never hold '
            'transformed values across calls, so no matrix epoch is required.',
            'The destination range guard keeps previous output accesses '
            'within 0..31; j<i rules out current/uninitialized outputs.',
            'Full original UV, lighting, texture-generation and fog source '
            'blocks compare byte-identical. Reuse does not copy color or UV.',
            'Reverse substitution of all additions restores the complete '
            'baseline source exactly.',
            'GEO1 successful geometry loads return before the plan lookup. '
            'Ordinary CPU fallback uses the same unchanged attribute pipeline.',
        ],
        'limits': [
            'Uses the existing renderer contract that the executable/const '
            'asset range remains immutable.',
            'A plan miss costs O(count squared), bounded by count<=32. '
            'Direct-map collisions rebuild plans rather than reuse stale data.',
            'One plan lookup per batch still costs instructions; no benefit '
            'claimed before runtime reuse/cycle measurements.',
            'Target compiler floating-point contraction and complete GPU '
            'color/depth outputs require separate qualification.',
        ],
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
