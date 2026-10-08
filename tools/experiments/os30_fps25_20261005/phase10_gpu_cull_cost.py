#!/usr/bin/env python3
"""Exploratory GPU cull cost probe. Requires unimplemented 0x54 cull-bit RTL.
The eager qsim ignores these bits; rendering correctness is NOT demonstrated.
"""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005';BASE=CAMP/'phase9/checks-state-f11';OUT=CAMP/'phase10/gpu-cull-cost-v1'
inputs=json.loads((BASE/'build-normal.json').read_text())['inputs'];OUT.mkdir(parents=True)
for name in inputs:(OUT/name).write_bytes((BASE/name).read_bytes())
p=OUT/'gfx_pc.c';s=p.read_text();start=s.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(');end=s.index('\n#endif',start);old=s[start:end];new=old
needle='    unsigned count = 0, consumed = 0;'
new=new.replace(needle,needle+'''
    const unsigned gpu_cull = ((rsp.geometry_mode & G_CULL_FRONT) ? 1u : 0u)
                            | ((rsp.geometry_mode & G_CULL_BACK) ? 2u : 0u);''')
new=new.replace('''            reject[t] = gfx_cpu_batch_reject(v1, v2, v3);''','''            /* Exploratory extension: rasterizer culls fully-inside cached
             * triangles. Every clipped/fallback triangle retains CPU culling. */
            reject[t] = (v1->clip_rej | v2->clip_rej | v3->clip_rej)
                ? gfx_cpu_batch_reject(v1, v2, v3) : 0;''')
new=new.replace('jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15);','jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15) | (gpu_cull << 18);')
new=new.replace('for (unsigned t = checked; t < n; ++t) {','for (unsigned t = 0; t < n; ++t) {')
# The first endif is nested diagnostic; work on entire remaining function instead.
if '(gpu_cull << 18)' not in new:
    end=s.index('\n#define C0',start);old=s[start:end];new=old
    new=new.replace(needle,needle+'''
    const unsigned gpu_cull = ((rsp.geometry_mode & G_CULL_FRONT) ? 1u : 0u)
                            | ((rsp.geometry_mode & G_CULL_BACK) ? 2u : 0u);''')
    new=new.replace('reject[t] = gfx_cpu_batch_reject(v1, v2, v3);','reject[t] = (v1->clip_rej | v2->clip_rej | v3->clip_rej)\n                ? gfx_cpu_batch_reject(v1, v2, v3) : 0;')
    new=new.replace('jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15);','jobs[count++] = s[0] | (s[1] << 5) | (s[2] << 10) | (dirty << 15) | (gpu_cull << 18);')
    new=new.replace('for (unsigned t = checked; t < n; ++t) {','for (unsigned t = 0; t < n; ++t) {')
assert new!=old and '(gpu_cull << 18)' in new;s=s[:start]+new+s[end:];p.write_text(s)
p=OUT/'gfx_gpu.c';s=p.read_text();start=s.index('void gfx_gpu_vtx_cache_batch(');end=s.index('/* Raw geometry path.',start);old=s[start:end];new=old
needle='''                  | (uint32_t)(slot[0] & 0x1fu);'''
assert new.count(needle)==1
new=new.replace(needle,'''                  | (uint32_t)(slot[0] & 0x1fu)
                  | ((job >> 3) & (3u << 15));''')
s=s[:start]+new+s[end:];p.write_text(s)
(OUT/'preparation.json').write_text(json.dumps({'candidate':'gpu-cull-cost-v1','parent':'phase9/checks-state-f11','experimental':True,'rendering_incorrect_on_current_core':True,'requires':'0x54 bits15:16 hardware culling; currently ignored. CPU cost feasibility only, no correctness/performance qualification.','sources':{n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in inputs}},indent=2)+'\n')
print(OUT)
