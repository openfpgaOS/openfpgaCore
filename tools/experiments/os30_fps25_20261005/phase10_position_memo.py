#!/usr/bin/env python3
"""Create a private F11 position-transform memo experiment; never publish."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
BASE = CAMP / 'phase9/checks-state-f11'
OUT = CAMP / 'phase10/position-memo-v1'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    record = json.loads((BASE/'build-normal.json').read_text())
    inputs = {}
    for name, item in record['inputs'].items():
        assert sha(BASE/name) == item['sha256']
        inputs[name] = (BASE/name).read_text()
    original = inputs['gfx_pc.c']
    source = original
    old = 'static __attribute__((noinline)) void gfx_sp_vertex(size_t n_vertices, size_t dest_index, const Vtx *vertices) {'
    new = '''#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP && !defined(OF_PC)
#define GFX_CPU_POSITION_MEMO 1
/* Vertices duplicated at UV seams/material boundaries share positions but
 * not attributes. Only memoize the exact clip-space calculation, reciprocal
 * and rejection flags. Matrix/aspect changes end the entire memo epoch. */
struct PositionMemo {
    uint32_t epoch, xy;
    uint16_t z;
    uint8_t clip_rej, pad;
    float x_out, y_out, z_out, w_out, w_inv;
};
static struct PositionMemo position_memo[128];
static uint32_t position_memo_epoch = 1;
static void gfx_position_memo_invalidate(void) {
    if (++position_memo_epoch == 0) {
        memset(position_memo, 0, sizeof(position_memo));
        position_memo_epoch = 1;
    }
}
#else
#define GFX_CPU_POSITION_MEMO 0
#endif
''' + old
    assert source.count(old) == 1
    source = source.replace(old, new)
    old = '''        rsp.matrix_changed = false;
    }
    if (rsp.geometry_mode & G_LIGHTING)'''
    new = '''        rsp.matrix_changed = false;
#if GFX_CPU_POSITION_MEMO
        gfx_position_memo_invalidate();
#endif
    }
    if (rsp.geometry_mode & G_LIGHTING)'''
    assert source.count(old) == 1
    source = source.replace(old, new)
    old = '''        const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
        float x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
        float y = ox * m01 + oy * m11 + oz * m21 + m31;
        float z = ox * m02 + oy * m12 + oz * m22 + m32;
        float w = ox * m03 + oy * m13 + oz * m23 + m33;
'''
    new = '''        float x, y, z, w;
#if GFX_CPU_POSITION_MEMO
        float w_inv;
        uint8_t clip_rej;
        uint32_t key_xy;
        memcpy(&key_xy, &v->ob[0], sizeof(key_xy));
        const uint16_t key_z = (uint16_t)v->ob[2];
        const uint32_t hash = (key_xy ^ ((uint32_t)key_z * 0x9e3779b9u)) * 0x85ebca6bu;
        struct PositionMemo *pm = &position_memo[hash >> 25];
        if (pm->epoch == position_memo_epoch && pm->xy == key_xy && pm->z == key_z) {
            x = pm->x_out; y = pm->y_out; z = pm->z_out; w = pm->w_out;
            w_inv = pm->w_inv; clip_rej = pm->clip_rej;
        } else {
#endif
            const float ox = v->ob[0], oy = v->ob[1], oz = v->ob[2];
            x = (ox * m00 + oy * m10 + oz * m20 + m30) * aspect_scale;
            y = ox * m01 + oy * m11 + oz * m21 + m31;
            z = ox * m02 + oy * m12 + oz * m22 + m32;
            w = ox * m03 + oy * m13 + oz * m23 + m33;
#if GFX_CPU_POSITION_MEMO
            clip_rej = 0;
            if (x < -w) clip_rej |= CLIP_LEFT;
            if (x >  w) clip_rej |= CLIP_RIGHT;
            if (y < -w) clip_rej |= CLIP_BOTTOM;
            if (y >  w) clip_rej |= CLIP_TOP;
            if (z < -w) clip_rej |= CLIP_FAR;
            if (z >  w) clip_rej |= CLIP_NEAR;
            w_inv = RSPV_RCP(w);
            pm->epoch = position_memo_epoch; pm->xy = key_xy; pm->z = key_z;
            pm->x_out = x; pm->y_out = y; pm->z_out = z; pm->w_out = w;
            pm->w_inv = w_inv; pm->clip_rej = clip_rej;
        }
#endif
'''
    assert source.count(old) == 1
    source = source.replace(old,new)
    old = '''        // trivial clip rejection
        d->clip_rej = 0;'''
    new = '''#if GFX_CPU_POSITION_MEMO
        d->clip_rej = clip_rej;
#else
        // trivial clip rejection
        d->clip_rej = 0;'''
    assert source.count(old) == 1
    source = source.replace(old,new)
    old = '''        if (z >  w) d->clip_rej |= CLIP_NEAR;

        d->x = x;'''
    new = '''        if (z >  w) d->clip_rej |= CLIP_NEAR;
#endif

        d->x = x;'''
    assert source.count(old) == 1
    source = source.replace(old,new)
    old = '        d->w_inv = RSPV_RCP(w);'
    new = '''#if GFX_CPU_POSITION_MEMO
        d->w_inv = w_inv;
#else
        d->w_inv = RSPV_RCP(w);
#endif'''
    assert source.count(old) == 1
    source = source.replace(old,new)
    old = 'void gfx_start_frame(void) {'
    new = '''void gfx_start_frame(void) {
#if GFX_CPU_POSITION_MEMO
    gfx_position_memo_invalidate();
#endif'''
    assert source.count(old) == 1
    source = source.replace(old,new)
    inputs['gfx_pc.c'] = source
    OUT.mkdir(parents=True,exist_ok=False)
    for name,contents in inputs.items():
        (OUT/name).write_text(contents)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True), source.splitlines(True),fromfile='F11/gfx_pc.c',tofile='position-memo-v1/gfx_pc.c')))
    (OUT/'source.json').write_text(json.dumps(dict(parent='phase9/checks-state-f11',source_sha256={n:sha(OUT/n) for n in inputs},recipe_sha256=sha(Path(__file__)),hypothesis='Repeated object positions under one MVP can reuse transform/clip/reciprocal even when UV and normal differ.',qualified=False),indent=2)+'\n')
    print(OUT)

if __name__ == '__main__': main()
