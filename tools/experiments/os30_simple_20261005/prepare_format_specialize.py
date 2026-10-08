#!/usr/bin/env python3
"""Freeze a private runtime-format specialization; do not build or deploy."""
import difflib
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MEASUREMENTS = ROOT / 'docs/measurements/os30-simple-20261005'
BASE = MEASUREMENTS / 'baseline/gfx_gpu.c'
OUT = MEASUREMENTS / 'format-specialize-v1'
EXPECTED = '71d7e1fb8afaf414c746a487c5cc9fa1eba22f685a4173c772743a662210bada'
OLD_HEADER = '''static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,
                               size_t buf_vbo_num_tris) {'''
NEW_HEADER = '''/* The framebuffer format is fixed during a draw. Keep one source body,
 * allowing the compiler to specialize its two constant-format callers. */
static __attribute__((noinline)) void gpu_draw_triangles_format(
        float buf_vbo[], size_t buf_vbo_len, size_t buf_vbo_num_tris,
        const int batch_truecolor) {'''
WRAPPER = '''

static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,
                               size_t buf_vbo_num_tris) {
    if (g_truecolor)
        gpu_draw_triangles_format(buf_vbo, buf_vbo_len, buf_vbo_num_tris, 1);
    else
        gpu_draw_triangles_format(buf_vbo, buf_vbo_len, buf_vbo_num_tris, 0);
}'''
NEXT_SECTION = '''

/* ================================================================
 * 2D rects'''


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(source):
    assert sha(source.encode()) == EXPECTED
    start = source.index(OLD_HEADER)
    end = source.index(NEXT_SECTION, start)
    original = source[start:end]
    assert original.endswith('}')
    assert len(re.findall(r'\bg_truecolor\b', original)) == 9
    assert not re.search(r'\bbatch_truecolor\b', original)
    specialized = re.sub(r'\bg_truecolor\b', 'batch_truecolor', original)
    specialized = specialized.replace(OLD_HEADER, NEW_HEADER, 1)
    candidate = source[:start] + specialized + WRAPPER + source[end:]
    # Exact reverse substitution proves that the body changed only its format
    # reads/signature; helper calls and all arithmetic expression text survive.
    restored = specialized.replace(NEW_HEADER, OLD_HEADER, 1)
    restored = re.sub(r'\bbatch_truecolor\b', 'g_truecolor', restored)
    assert restored == original
    assert candidate[:start] == source[:start]
    assert candidate[start + len(specialized) + len(WRAPPER):] == source[end:]
    return candidate


def main():
    original = BASE.read_text()
    candidate = transform(original)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'gfx_gpu.c'
    assert not path.exists() or path.read_text() == candidate
    path.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_gpu.c', tofile='b/gfx_gpu.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = {
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'source_review_passed': True,
        'runtime_qualified': False,
        'source_scope': 'One renamed draw body with explicit format parameter, '
                        'nine direct format reads replaced, one API-preserving '
                        'wrapper dispatching on the original runtime format.',
        'preserved': [
            'Both RGB565 and indexed-color paths remain available at runtime.',
            'GPU/shader/empty-batch guards and performance scope stay in the body.',
            'All rendering arithmetic, combiner, clipping, alpha, counters, '
            'command emission and kick calls preserve expression text and order.',
            'All other functions, including state emission and rectangle paths, '
            'remain byte-identical.',
        ],
        'format_lifetime': 'g_truecolor is file-static and assigned only by '
                           'gfx_gpu_boot. No call from gpu_draw_triangles initializes '
                           'or reconfigures the backend. The value is normalized '
                           'to 0 or 1 by initialization.',
        'proof': 'Reverse replacement of the parameter reads and signature '
                 'restores the original body exactly; untouched source prefix '
                 'and suffix compare byte-identical.',
        'compiler_intent': 'noinline retains a batch call boundary while allowing '
                           'IPA constant-propagation clones for arguments 0/1. '
                           'Target emitted clones and cache footprint must be '
                           'inspected after building.',
        'limits': [
            'g_fb_bpp has no direct read in this body; shared helpers still use '
            'the original global format state.',
            'The wrapper format branch precedes the existing diagnostic scope; '
            'normal whole-frame timing includes it.',
            'Preserving expression text does not replace target floating-point '
            'and full-frame command/color/depth qualification.',
            'No speedup claimed before target capture and replay.',
        ],
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
