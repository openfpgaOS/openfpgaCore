#!/usr/bin/env python3
"""Use existing RSP dirty events rather than comparing contexts every LOAD."""
import difflib
import json
from phase10_experiment import CAMP,sha,write
BASE=CAMP/'phase10/raw-producer-v1';OUT=CAMP/'phase10/raw-producer-v2'
def main():
    inputs={n:(BASE/n).read_text() for n in json.loads((BASE/'build-normal.json').read_text())['inputs']}
    original=s=inputs['gfx_pc.c']
    def replace(a,b):
        nonlocal s
        assert s.count(a)==1,a;s=s.replace(a,b)
    replace('static unsigned raw_dry_context_valid;','static unsigned raw_dry_context_valid,raw_dry_context_dirty;')
    replace('raw_dry_next=NULL;raw_dry_context_valid=0;','raw_dry_next=NULL;raw_dry_context_valid=0;raw_dry_context_dirty=14;')
    replace('    if((raw_dry_context_valid&mask)&&!memcmp(previous,current,n*4))return;','''    if((raw_dry_context_valid&mask)&&!(raw_dry_context_dirty&mask)) {
        if(op!=3 || (previous[0]==current[0] && previous[1]==current[1] && previous[2]==current[2]))return;
    }''')
    replace('    raw_dry_context_valid|=mask;raw_dry_stats[op]++;','    raw_dry_context_valid|=mask;raw_dry_context_dirty&=~mask;raw_dry_stats[op]++;')
    replace('''    memcpy(matrix,rsp.MP_matrix,64);memcpy(matrix+16,&aspect,4);
    memcpy(light,rsp.current_lights,36);
    memcpy(light+9,rsp.current_lights_coeffs,24);
    memcpy(light+15,rsp.current_lookat_coeffs,24);
    light[21]=rsp.current_num_lights;''','''    if(raw_dry_context_dirty&2) {
        memcpy(matrix,rsp.MP_matrix,64);memcpy(matrix+16,&aspect,4);
        raw_dry_context(1,raw_dry_matrix,matrix,17);
    }
    if(raw_dry_context_dirty&4) {
        memcpy(light,rsp.current_lights,36);
        memcpy(light+9,rsp.current_lights_coeffs,24);
        memcpy(light+15,rsp.current_lookat_coeffs,24);
        light[21]=rsp.current_num_lights;
        raw_dry_context(2,raw_dry_light,light,22);
    }''')
    replace('''    raw_dry_context(1,raw_dry_matrix,matrix,17);
    raw_dry_context(2,raw_dry_light,light,22);
    raw_dry_context(3,raw_dry_controls,controls,3);''','    raw_dry_context(3,raw_dry_controls,controls,3);')
    replace('''    if (n_vertices == 0)
        return;''','''    if (n_vertices == 0)
        return;
#if RAW_DRY_ENABLED
    if(rsp.matrix_changed)raw_dry_context_dirty|=2;
    if((rsp.geometry_mode&G_LIGHTING) && (rsp.lights_changed || ((rsp.geometry_mode&G_TEXTURE_GEN)&&rsp.lookat_changed)))
        raw_dry_context_dirty|=4;
#endif''')
    inputs['gfx_pc.c']=s;OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='v1/gfx_pc.c',tofile='v2/gfx_pc.c')))
    write(OUT/'source.json',dict(parent='raw-producer-v1',source_sha256={n:sha(OUT/n) for n in inputs},dry_producer_only=True,context_strategy='Existing matrix/light dirty events; controls compare3 words. Original renderer remains enabled.'))
    print(OUT)
if __name__=='__main__':main()
