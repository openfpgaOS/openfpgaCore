#!/usr/bin/env python3
"""Isolate integer W-sign predicates over frozen F9; private experiment only."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
PARENT = CAMP / 'phase7/combined-v1'
DEST = CAMP / 'phase9/w-sign-v1'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    build = json.loads((PARENT / 'build-normal.json').read_text())
    assert sha(PARENT / 'app.elf') == build['elf_sha256']
    sources = {}
    for name, item in build['inputs'].items():
        assert sha(PARENT / name) == item['sha256']
        sources[name] = (PARENT / name).read_text()
    original = sources['gfx_pc.c']
    anchor = 'static void gfx_sp_tri1(uint8_t vtx1_idx, uint8_t vtx2_idx, uint8_t vtx3_idx) {'
    helper = '''/* Use integer loads/comparisons for winding parity. This is the exact
 * binary32 less-than-zero predicate, including -infinity, excluding -0 and
 * all NaNs. Arithmetic, rounding, and culling thresholds are unchanged. */
static inline __attribute__((always_inline)) int gfx_cpu_w_negative(const float *w) {
#if defined(__riscv) && defined(TARGET_OPENFPGA)
    _Static_assert(sizeof(float) == sizeof(uint32_t), "binary32 W");
    uint32_t bits;
    memcpy(&bits, w, sizeof(bits));
    return bits - 0x80000001u < 0x7f800000u;
#else
    return *w < 0.0f;
#endif
}

'''
    assert original.count(anchor) == 1
    changed = original.replace(anchor, helper + anchor)
    expr = '(v1->w < 0) ^ (v2->w < 0) ^ (v3->w < 0)'
    replacement = 'gfx_cpu_w_negative(&v1->w) ^ gfx_cpu_w_negative(&v2->w) ^ gfx_cpu_w_negative(&v3->w)'
    assert changed.count(expr) == 2
    changed = changed.replace(expr, replacement)
    sources['gfx_pc.c'] = changed
    DEST.mkdir(parents=True, exist_ok=False)
    for name, contents in sources.items():
        (DEST / name).write_text(contents)
    (DEST / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), changed.splitlines(True), fromfile='F9/gfx_pc.c', tofile='w-sign-v1/gfx_pc.c')))
    record = dict(parent='phase7/combined-v1/F9', parent_elf_sha256=build['elf_sha256'],
                  source_sha256={n: sha(DEST / n) for n in sources},
                  parent_source_sha256={n: sha(PARENT / n) for n in sources},
                  recipe_sha256=sha(Path(__file__)), qualified=False,
                  scope='Only the three W-negative tests in both CPU culling paths; no geometry arithmetic changes.',
                  proof='Unsigned interval [0x80000001,0xff800000] contains all and only negative nonzero finite binary32 values and negative infinity. Subtracting its lower bound and comparing to its width tests exactly that interval, with defined unsigned wrap.',
                  limits='IEEE NaN comparison can accrue invalid flags; integer check does not. Rendering consumes the boolean only. Actual target codegen/output/performance remain unqualified.')
    (DEST / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(DEST)

if __name__ == '__main__':
    main()
