#!/usr/bin/env python3
"""Lighter filtered cull candidate: use clip-OR and area range, no valid bitset."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
BASE=CAMP/'phase10/cull-filter-v1'
OUT=CAMP/'phase10/cull-filter-v2'
inputs=json.loads((BASE/'build-normal.json').read_text())['inputs']
OUT.mkdir(parents=True)
for name in inputs:(OUT/name).write_bytes((BASE/name).read_bytes())
p=OUT/'gfx_pc.c';s=p.read_text()
s=s.replace('static uint32_t cpu_cull_ndc_valid;\n','')
start=s.index('        if (dest_index < 32) {\n            uint32_t wb;')
end=s.index('\n#endif',start)
s=s[:start]+'''        if (dest_index < 32) {
            cpu_cull_ndc[dest_index][0] = x * d->w_inv;
            cpu_cull_ndc[dest_index][1] = y * d->w_inv;
        }'''+s[end:]
s=s.replace('''        if ((a | b | c) < 32) {
            const uint32_t needed = (1u << a) | (1u << b) | (1u << c);
            if ((cpu_cull_ndc_valid & needed) == needed) {''','''        if ((a | b | c) < 32) {
            if ((v1->clip_rej | v2->clip_rej | v3->clip_rej) == 0) {''')
s=s.replace('''                if (cross > 0x1p-16f)
                    return (rsp.geometry_mode & G_CULL_BACK) ? 2 : 0;
                if (cross < -0x1p-16f)
                    return (rsp.geometry_mode & G_CULL_FRONT) ? 2 : 0;''','''                if (cross > 0x1p-16f && cross < 8.0f)
                    return (rsp.geometry_mode & G_CULL_BACK) ? 2 : 0;
                if (cross < -0x1p-16f && cross > -8.0f)
                    return (rsp.geometry_mode & G_CULL_FRONT) ? 2 : 0;''')
s=s.replace(' * finite fully-inside positive-W vertices and a conservative nonzero area.', ' * fully-inside vertices and a conservative finite nonzero area.')
p.write_text(s)
(OUT/'preparation.json').write_text(json.dumps({'candidate':'cull-filter-v2','parent':'phase9/checks-state-f11','experimental':True,'formal_bound_pending':True,'sources':{n:hashlib.sha256((OUT/n).read_bytes()).hexdigest() for n in inputs}},indent=2)+'\n')
print(OUT)
