#!/usr/bin/env python3
"""Private exact clip-mask expression experiment; no geometry changes."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'
original = (OUT / 'baseline/gfx_pc.c').read_text()
old = '''        d->clip_rej = 0;
        if (x < -w) d->clip_rej |= CLIP_LEFT;
        if (x >  w) d->clip_rej |= CLIP_RIGHT;
        if (y < -w) d->clip_rej |= CLIP_BOTTOM;
        if (y >  w) d->clip_rej |= CLIP_TOP;
        if (z < -w) d->clip_rej |= CLIP_FAR;
        if (z >  w) d->clip_rej |= CLIP_NEAR;
'''
new = '''        /* Six independent predicates, with one mask store. This preserves
         * every comparison while allowing straight-line RV32 flag packing. */
        d->clip_rej = (x < -w) * CLIP_LEFT | (x > w) * CLIP_RIGHT
                    | (y < -w) * CLIP_BOTTOM | (y > w) * CLIP_TOP
                    | (z < -w) * CLIP_FAR | (z > w) * CLIP_NEAR;
'''
assert original.count(old) == 1
candidate = original.replace(old, new)
dest = OUT / 'vertex-flags'
dest.mkdir(exist_ok=False)
(dest / 'gfx_pc.c').write_text(candidate)
(dest / 'change.patch').write_text(''.join(difflib.unified_diff(
    original.splitlines(True), candidate.splitlines(True),
    fromfile='a/gfx_pc.c', tofile='b/gfx_pc.c')))
(dest / 'manifest.json').write_text(json.dumps({
    'baseline_sha256': hashlib.sha256(original.encode()).hexdigest(),
    'candidate_sha256': hashlib.sha256(candidate.encode()).hexdigest(),
    'scope': 'Equivalent per-vertex clip mask expression; same FP comparisons.',
    'qualified': False,
}, indent=2) + '\n')
print(dest)
