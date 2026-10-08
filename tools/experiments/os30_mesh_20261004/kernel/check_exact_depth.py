#!/usr/bin/env python3
"""Exhaustively compare reciprocal-assisted depth with original integer divide."""
from pathlib import Path
import json,os,subprocess
HERE=Path(__file__).resolve().parent
OUT=HERE/'results/exact-depth';OUT.mkdir(exist_ok=True)
s=(HERE/'retained_contexts/sm64_lazy_state.h').read_text();a=s.index('static inline uint32_t gfx_mesh_exact_depth(');b=s.index('\n}\n',a)+3
code='''#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <math.h>
'''+s[a:b]+r'''
static uint32_t rng=0xa48fe93c;
static uint32_t next(void) {rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void) {
    unsigned max_delta=0,adjusted=0;uint64_t total_delta=0;
    for(uint32_t den=1024;den<8388608;den++) {
        float w=den*(1.0f/256.0f),reciprocal=1.0f/w;
        uint32_t expected=(UINT64_C(1)<<38)/den;
        uint32_t initial=(uint32_t)(reciprocal*1073741824.0f);
        unsigned delta=initial<expected?expected-initial:initial-expected;
        if(delta>max_delta)max_delta=delta;
        total_delta+=delta;adjusted+=delta!=0;
        uint32_t got=gfx_mesh_exact_depth(den,reciprocal);
        if(got!=expected){fprintf(stderr,"den=%u expected=%u got=%u\n",den,expected,got);return 1;}
        /* One-ULP perturbed estimates still require exact correction. */
        assert(gfx_mesh_exact_depth(den,nextafterf(reciprocal,0.0f))==expected);
        assert(gfx_mesh_exact_depth(den,nextafterf(reciprocal,INFINITY))==expected);
    }
    for(unsigned i=0;i<200000;i++) {
        uint32_t den=next();if(!den)den=1;
        assert(gfx_mesh_exact_depth(den,256.0f/(float)den)==(uint32_t)((UINT64_C(1)<<38)/den));
    }
    for(uint32_t den=1;den<1024;den++)
        assert(gfx_mesh_exact_depth(den,256.0f/den)==(uint32_t)((UINT64_C(1)<<38)/den));
    printf("{\"exhaustive_denominators\":8387584,\"random_general_inputs\":200000,\"small_denominators\":1023,\"perturbed_estimates\":16775168,\"max_exact_corrections\":%u,\"adjusted\":%u,\"total_corrections\":%llu}\n",max_delta,adjusted,(unsigned long long)total_delta);
}
'''
(OUT/'test.c').write_text(code)
results={}
for name,flags in [('optimized',['-O3','-ffast-math','-ffp-contract=off']),('sanitized',['-O1','-fsanitize=address,undefined'])]:
    exe=OUT/name
    subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',*flags,str(OUT/'test.c'),'-lm','-o',str(exe)],check=True)
    output=subprocess.check_output([str(exe)],text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'))
    results[name]=json.loads(output)
(OUT/'result.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
