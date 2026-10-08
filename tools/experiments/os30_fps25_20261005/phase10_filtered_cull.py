#!/usr/bin/env python3
"""Private exact-fallback filtered cull cost experiment; no publication."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase9/checks-state-f11'
OUT=CAMP/'phase10/cull-filter-v1'
inputs=json.loads((BASE/'build-normal.json').read_text())['inputs']
OUT.mkdir(parents=True)
for name in inputs:
    (OUT/name).write_bytes((BASE/name).read_bytes())
p=OUT/'gfx_pc.c';s=p.read_text()
needle='struct TextureHashmapNode {'
addition='''#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
/* Candidate-only rounded NDC memo. Its fast classification is restricted to
 * finite fully-inside positive-W vertices and a conservative nonzero area.
 * Near-zero/exceptional input retains the original arithmetic below. */
static float cpu_cull_ndc[32][2];
static uint32_t cpu_cull_ndc_valid;
#endif

'''
assert s.count(needle)==1;s=s.replace(needle,addition+needle)
needle='        d->w_inv = RSPV_RCP(w);\n'
addition='''#if defined(TARGET_OPENFPGA) && !GFX_GPU_GEOMETRY_WORKER_APP
        if (dest_index < 32) {
            uint32_t wb;
            memcpy(&wb, &w, sizeof(wb));
            const uint32_t bit = 1u << dest_index;
            cpu_cull_ndc_valid &= ~bit;
            /* 2^-32 <= W <= 2^32 excludes exceptional reciprocals. */
            if (d->clip_rej == 0 && wb >= 0x2f800000u && wb <= 0x4f800000u) {
                cpu_cull_ndc[dest_index][0] = x * d->w_inv;
                cpu_cull_ndc[dest_index][1] = y * d->w_inv;
                cpu_cull_ndc_valid |= bit;
            }
        }
#endif
'''
assert s.count(needle)==1;s=s.replace(needle,needle+addition)
start=s.index('static inline int gfx_cpu_batch_reject(')
end=s.index('\n/* A prepared slot',start)
old=s[start:end]
new=old.replace('static inline int gfx_cpu_batch_reject(const struct LoadedVertex *v1,\n                                      const struct LoadedVertex *v2,\n                                      const struct LoadedVertex *v3) {', '''static inline int gfx_cpu_batch_reject(unsigned a, unsigned b, unsigned c) {
    const struct LoadedVertex *v1 = &rsp.loaded_vertices[a];
    const struct LoadedVertex *v2 = &rsp.loaded_vertices[b];
    const struct LoadedVertex *v3 = &rsp.loaded_vertices[c];''')
needle='    if ((rsp.geometry_mode & G_CULL_BOTH) != 0) {\n'
addition='''        if ((a | b | c) < 32) {
            const uint32_t needed = (1u << a) | (1u << b) | (1u << c);
            if ((cpu_cull_ndc_valid & needed) == needed) {
                const float dx1 = cpu_cull_ndc[a][0] - cpu_cull_ndc[b][0];
                const float dy1 = cpu_cull_ndc[a][1] - cpu_cull_ndc[b][1];
                const float dx2 = cpu_cull_ndc[c][0] - cpu_cull_ndc[b][0];
                const float dy2 = cpu_cull_ndc[c][1] - cpu_cull_ndc[b][1];
                const float cross = dx1 * dy2 - dy1 * dx2;
                /* All six coordinates have absolute value <=1+2^-22.
                 * The threshold is >16x the accumulated binary32 roundoff
                 * bound relative to the legacy partially fused DAG. */
                if (cross > 0x1p-16f)
                    return (rsp.geometry_mode & G_CULL_BACK) ? 2 : 0;
                if (cross < -0x1p-16f)
                    return (rsp.geometry_mode & G_CULL_FRONT) ? 2 : 0;
            }
        }
'''
assert new.count(needle)==1;new=new.replace(needle,needle+addition)
assert new!=old;s=s[:start]+new+s[end:]
s=s.replace('gfx_cpu_batch_reject(v1, v2, v3)','gfx_cpu_batch_reject(s[0], s[1], s[2])')
s=s.replace('gfx_cpu_batch_reject(&rsp.loaded_vertices[s[0]],\n                                       &rsp.loaded_vertices[s[1]],\n                                       &rsp.loaded_vertices[s[2]])','gfx_cpu_batch_reject(s[0], s[1], s[2])')
assert 'gfx_cpu_batch_reject(&' not in s
p.write_text(s)
record={'parent':'phase9/checks-state-f11','candidate':'cull-filter-v1','experimental':True,'formal_bound_pending':True,'sources':{n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in inputs}}
(OUT/'preparation.json').write_text(json.dumps(record,indent=2)+'\n')
print(OUT)
