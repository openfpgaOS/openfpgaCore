#!/usr/bin/env python3
"""Check the original two rounded quotients against the candidate frame setter."""
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/division'
HERE=Path(__file__).parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    dest=OUT/'dimension-proof';dest.mkdir(exist_ok=False)
    source=(OUT/'aspect-v1/gfx_pc.c').read_text()
    start=source.index('    if (gfx_current_dimensions.height == 0)',source.index('void gfx_start_frame('))
    end=source.index('\n}',start)
    body=source[start:end]
    body='\n'.join(line for line in body.splitlines() if not line.strip().startswith(('ratio_x =','ratio_y =','inv_ratio_x =','inv_ratio_y =')))
    (dest/'candidate.c').write_text('#include <stdint.h>\nstruct {uint32_t width,height;float aspect_ratio;} gfx_current_dimensions;\nstatic float gfx_vertex_aspect_scale;\nfloat candidate(uint32_t w,uint32_t h) {\ngfx_current_dimensions.width=w;gfx_current_dimensions.height=h;\n'+body+'\nreturn gfx_vertex_aspect_scale;\n}\n')
    # Separate translation units preserve the original already-rounded store
    # boundary, without depending on a native noinline/IPA extension.
    (dest/'baseline.c').write_text('#include <stdint.h>\nfloat baseline_aspect(uint32_t w,uint32_t h){if(!h)h=1;return (float)w/(float)h;}\nfloat baseline_quotient(float aspect){return (4.0f/3.0f)/aspect;}\n')
    (dest/'main.c').write_text('''#include <stdint.h>
#include <stdio.h>
#include <string.h>
float candidate(uint32_t,uint32_t),baseline_aspect(uint32_t,uint32_t),baseline_quotient(float);
static unsigned count;
static int one(uint32_t w,uint32_t h) {
    float a=baseline_quotient(baseline_aspect(w,h)),b=candidate(w,h);
    uint32_t aa,bb;memcpy(&aa,&a,4);memcpy(&bb,&b,4);++count;
    if(aa!=bb){printf("FAIL %u %u %08x %08x\\n",w,h,aa,bb);return 1;}return 0;
}
int main(void) {
    for(uint32_t w=0;w<=2048;++w)for(uint32_t h=0;h<=2048;++h)if(one(w,h))return 1;
    uint32_t state=0x12345678;
    for(unsigned i=0;i<200000;++i){state=state*1664525u+1013904223u;uint32_t w=state;
        state=state*1664525u+1013904223u;if(one(w,state))return 1;}
    uint32_t edge[]={0,1,2,240,320,480,720,1080,1920,2160,3840,65535,16777215,16777216,16777217,0xffffffff};
    for(unsigned i=0;i<sizeof(edge)/4;++i)for(unsigned j=0;j<sizeof(edge)/4;++j)if(one(edge[i],edge[j]))return 1;
    printf("PASS %u exact quotient words\\n",count);return 0;
}
''')
    runs=[]
    for mode,extra in [('strict',[]),('fast',['-ffast-math'])]:
        command=['gcc','-O2',*extra,'-std=c11','baseline.c','candidate.c','main.c','-o',mode]
        build=subprocess.run(command,cwd=dest,capture_output=True,text=True,check=True)
        run=subprocess.run([str(dest/mode)],capture_output=True,text=True,check=True)
        runs.append({'mode':mode,'command':command,'output':run.stdout.strip(),'binary_sha256':sha(dest/mode)})
    record={'passed':True,'candidate_source_sha256':sha(OUT/'aspect-v1/gfx_pc.c'),'script_sha256':sha(Path(__file__)),'input_sha256':{p.name:sha(p)for p in dest.glob('*.c')},'runs':runs,'scope':'Native binary32 round-to-nearest, original two expression boundaries vs extracted candidate setter. 4,398,657 cases per mode including height-zero clamp, dimensions0..2048, unsigned32 randoms and conversion boundaries. SDK codegen independently required; no claim about accrued exception flags.'}
    (dest/'proof.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
if __name__=='__main__':main()
