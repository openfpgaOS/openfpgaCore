#!/usr/bin/env python3
"""Prepare the local UV-floor experiment; no sibling or package writes."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008'
COMPONENT = OUT / 'components/floor-v1'
REL = Path('src/quake2/openfpga/of_gl1.c')
BASE_SHA = 'a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'

HELPER = '''/* Floor the texture-rebase quotient with the F extension. Values below
 * 2^23 fit exactly in both conversion directions; explicit round-down avoids
 * depending on the current floating-point rounding mode. Preserve signed
 * zero and leave large/nonfinite inputs on the original library path. */
static inline float
ofgl_uv_floor(float v)
{
#if defined(__riscv) && defined(__riscv_flen) && __riscv_flen >= 32
\tuint32_t bits;
\tmemcpy(&bits, &v, sizeof(bits));
\tuint32_t magnitude = bits & 0x7fffffffu;
\tif (magnitude != 0 && magnitude < 0x4b000000u)
\t{
\t\tint32_t integral;
\t\t__asm__("fcvt.w.s %0, %1, rdn" : "=r"(integral) : "f"(v));
\t\treturn (float)integral;
\t}
#endif
\treturn floorf(v);
}

'''

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def apply(text):
    anchor = '/* Emit a finite, visible triangle. Returns 1 if it reached the GPU. */\n'
    assert text.count(anchor) == 1
    text = text.replace(anchor, HELPER + anchor)
    for axis, size in [('s', 'tw'), ('t', 'th')]:
        before = f'base_{axis} = floorf(base_{axis} * ofgl_inv_{size}) * {size};'
        after = before.replace('floorf(', 'ofgl_uv_floor(')
        assert text.count(before) == 1
        text = text.replace(before, after)
    return text

def main():
    base = OUT / 'work/base'
    candidate = OUT / 'work/floor'
    assert sha(base / REL) == BASE_SHA
    assert not candidate.exists() and not COMPONENT.exists()
    shutil.copytree(base, candidate)
    COMPONENT.mkdir(parents=True)
    old = (base / REL).read_text()
    new = apply(old)
    (candidate / REL).write_text(new)
    (COMPONENT / 'of_gl1.c').write_text(new)
    (COMPONENT / 'helper.h').write_text(HELPER)
    (COMPONENT / 'change.patch').write_text(''.join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), fromfile=str(REL), tofile=str(REL))))
    (COMPONENT / 'manifest.json').write_text(json.dumps(dict(
        baseline_sha256=BASE_SHA, candidate_sha256=sha(candidate / REL),
        helper_sha256=sha(COMPONENT / 'helper.h'), candidate=str(candidate),
        changes='Only the two ofgl_emit_ctri UV-rebase floorf calls use the local helper.',
        expected_equivalence='Float result bits match existing floorf for all inputs; exception flags are not a renderer input.'), indent=2) + '\n')
    print((COMPONENT / 'manifest.json').read_text())

if __name__ == '__main__':
    main()
