#!/usr/bin/env python3
"""Prepare the private exact clip-distance simplification; never build a stage."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
PHASE = ROOT / 'docs/measurements/os30-fps25-20261005/phase2'
FILES = ('gfx_pc.c', 'gfx_gpu.c', 'gfx_gpu.h', 'gfx_geometry.h',
         'perf_pocket.h', 'perf_pocket.c', 'Makefile.sm64')
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def transform(source, version=1):
    start = source.index('    // Clipping plane coefficients: each plane is {nx, ny, nz, nw}')
    end = source.index('    const uint8_t clip_or', start)
    source = source[:start] + source[end:]
    old = '''        const int8_t *plane = c_planes[plane_idx];
        /* Retain the original floating expression and evaluation
         * order; the preceding edge already calculated this point. */
        rspv_t d1 = plane[0] * v_in[0].x + plane[1] * v_in[0].y + plane[2] * v_in[0].z + plane[3] * v_in[0].w;'''
    new = '''        /* Select one coordinate per plane. Byte addressing plus memcpy
         * accesses the selected member without treating struct fields as an array. */
        const size_t axis = plane_idx < 2 ? offsetof(struct LoadedVertex, z)
            : plane_idx < 4 ? offsetof(struct LoadedVertex, y) : offsetof(struct LoadedVertex, x);
        const bool positive = (plane_idx & 1u) != 0;
        rspv_t coord;
        memcpy(&coord, (const uint8_t *)v_in + axis, sizeof(coord));
        rspv_t d1 = positive ? coord + v_in[0].w : v_in[0].w - coord;'''
    assert source.count(old) == 1
    source = source.replace(old, new)
    old = '''            // Plane distance using -1/0/1 coefficients (integer add/sub, no multiply)
            const rspv_t d2 = plane[0] * vnext->x + plane[1] * vnext->y + plane[2] * vnext->z + plane[3] * vnext->w;'''
    new = '''            memcpy(&coord, (const uint8_t *)vnext + axis, sizeof(coord));
            const rspv_t d2 = positive ? coord + vnext->w : vnext->w - coord;'''
    assert source.count(old) == 1
    source = source.replace(old, new)
    if version == 2:
        source = source.replace(
            '        /* Select one coordinate per plane. Byte addressing plus memcpy\n'
            '         * accesses the selected member without treating struct fields as an array. */',
            '        /* Select the actual float member by its byte offset; each offset\n'
            '         * is aligned and points to x, y or z within this vertex object. */')
        source = source.replace(
            '        rspv_t coord;\n'
            '        memcpy(&coord, (const uint8_t *)v_in + axis, sizeof(coord));',
            '        rspv_t coord = *(const rspv_t *)((const uint8_t *)v_in + axis);')
        source = source.replace(
            '            memcpy(&coord, (const uint8_t *)vnext + axis, sizeof(coord));',
            '            coord = *(const rspv_t *)((const uint8_t *)vnext + axis);')
    return source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    name_candidate = 'clip-distance-v'+str(args.version)
    DEST = PHASE / name_candidate
    base = PHASE / 'baseline'
    assert sha(base/'gfx_pc.c') == 'a5c16f135e4b89006a08891794562329f7b0a56daf0b22bb1cd35d791f7f90b5'
    DEST.mkdir(exist_ok=False)
    for name in FILES:
        shutil.copy2(base/name, DEST/name)
    original = (base/'gfx_pc.c').read_text()
    candidate = transform(original, args.version)
    (DEST/'gfx_pc.c').write_text(candidate)
    (DEST/'change.patch').write_text(''.join(difflib.unified_diff(
        original.splitlines(True), candidate.splitlines(True),
        fromfile='baseline/gfx_pc.c', tofile=name_candidate+'/gfx_pc.c')))
    record = {'candidate':name_candidate,'parent':'render-v6','qualified':False,
        'change':'Replace the six variable coefficient dot products with the selected w+axis or w-axis distance. Original interpolation, output order, culling and clipping policy unchanged.',
        'files':{name:{'parent_sha256':sha(base/name),'candidate_sha256':sha(DEST/name)} for name in FILES},
        'generator_sha256':sha(Path(__file__)),
        'scope':'Intended finite-coordinate RNE behavior. Signed-zero distance encodings can differ internally; exact emitted vertex-bit proof and target code generation checks required.',
        'pending':['Host exact extracted-function comparison for finite/signed-zero/mask cases','SDK RNE code generation or target component proof','Whole-scene rendering and CPU measurement']}
    (DEST/'manifest.json').write_text(json.dumps(record,indent=2)+'\n')
    print(DEST)


if __name__ == '__main__':
    main()
