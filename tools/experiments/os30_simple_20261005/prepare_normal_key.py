#!/usr/bin/env python3
"""Freeze exact lighting-key byte packing without changing lighting math."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MEASUREMENTS = ROOT / 'docs/measurements/os30-simple-20261005'
BASE = MEASUREMENTS / 'baseline/gfx_pc.c'
OUT = MEASUREMENTS / 'normal-key-v1'
EXPECTED = '4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84'
HELPER = '''
/* The memo key contains only normal XYZ. Explicit byte packing avoids the
 * target compiler's small-memcpy stack round trip; retain native-byte-order
 * behavior on other targets. Lighting and alpha evaluation are unchanged. */
static inline __attribute__((always_inline)) uint32_t gfx_lighting_normal_key(
        const uint8_t *cn) {
#if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__) && \\
    __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    return (uint32_t)cn[0] | ((uint32_t)cn[1] << 8) | ((uint32_t)cn[2] << 16);
#else
    uint32_t normal;
    memcpy(&normal, cn, sizeof(normal));
    return normal & 0x00ffffffu;
#endif
}
'''
ANCHOR = 'static struct LightingMemo lighting_memo[256];\n'
REPLACEMENTS = [
    ('        uint32_t normal;memcpy(&normal,v->cn,sizeof(normal));normal&=0xffffffu;',
     '        const uint32_t normal=gfx_lighting_normal_key(v->cn);'),
    ('''            uint32_t normal;
            memcpy(&normal,v->cn,sizeof(normal));
            normal&=0x00ffffffu;''',
     '            const uint32_t normal=gfx_lighting_normal_key(v->cn);'),
]
REPLACEMENTS = [('\n' + old + '\n', '\n' + new + '\n')
                for old, new in REPLACEMENTS]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(source):
    assert sha(source.encode()) == EXPECTED
    assert source.count(ANCHOR) == 1
    candidate = source.replace(ANCHOR, ANCHOR + HELPER)
    for old, new in REPLACEMENTS:
        assert candidate.count(old) == 1
        candidate = candidate.replace(old, new)
    restored = candidate.replace(ANCHOR + HELPER, ANCHOR, 1)
    for old, new in REPLACEMENTS:
        assert restored.count(new) == 1
        restored = restored.replace(new, old)
    assert restored == source
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
        'scope': 'Replace the two lighting memo key extractions in ordinary '
                 'CPU vertices and deferred GPU-geometry attributes with one '
                 'always-inline byte-packing helper.',
        'proof': 'For little-endian uint32_t, memcpy of bytes b0,b1,b2,b3 '
                 'followed by mask0x00ffffff equals '
                 'b0|(b1<<8)|(b2<<16) for all byte values. Each source byte is '
                 'converted to uint32_t before shifting; the largest term is '
                 '0xff0000, so there is no signed shift or overflow. '
                 'Big-endian and unknown-endian builds execute the original '
                 'memcpy/mask operation unchanged.',
        'preserved': [
            'Normal cache hash, equality checks, epoch and cached RGB values.',
            'Every lighting, UV, texture-generation, matrix, clipping and fog '
            'expression and their control flow.',
            'Alpha reads and writes in their original evaluation paths.',
            'Non-TARGET_OPENFPGA builds; helper and both uses are enclosed '
            'by the existing target guards.',
        ],
        'reverse_source_exact': True,
        'remaining': 'Inspect target emitted code and measure exact application '
                     'replay/cycles before selecting. No speedup claimed.',
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
