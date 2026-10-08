#!/usr/bin/env python3
"""Skip the W-sign parity comparisons when all three sign bits are clear."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005'
PARENT = CAMP / 'phase7/combined-v1'
DEST = CAMP / 'phase9/w-positive-v1'

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
    helper = '''/* When every binary32 sign bit is clear, every W<0 predicate is
 * false, including +0 and positive NaNs. Preserve the original comparisons
 * for every other case, including negative zero and negative NaNs. */
static inline __attribute__((always_inline)) int gfx_cpu_w_sign_check(
        const float *a, const float *b, const float *c) {
#if defined(__riscv) && defined(TARGET_OPENFPGA)
    _Static_assert(sizeof(float) == sizeof(uint32_t), "binary32 W");
    uint32_t x, y, z;
    memcpy(&x, a, sizeof(x));
    memcpy(&y, b, sizeof(y));
    memcpy(&z, c, sizeof(z));
    return (x | y | z) >> 31;
#else
    return 1;
#endif
}

'''
    assert original.count(anchor) == 1
    changed = original.replace(anchor, helper + anchor)
    expr = 'if ((v1->w < 0) ^ (v2->w < 0) ^ (v3->w < 0))'
    replacement = '''if (gfx_cpu_w_sign_check(&v1->w, &v2->w, &v3->w) &&
            ((v1->w < 0) ^ (v2->w < 0) ^ (v3->w < 0)))'''
    assert changed.count(expr) == 2
    changed = changed.replace(expr, replacement)
    sources['gfx_pc.c'] = changed
    DEST.mkdir(parents=True, exist_ok=False)
    for name, contents in sources.items():
        (DEST / name).write_text(contents)
    (DEST / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), changed.splitlines(True), fromfile='F9/gfx_pc.c', tofile='w-positive-v1/gfx_pc.c')))
    record = dict(parent='phase7/combined-v1/F9', parent_elf_sha256=build['elf_sha256'],
                  source_sha256={n: sha(DEST / n) for n in sources},
                  parent_source_sha256={n: sha(PARENT / n) for n in sources},
                  recipe_sha256=sha(Path(__file__)), qualified=False,
                  scope='Skip three float comparisons only when all W sign bits clear; preserve all other predicates and winding math.',
                  proof='Every binary32 value with clear sign bit compares false to zero with less-than, including NaNs; any set sign bit retains exact old parity test.',
                  limits='Sign-clear NaN no longer accrues invalid flags here; graphics code does not inspect fflags. Rendering/math/performance need produced-code validation.')
    (DEST / 'source.json').write_text(json.dumps(record, indent=2) + '\n')
    print(DEST)

if __name__ == '__main__':
    main()
