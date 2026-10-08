#!/usr/bin/env python3
"""Explicitly prune accelerator dispatch from the CPU-geometry build; no stage writes."""
import difflib
import hashlib
import json
from pathlib import Path

from prepare_cpu_only import BASE, EXPECTED, OUT, replace_once, transform_gpu, transform_pc

DEST = OUT / 'cpu-only-v2'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def transform(source):
    source = transform_pc(source)
    for old, new in (
        ('#ifdef TARGET_OPENFPGA\n    uint32_t overwritten=',
         '#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP\n    uint32_t overwritten='),
        ('#ifdef TARGET_OPENFPGA\n        ++geometry_mvp_epoch;',
         '#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP\n        ++geometry_mvp_epoch;'),
        ('#ifdef TARGET_OPENFPGA\n    const int geometry_result=',
         '#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP\n    const int geometry_result='),
        ('''                           bool raw_geometry) {
    int rgb_in, alpha_in;''',
         '''                           bool raw_geometry) {
#if !GFX_GPU_GEOMETRY_WORKER_APP
    /* No caller can submit deferred vertices in this executable. Keep the
     * ordinary clip-space upload path and let the compiler drop raw mode. */
    raw_geometry = false;
#endif
    int rgb_in, alpha_in;'''),
        ('''static inline int gfx_push_triangle(const struct LoadedVertex *restrict v1, const struct LoadedVertex *restrict v2, const struct LoadedVertex *restrict v3, bool geometry_only) {
''',
         '''static inline int gfx_push_triangle(const struct LoadedVertex *restrict v1, const struct LoadedVertex *restrict v2, const struct LoadedVertex *restrict v3, bool geometry_only) {
#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
    geometry_only = false;
#endif
'''),
        ('#ifdef TARGET_OPENFPGA\n    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending)',
         '#if defined(TARGET_OPENFPGA) && GFX_GPU_GEOMETRY_WORKER_APP\n    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending)'),
        ('        if(vertex_attributes_pending && (opcode==',
         '#if GFX_GPU_GEOMETRY_WORKER_APP\n        if(vertex_attributes_pending && (opcode=='),
        ('''            if(next!=cmd) {cmd=next;continue;}
        }
#endif

        switch (opcode)''',
         '''            if(next!=cmd) {cmd=next;continue;}
        }
#endif /* GFX_GPU_GEOMETRY_WORKER_APP */
#endif

        switch (opcode)'''),
        ('''static void gfx_sp_reset() {
#ifdef TARGET_OPENFPGA
    gfx_flush_vertex_attributes(0xffffffffu);
''',
         '''static void gfx_sp_reset() {
#ifdef TARGET_OPENFPGA
#if GFX_GPU_GEOMETRY_WORKER_APP
    gfx_flush_vertex_attributes(0xffffffffu);
#endif
'''),
    ):
        source = replace_once(source, old, new)
    return source


def main():
    originals = {name: (BASE / name).read_bytes() for name in EXPECTED}
    for name, data in originals.items():
        assert sha(data) == EXPECTED[name], name
    candidates = {
        'gfx_pc.c': transform(originals['gfx_pc.c'].decode()).encode(),
        'gfx_gpu.c': transform_gpu(originals['gfx_gpu.c'].decode()).encode(),
    }
    DEST.mkdir(exist_ok=False)
    files = {}
    for name, candidate in candidates.items():
        original = originals[name]
        (DEST / name).write_bytes(candidate)
        (DEST / (name + '.patch')).write_text(''.join(difflib.unified_diff(
            original.decode().splitlines(True), candidate.decode().splitlines(True),
            fromfile='a/' + name, tofile='b/' + name)))
        files[name] = {'baseline_sha256': sha(original), 'candidate_sha256': sha(candidate)}
    (DEST / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    v1_recipe = Path(__file__).with_name('prepare_cpu_only.py')
    (DEST / v1_recipe.name).write_bytes(v1_recipe.read_bytes())
    record = {
        'files': files,
        'qualified': False,
        'recipe_sha256': {path.name: sha(path.read_bytes()) for path in (Path(__file__), v1_recipe)},
        'macro': 'GFX_GPU_GEOMETRY_WORKER_APP',
        'default': 0,
        'scope': 'Explicit compile-time guards on absent accelerator/deferred bookkeeping '
                 'at vertex, triangle, display-list and reset entry points.',
        'preserved': [
            'Ordinary CPU transform/FMA, clip masks, homogeneous clipping, backface tests, '
            'lighting, fog and all GPU sampling/rendering paths.',
            'GPU vertex cache staleness and material lifetime tracking.',
            'Public P3 statistics and app geometry-mode query.',
            'Opt-in macro=1 preserves the previous code; GFX_GPU_GEOMETRY remains an '
            'additional enable condition.',
        ],
        'required_checks': [
            'Normal/diagnostic ELF emission has no worker dispatch/state references.',
            'Macro=1 preprocessing matches baseline tokens except the two logically '
            'redundant backend enable expressions.',
            'Same scene and exact GPU color/depth/cycles, whole-trace modeled CPU '
            'comparison and final combined measurement.',
        ],
        'stage_changed': False,
        'sibling_changed': False,
        'card_changed': False,
    }
    (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
