#!/usr/bin/env python3
"""Reproduce the selected cache from the hash-guarded, pre-cache SM64 source.

Use --stage to select a private copy of that source tree. This does not copy
the SDK, assemble a package, or deploy anything.
"""
from pathlib import Path
import argparse,hashlib,json,shutil

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-triangle-cache-20261004'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--stage',type=Path,default=OUT/'SM64')
STAGE=p.parse_args().stage
GFX=STAGE/'src/sm64/sm64/src/pc/gfx'
before=json.loads((OUT/'before.json').read_text())
for name in ('gfx_pc.c','gfx_geometry.h'):
    assert hashlib.sha256((GFX/name).read_bytes()).hexdigest()==before['src/sm64/sm64/src/pc/gfx/'+name]
assert not (GFX/'gfx_geometry_cache.h').exists()
perf=STAGE/'src/sm64/pocket/perf_pocket.c'
assert hashlib.sha256(perf.read_bytes()).hexdigest()==before['src/sm64/pocket/perf_pocket.c']

p=GFX/'gfx_geometry.h';s=p.read_text()
s=s.replace('static struct GeometryVertex geometry_vertices[32];', '''static struct GeometryVertex geometry_vertices[32];
/* Nonzero only for a complete immutable source range. Store an integer
 * adjusted address, avoiding pointer arithmetic before the source object. */
static uintptr_t geometry_batch_source;
static uint32_t geometry_batch_mask;
static unsigned geometry_batch_context;''')
s=s.replace('    if (!gfx_gpu_geometry_enabled() || first >= 32)',
            '    geometry_batch_mask=0;\n    if (!gfx_gpu_geometry_enabled() || first >= 32)')
old='    for(size_t i=0;i<count;i++) {\n        struct GeometryVertex *v=&geometry_vertices[first+i];'
assert s.count(old)==1
s=s.replace(old,'''    geometry_batch_mask=mask;geometry_batch_context=context;
    uintptr_t source_base=0;
#ifndef OF_PC
    uintptr_t source=(uintptr_t)vertices,end=(uintptr_t)__EH_FRAME_BEGIN__;
    if(source>=(uintptr_t)&_start && source<end && count<=(end-source)/sizeof(Vtx))
        source_base=source-first*sizeof(Vtx);
#endif
    geometry_batch_source=source_base;
    for(size_t i=0;i<count;i++) {
        struct GeometryVertex *v=&geometry_vertices[first+i];''')
p.write_text(s)

p=GFX/'gfx_pc.c';s=p.read_text()
start=s.index('    const int textured=elig&GFX_VC_TEXTURED;',s.index('static Gfx *gfx_geometry_run('))
end=s.index('    return cmd;',start)
body=s[start:end]
helper='''static void gfx_geometry_submit(const struct ColorCombiner *comb,int elig,int rgb_in,
                                uint32_t used,const uint32_t *triangles,unsigned triangle_count,
                                uint8_t alpha) {
'''+body+'''}
#include "gfx_geometry_cache.h"

'''
s=s[:start]+'    gfx_geometry_submit(comb,elig,rgb_in,used,triangles,triangle_count,alpha);\n'+s[end:]
start=s.index('static Gfx *gfx_geometry_run(')
s=s[:start]+helper+s[start:]
old='static Gfx *gfx_geometry_run(Gfx *first) {\n    SM64_PERF_SCOPE(SM64_PERF_TRI);'
assert s.count(old)==1
s=s.replace(old,old+'\n    Gfx *cached=gfx_geometry_cached_run(first);\n    if(cached!=first)return cached;')
s=s.replace('static void gfx_sp_vertex(size_t n_vertices,',
            'static __attribute__((noinline)) void gfx_sp_vertex(size_t n_vertices,')
p.write_text(s)
shutil.copyfile(Path(__file__).with_name('gfx_geometry_cache.h'),GFX/'gfx_geometry_cache.h')
perf.write_text(perf.read_text().replace('PERF P2 SNAPSHOT','PERF P2 CACHE'))
print('Private immutable batch/face cache installed.')
