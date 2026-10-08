#!/usr/bin/env python3
"""Force two format-specific draw bodies from one source implementation."""
import difflib
import hashlib
import json
from pathlib import Path

import prepare_format_specialize as v1

OUT = v1.MEASUREMENTS / 'format-specialize-v2'
SPECIALIZED_WRAPPERS = '''

static __attribute__((noinline)) void gpu_draw_triangles_rgb565(
        float buf_vbo[], size_t buf_vbo_len, size_t buf_vbo_num_tris) {
    gpu_draw_triangles_format(buf_vbo, buf_vbo_len, buf_vbo_num_tris, 1);
}

static __attribute__((noinline)) void gpu_draw_triangles_indexed(
        float buf_vbo[], size_t buf_vbo_len, size_t buf_vbo_num_tris) {
    gpu_draw_triangles_format(buf_vbo, buf_vbo_len, buf_vbo_num_tris, 0);
}

static void gpu_draw_triangles(float buf_vbo[], size_t buf_vbo_len,
                               size_t buf_vbo_num_tris) {
    if (g_truecolor)
        gpu_draw_triangles_rgb565(buf_vbo, buf_vbo_len, buf_vbo_num_tris);
    else
        gpu_draw_triangles_indexed(buf_vbo, buf_vbo_len, buf_vbo_num_tris);
}'''


def main():
    original = v1.BASE.read_text()
    first = v1.transform(original)
    old = 'static __attribute__((noinline)) void gpu_draw_triangles_format('
    new = 'static inline __attribute__((always_inline)) void gpu_draw_triangles_format('
    assert first.count(old) == first.count(v1.WRAPPER) == 1
    candidate = first.replace(old, new).replace(v1.WRAPPER, SPECIALIZED_WRAPPERS)
    assert candidate.replace(new, old).replace(SPECIALIZED_WRAPPERS, v1.WRAPPER) == first
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / 'gfx_gpu.c'
    assert not path.exists() or path.read_text() == candidate
    path.write_text(candidate)
    (OUT / 'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='a/gfx_gpu.c', tofile='b/gfx_gpu.c')))
    (OUT / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    (OUT / 'prepare_format_specialize.py').write_bytes(Path(v1.__file__).read_bytes())
    sha = lambda data: hashlib.sha256(data).hexdigest()
    manifest = {
        'parent_sha256': sha(original.encode()),
        'candidate_sha256': sha(candidate.encode()),
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'v1_recipe_sha256': sha(Path(v1.__file__).read_bytes()),
        'source_review_passed': True,
        'runtime_qualified': False,
        'difference_from_v1': 'The shared body is always_inline, with separate '
                              'noinline RGB565 and indexed callers supplying '
                              'constant 1 and 0. Existing runtime dispatch '
                              'selects these specialized functions.',
        'reason': 'GCC declined IPA specialization of the v1 body. These explicit '
                  'constant call sites force format branch folding in separate '
                  'target functions without duplicating source math.',
        'proof': 'Exact reverse substitutions restore v1, whose original-body '
                 'and untouched-prefix/suffix source proofs are checked again.',
        'preserved': [
            'Both runtime formats, original shader/empty-batch/GPU guards.',
            'All rendering expressions, globals, command order and counters.',
            'Shared state-emission helpers and framebuffer stride globals.',
        ],
        'limits': [
            'Compiled expressions and register allocation can still differ; '
            'exact target replay is required.',
            'Two optimized target bodies may increase total text size even '
            'while the active RGB565 body shrinks.',
            'No speedup claimed without whole-app target capture.',
        ],
    }
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
