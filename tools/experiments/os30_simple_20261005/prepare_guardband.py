#!/usr/bin/env python3
"""Trial: let the existing rasterizer scissor bounded offscreen triangles.

This changes clipping/interpolation rounding and is NOT an exact-output fix.
It is private until performance and complete rendered output are reviewed.
"""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'


def main():
    original = (OUT / 'baseline/gfx_pc.c').read_text()
    needle = '''#ifdef GFX_MANUAL_CLIPPING
    if (!gfx_clip_triangle(v1, v2, v3, clip_and))
'''
    replacement = '''#ifdef GFX_MANUAL_CLIPPING
#ifdef TARGET_OPENFPGA
    /* Private guard-band trial. Depth/eye-plane clipping stays on the CPU.
     * With 4 < W < 32760 and |X|,|Y| <= 4W, os30's existing Q12.4
     * projected coordinates and widened edge walker can scissor this face.
     * Keep original resident vertices instead of producing a clipped fan.
     * Side-plane clipping changes interpolation rounding: qualify output. */
    const unsigned side_clip = v1->clip_rej | v2->clip_rej | v3->clip_rej;
    if (side_clip && !(side_clip & (CLIP_NEAR | CLIP_FAR))
        && !gfx_gpu_geometry_enabled()) {
        const struct LoadedVertex *guard[3] = {v1, v2, v3};
        bool bounded = true;
        for (unsigned i = 0; i < 3; ++i) {
            const struct LoadedVertex *v = guard[i];
            const float limit = v->w * 4.0f;
            if (!(v->w > 4.0f && v->w < 32760.0f
                  && v->x >= -limit && v->x <= limit
                  && v->y >= -limit && v->y <= limit)) {
                bounded = false;
                break;
            }
        }
        if (bounded) {
#ifdef TARGET_POCKET
            tri_diag.direct++;
#endif
            gfx_push_triangle(v1, v2, v3, false);
            return;
        }
    }
#endif
    if (!gfx_clip_triangle(v1, v2, v3, clip_and))
'''
    assert original.count(needle) == 1
    candidate = original.replace(needle, replacement)
    dest = OUT / 'guardband'
    dest.mkdir(exist_ok=False)
    (dest / 'gfx_pc.c').write_text(candidate)
    (dest / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
    (dest / 'manifest.json').write_text(json.dumps({
        'baseline_sha256': hashlib.sha256(original.encode()).hexdigest(),
        'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
        'scope': 'Private GEO0 XY guardband experiment; CPU, GPU and clocks unchanged.',
        'qualified': False,
        'warning': 'Side-plane clipping changes interpolation rounding; rendering comparison required.',
    }, indent=2) + '\n')
    print(dest)


if __name__ == '__main__':
    main()
