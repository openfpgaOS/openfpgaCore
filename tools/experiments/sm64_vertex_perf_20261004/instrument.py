#!/usr/bin/env python3
"""Add P3 vertex diagnostics to the guarded private copy of the cache build."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/sm64-vertex-perf-20261004'
STAGE = OUT/'SM64'
before = json.loads((OUT/'before.json').read_text())

def read(rel):
    p=STAGE/rel
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before[rel],rel
    return p,p.read_text()

def replace(s,old,new):
    assert s.count(old)==1,(old,s.count(old))
    return s.replace(old,new)

p,s=read('src/sm64/pocket/perf_pocket.h')
s=replace(s,'    SM64_PERF_WAIT, SM64_PERF_BUCKETS','''    SM64_PERF_WAIT,
    /* Subdivide only time previously charged to VTX. Keep the first nine
     * categories and the first 30 snapshot words compatible with P2. */
    SM64_PERF_VTX_MATRIX, SM64_PERF_VTX_PREP,
    SM64_PERF_VTX_ATTR, SM64_PERF_VTX_CPU, SM64_PERF_BUCKETS''')
s=replace(s,'#if SM64_PERF_DIAG\n','''enum { SM64_PERF_LEGACY_BUCKETS=SM64_PERF_WAIT+1, SM64_PERF_VERTEX_PARTS=5 };
enum Sm64PerfVertexCount {
    SM64_PERF_RAW, SM64_PERF_CPU, SM64_PERF_OUT, SM64_PERF_RECOVER,
    SM64_PERF_VERTEX_COUNTS
};

#if SM64_PERF_DIAG
''')
s=replace(s,'    uint32_t stamp,ticks[SM64_PERF_BUCKETS];','''    uint32_t stamp,ticks[SM64_PERF_BUCKETS];
    uint32_t vertices[SM64_PERF_VERTEX_COUNTS];''')
s=replace(s,'void sm64_perf_render_begin(uint32_t begin);','''/* Route counts use the same sampled frames as the detailed timings. Avoid
 * per-vertex timer reads: counts are added once per load or reconstruction. */
static inline void sm64_perf_count(unsigned route,unsigned count) {
    if(__builtin_expect(sm64_perf_detail_active,0))sm64_perf_detail.vertices[route]+=count;
}
#define SM64_PERF_COUNT(route,count) sm64_perf_count(route,count)

void sm64_perf_render_begin(uint32_t begin);''')
s=replace(s,'#define SM64_PERF_SCOPE(bucket) ((void)0)','#define SM64_PERF_SCOPE(bucket) ((void)0)\n#define SM64_PERF_COUNT(route,count) ((void)0)')
p.write_text(s)

p,s=read('src/sm64/pocket/perf_pocket.c')
s=replace(s,'    uint64_t detail[SM64_PERF_BUCKETS];','''    uint64_t detail[SM64_PERF_BUCKETS];
    uint64_t vertex_counts[SM64_PERF_VERTEX_COUNTS];''')
s=replace(s,'    uint32_t detail_ms10[SM64_PERF_BUCKETS];','''    uint32_t detail_ms10[SM64_PERF_LEGACY_BUCKETS];
    /* P3 appends MTX, PREP, ATTR, CPU, REST and RAW, CPU, OUT, REC.
     * The existing VTX total includes all five parts, rounded only once. */
    uint32_t vertex_ms10[SM64_PERF_VERTEX_PARTS];
    uint32_t vertex_counts[SM64_PERF_VERTEX_COUNTS];''')
s=replace(s,'    memset(sm64_perf_detail.ticks,0,sizeof(sm64_perf_detail.ticks));','''    memset(sm64_perf_detail.ticks,0,sizeof(sm64_perf_detail.ticks));
    memset(sm64_perf_detail.vertices,0,sizeof(sm64_perf_detail.vertices));''')
s=replace(s,'            window.samples++;','''            for(unsigned i=0;i<SM64_PERF_VERTEX_COUNTS;i++)
                window.vertex_counts[i]+=sm64_perf_detail.vertices[i];
            window.samples++;''')
start=s.index('    s->version=2;')
end=s.index('\n}\n\nuint32_t sm64_perf_input',start)
s=s[:start]+'''    s->version=3;s->samples=window.samples;
    uint64_t sample_render=0;
    for(unsigned i=0;i<SM64_PERF_BUCKETS;i++)
        if(i!=SM64_PERF_WAIT)sample_render+=window.detail[i];
    for(unsigned i=0;i<SM64_PERF_LEGACY_BUCKETS;i++)
        s->detail_ms10[i]=window.samples
            ?(window.detail[i]*10000u)/(tick_hz*(uint64_t)window.samples):0;
    static const unsigned parts[SM64_PERF_VERTEX_PARTS]={
        SM64_PERF_VTX_MATRIX,SM64_PERF_VTX_PREP,SM64_PERF_VTX_ATTR,
        SM64_PERF_VTX_CPU,SM64_PERF_VTX};
    uint64_t vertex=0;
    for(unsigned i=0;i<SM64_PERF_VERTEX_PARTS;i++) {
        uint64_t ticks=window.detail[parts[i]];vertex+=ticks;
        s->vertex_ms10[i]=window.samples
            ?(ticks*10000u)/(tick_hz*(uint64_t)window.samples):0;
    }
    s->detail_ms10[SM64_PERF_VTX]=window.samples
        ?(vertex*10000u)/(tick_hz*(uint64_t)window.samples):0;
    for(unsigned i=0;i<SM64_PERF_VERTEX_COUNTS;i++)
        s->vertex_counts[i]=window.samples?window.vertex_counts[i]/window.samples:0;
    s->sample_render_ms10=window.samples
        ?(sample_render*10000u)/(tick_hz*(uint64_t)window.samples):0;'''+s[end:]
s=replace(s,'    print_text(12,210,"PERF P2 CACHE");','    print_text(12,210,"PERF P3 VERT");')
start=s.index('        pair_line(170,"GAME"')
end=s.index('        snprintf(text,sizeof(text),"SMP',start)
s=s[:start]+'''        pair_line(170,"REND",s->render_ms10,"VERT",s->detail_ms10[SM64_PERF_VTX]);
        pair_line(150,"MATR",s->vertex_ms10[0],"PREP",s->vertex_ms10[1]);
        pair_line(130,"ATTR",s->vertex_ms10[2],"CPU",s->vertex_ms10[3]);
        pair_line(110,"REST",s->vertex_ms10[4],"TRI",s->detail_ms10[SM64_PERF_TRI]);
        snprintf(text,sizeof(text),"RAW %u CPU %u",s->vertex_counts[SM64_PERF_RAW],
            s->vertex_counts[SM64_PERF_CPU]);
        print_text(12,90,text);
        snprintf(text,sizeof(text),"OUT %u REC %u",s->vertex_counts[SM64_PERF_OUT],
            s->vertex_counts[SM64_PERF_RECOVER]);
        print_text(12,70,text);
'''+s[end:]
p.write_text(s)

p,s=read('src/sm64/sm64/src/pc/gfx/gfx_pc.c')
s=replace(s,'#define SM64_PERF_SCOPE(bucket) ((void)0)','#define SM64_PERF_SCOPE(bucket) ((void)0)\n#define SM64_PERF_COUNT(route,count) ((void)0)')
for name,next_name,bucket in [('gfx_sp_matrix','gfx_sp_pop_matrix','MATRIX'),
                             ('gfx_sp_pop_matrix','gfx_adjust_x_for_aspect_ratio','MATRIX'),
                             ('gfx_load_geometry_attributes','gfx_flush_vertex_attributes','ATTR')]:
    a=s.index(' '+name+'(');b=s.index(' '+next_name+'(',a)
    part=s[a:b];part=replace(part,'SM64_PERF_SCOPE(SM64_PERF_VTX);',
                           'SM64_PERF_SCOPE(SM64_PERF_VTX_'+bucket+');')
    s=s[:a]+part+s[b:]
a=s.index(' void gfx_sp_vertex(');b=s.index('static inline struct ColorCombiner *gfx_pick_combiner',a)
part=s[a:b]
part=replace(part,'    if (rsp.matrix_changed) {','    if (rsp.matrix_changed) {\n        SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);')
part=replace(part,'    if (rsp.geometry_mode & G_LIGHTING) {\n        if (rsp.lights_changed)',
                  '    if (rsp.geometry_mode & G_LIGHTING) {\n        SM64_PERF_SCOPE(SM64_PERF_VTX_ATTR);\n        if (rsp.lights_changed)')
part=replace(part,'    if(geometry_result==2)return;','    if(geometry_result==2) {SM64_PERF_COUNT(SM64_PERF_OUT,n_vertices);return;}')
part=replace(part,'    if(geometry_result==1) {','    if(geometry_result==1) {\n        SM64_PERF_COUNT(SM64_PERF_RAW,n_vertices);')
part=replace(part,'    // Hoist the loop-invariant MVP matrix into locals:',
'''    SM64_PERF_COUNT(SM64_PERF_CPU,n_vertices);
    { /* Time the complete fallback loop, including its UV/lighting/fog work.
       * No per-vertex clock reads are added. */
    SM64_PERF_SCOPE(SM64_PERF_VTX_CPU);
    // Hoist the loop-invariant MVP matrix into locals:''')
part=replace(part,'#ifdef TARGET_POCKET\n    prof_vtx +=', '    }\n#ifdef TARGET_POCKET\n    prof_vtx +=')
s=s[:a]+part+s[b:]
p.write_text(s)

p,s=read('src/sm64/sm64/src/pc/gfx/gfx_geometry.h')
s=replace(s,'                            float aspect) {\n    geometry_batch_mask=0;',
            '                            float aspect) {\n    SM64_PERF_SCOPE(SM64_PERF_VTX_PREP);\n    geometry_batch_mask=0;')
s=replace(s,'    context=geometry_context_next;','    SM64_PERF_SCOPE(SM64_PERF_VTX_MATRIX);\n    context=geometry_context_next;')
s=replace(s,'    if(!(geometry_pending&bit))return;',
            '    if(!(geometry_pending&bit))return;\n    SM64_PERF_COUNT(SM64_PERF_RECOVER,1);')
p.write_text(s)

p,s=read('src/sm64/sm64/src/pc/gfx/gfx_vertex_cull.h')
s=replace(s,'    if(!(culled_pending&bit))return;',
            '    if(!(culled_pending&bit))return;\n    SM64_PERF_COUNT(SM64_PERF_RECOVER,1);')
p.write_text(s)
print('P3 vertex diagnostics installed in the private source tree.')
