#!/usr/bin/env python3
"""Freeze a two-file, compile-time CPU-geometry candidate; never change the build stage."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-simple-20261005'
BASE = OUT / 'baseline'
DEST = OUT / 'cpu-only-v1'
EXPECTED = {
    'gfx_pc.c': '4d8bd5cdef7b663adf86233763b54fda13d977abfaf225bdc8c0797d8e854e84',
    'gfx_gpu.c': '71d7e1fb8afaf414c746a487c5cc9fa1eba22f685a4173c772743a662210bada',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def transform_pc(source):
    return replace_once(source,
        '#include "gfx_gpu.h"   /* GPU vertex-cache fast path (0x56/0x54) entry points */\n',
        '''#include "gfx_gpu.h"   /* GPU vertex-cache fast path (0x56/0x54) entry points */
/* The shipping single-issue os30 uses CPU geometry. Make the accelerator
 * predicate constant in this translation unit so its unused deferred state
 * and dispatch checks can disappear. Define this to 1 in BOTH graphics
 * translation units to restore the optional accelerator renderer. */
#ifndef GFX_GPU_GEOMETRY_WORKER_APP
#define GFX_GPU_GEOMETRY_WORKER_APP 0
#endif
#if !GFX_GPU_GEOMETRY_WORKER_APP
#define gfx_gpu_geometry_enabled() 0
#endif
''')


def transform_gpu(source):
    source = replace_once(source, '#ifndef GFX_GPU_GEOMETRY\n',
        '''/* Keep the exported mode query consistent with gfx_pc.c's compile-time
 * renderer selection. Hardware capability detection for ordinary triangles,
 * clip-space vertex loads, textures and blending is unchanged. */
#ifndef GFX_GPU_GEOMETRY_WORKER_APP
#define GFX_GPU_GEOMETRY_WORKER_APP 0
#endif
#ifndef GFX_GPU_GEOMETRY
''')
    source = replace_once(source,
        'int gfx_gpu_geometry_enabled(void) { return g_geometry; }',
        'int gfx_gpu_geometry_enabled(void) { return GFX_GPU_GEOMETRY_WORKER_APP && g_geometry; }')
    return replace_once(source,
        'g_geometry = GFX_GPU_GEOMETRY && g_clip_load && ((GPU_STATUS & (1u<<10)) != 0);',
        'g_geometry = GFX_GPU_GEOMETRY_WORKER_APP && GFX_GPU_GEOMETRY\n'
        '            && g_clip_load && ((GPU_STATUS & (1u<<10)) != 0);')


def main():
    DEST.mkdir(exist_ok=False)
    records = {}
    for name, transform in (('gfx_pc.c', transform_pc), ('gfx_gpu.c', transform_gpu)):
        original = (BASE / name).read_bytes()
        assert sha(original) == EXPECTED[name], name
        candidate = transform(original.decode()).encode()
        (DEST / name).write_bytes(candidate)
        (DEST / (name + '.patch')).write_text(''.join(difflib.unified_diff(
            original.decode().splitlines(True), candidate.decode().splitlines(True),
            fromfile='a/' + name, tofile='b/' + name)))
        records[name] = {'baseline_sha256': sha(original), 'candidate_sha256': sha(candidate)}
    (DEST / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    record = {
        'files': records,
        'recipe_sha256': sha(Path(__file__).read_bytes()),
        'qualified': False,
        'macro': 'GFX_GPU_GEOMETRY_WORKER_APP',
        'default': 0,
        'opt_in': 'Compile BOTH gfx_pc.c and gfx_gpu.c with GFX_GPU_GEOMETRY_WORKER_APP=1; '
                  'existing GFX_GPU_GEOMETRY also remains an enable condition.',
        'scope': 'Only compile-time accelerator selection; no CPU arithmetic, clipping, '
                 'backface culling, lighting, GPU sampling, command or feature changes.',
        'compatibility': [
            'A uses ordinary CPU geometry with the existing GPU clip-load/triangle paths.',
            'A geometry-capable core can run the same CPU fallback, with the app mode '
            'query reporting zero because acceleration is disabled in this executable.',
            'P3 public geometry/statistics symbols remain defined by unchanged headers.',
        ],
        'required_checks': [
            'Inspect emitted normal and diagnostic code: confirm dead deferred paths '
            'and state actually disappear, rather than assuming interprocedural pruning.',
            'Compare the identical scene, all selected GPU color/depth traces and '
            'normal/diagnostic modeled CPU results.',
            'Verify macro=1 object code restores the original accelerator path.',
        ],
        'stage_changed': False,
        'sibling_changed': False,
        'card_changed': False,
    }
    (DEST / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
