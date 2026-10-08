#!/usr/bin/env python3
"""Bit-compare extracted eager math and saved-state lazy CPU fallback."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import struct
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path[:0] = [str(HERE), str(HERE.parent / 'client')]
from stage_lazy import DEFAULT, transform
from analyze import read

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=DEFAULT/'gfx_pc.c')
    parser.add_argument('--out-name',default='lazy-cpu')
    args=parser.parse_args()
    out = HERE / 'results' / args.out_name
    out.mkdir(exist_ok=False)
    source = args.source.read_text()
    transformed, cpu = transform(source)
    (out / 'original_gfx_pc.c').write_text(source)
    (out / 'gfx_pc.c').write_text(transformed)
    (out / 'sm64_lazy_cpu.h').write_text(cpu)
    for name in ('sm64_lazy_state.h', 'sm64_lazy_capture.h'):
        shutil.copy2(HERE / name, out / name)
    original = source[source.index('    { /* Time the complete fallback loop,'):]
    original = original[:original.index('\n#ifdef TARGET_POCKET\n    prof_vtx')]
    gbi = (DEFAULT.parents[2] / 'include/PR/gbi.h').read_text()
    vtx = gbi[gbi.index('typedef struct {\n#ifndef GBI_FLOATS'):]
    vtx = vtx[:vtx.index('\n/*\n * Sprite structure')]
    light = gbi[gbi.index('typedef struct {\n  unsigned char\tcol[3]'):]
    light = light[:light.index('} Light_t;') + len('} Light_t;')]
    loaded = source[source.index('struct LoadedVertex {'):]
    loaded = loaded[:loaded.index('\n};') + 3]
    rsp = source[source.index('static struct RSP {'):]
    rsp = rsp[:rsp.index('\n} rsp;') + len('\n} rsp;')]
    prefix = '''#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#define GBI_FLOATS 1
#define TARGET_OPENFPGA 1
#define MAX_LIGHTS 2
#define MAX_VERTICES 64
#define G_LIGHTING 0x20000
#define G_TEXTURE_GEN 0x40000
#define G_FOG 0x10000
#define SM64_PERF_SCOPE(x) ((void)0)
#define SM64_PERF_COUNT(x,y) ((void)0)
typedef float rspv_t;
#define RSPV_TO_FLOAT(x) (x)
#define RSPV_FROM_INT(x) ((float)(x))
#define RSPV_RCP(x) (1.0f/(x))
enum {CLIP_NEAR=1,CLIP_FAR=2,CLIP_TOP=4,CLIP_BOTTOM=8,CLIP_RIGHT=16,CLIP_LEFT=32};
struct RGBA { uint8_t r,g,b,a; };
'''
    prefix += vtx + '\n' + light + '\n' + loaded + '\n' + rsp
    prefix += '''
struct LightingMemo {uint32_t epoch,normal;struct RGBA color;};
static struct LightingMemo lighting_memo[256];
static uint32_t lighting_epoch=1;
static bool configEnableFog;
static uint32_t geometry_depth[32];
static void original_vertices(size_t n_vertices,size_t dest_index,const Vtx *vertices,float aspect_scale) {
''' + original + '\n}\n#include "sm64_lazy_cpu.h"\n#include "sm64_lazy_state.h"\n'
    (out / 'test_lazy.c').write_text(prefix + (HERE / 'test_lazy.c').read_text())
    count = 0
    with (out / 'scene.bin').open('wb') as dest:
        for path in sorted((ROOT / 'docs/measurements/os30-mesh-20261004/SM64/mesh-shadow/run-360-8').glob('mesh-*.bin')):
            _, jobs = read(path)
            for job in jobs:
                words = [job['header'][3], *job['state'], *job['vertices']]
                dest.write(struct.pack('<' + 'I'*len(words), *words))
                count += 1
    results = []
    for flags, label in [(['-O3', '-ffast-math', '-ffp-contract=off'], 'optimized'),
                         (['-O1', '-fsanitize=address,undefined', '-fno-omit-frame-pointer'], 'sanitized')]:
        exe = out / label
        command = ['gcc', '-std=gnu99', '-fno-strict-aliasing', '-fwrapv', *flags,
                   str(out / 'test_lazy.c'), '-lm', '-o', str(exe)]
        subprocess.run(command, check=True)
        run = subprocess.run([str(exe), str(out / 'scene.bin')], check=True, text=True, capture_output=True,
                             env={**__import__('os').environ, 'ASAN_OPTIONS':'detect_leaks=0'})
        print(run.stdout, end='')
        results.append(dict(configuration=label, command=command, output=run.stdout))
    result = dict(scene_loads=count, random_loads=30000,
                  original_sha256=hashlib.sha256(source.encode()).hexdigest(), results=results,
                  scope='CPU float fallback bits and saved-state ownership; no GPU timing claim')
    (out / 'result.json').write_text(json.dumps(result, indent=2)+'\n')

if __name__ == '__main__':
    main()
