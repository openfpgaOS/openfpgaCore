#!/usr/bin/env python3
"""Independent exact-scope review of the frozen first culling candidate."""
from pathlib import Path
import hashlib, json

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005'
AUDIT=OUT/'independent-audit'
CAND=AUDIT/'candidate1-inputs'
BASE=OUT/'source-baseline'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def once(s,a,b):
    assert s.count(a)==1
    return s.replace(a,b)

g=(CAND/'gfx_geometry.h').read_text()
start=g.index('/* With software transforms, confirm only a prospective reject plane')
end=g.index('static __attribute__((noinline)) int geometry_prepare(',start)
helper=g[start:end]
assert 'memcpy(&cb,&coordinate,sizeof(cb));memcpy(&wb,&w,sizeof(wb));' in helper
assert 'if(positive ? !(coordinate>w) : !(coordinate < -w))return false;' in helper
g=g[:start]+g[end:]
g=once(g,'    const bool hardware_geometry=gfx_gpu_geometry_enabled();\n    if (first >= 32)\n',
         '    if (!gfx_gpu_geometry_enabled() || first >= 32)\n')
g=once(g,'''    if(outside && (hardware_geometry || geometry_confirm_outside(vertices,count,aspect,outside))) {
        vertex_cull_capture(vertices,count,first,aspect,outside);return 2;
    }
    /* Coarse software rejection is independent of the hardware transform
     * capability. All accepted software batches still use the ordinary CPU. */
    if(!hardware_geometry)return 0;
''','    if(outside) {vertex_cull_capture(vertices,count,first,aspect,outside);return 2;}\n')
assert g==(BASE/'gfx_geometry.h').read_text()
p=(CAND/'gfx_pc.c').read_text()
p=once(p,'''    if(culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        /* A triangle may mix deferred low slots with ordinary slots 32+. */
        if(vtx1_idx<32)vertex_cull_materialize(vtx1_idx);
        if(vtx2_idx<32)vertex_cull_materialize(vtx2_idx);
        if(vtx3_idx<32)vertex_cull_materialize(vtx3_idx);
    }
''','''    if((vtx1_idx|vtx2_idx|vtx3_idx)<32 && culled_pending) {
        if(v1->clip_rej&v2->clip_rej&v3->clip_rej)return;
        vertex_cull_materialize(vtx1_idx);vertex_cull_materialize(vtx2_idx);vertex_cull_materialize(vtx3_idx);
    }
''')
assert p==(BASE/'gfx_pc.c').read_text()
assert (CAND/'gfx_vertex_cull.h').read_bytes()==(BASE/'gfx_vertex_cull.h').read_bytes()
codegen=json.loads((AUDIT/'codegen-manifest.json').read_text())
host=json.loads((CAND/'manifest.json').read_text())
assert codegen['passed'] and host['passed']
sources={n:sha(CAND/n) for n in ['gfx_geometry.h','gfx_pc.c','gfx_vertex_cull.h']}
assert codegen['source_sha256']==sources and host['source_files']==sources
result=dict(passed=True,source_sha256=sources,scope='Independent source/state/actual target instruction audit; candidate1 only.',
    exact_reverse_to_baseline=True,
    evidence={'target_codegen_sha256':sha(AUDIT/'codegen-manifest.json'),
              'host_actual_function_sha256':sha(CAND/'manifest.json'),
              'review_sha256':sha(AUDIT/'REVIEW.txt')},
    status='Correctness audit passed; candidate1 is not selected because controlled performance regressed.',
    script_sha256=sha(Path(__file__)),
    limitations=['Host tests are sampled, not exhaustive floating-point proof.',
                'Target instruction-DAG equivalence is tied to the recorded binaries/compiler.',
                'No independent whole-GPU runtime claim is made here; validator owns rendering replay.'])
(AUDIT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
print('PASS: candidate1 exact scope, state review and target arithmetic bindings.')
