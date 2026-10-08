#!/usr/bin/env python3
"""Shadow a concrete PRECHECK/DRAW/END protocol while F11 still renders."""
import difflib
import json
from pathlib import Path
from phase10_experiment import CAMP,sha,write
BASE=CAMP/'phase10/raw-producer-v2';OUT=CAMP/'phase10/run-producer-v1'
RUN=r'''
#if RAW_DRY_ENABLED
static const Gfx *raw_run_request;
static uint32_t raw_run_initial_stale;
static __attribute__((noinline)) void raw_dry_run(const Gfx *cmd) {
    /* The worker parses immutable Gfx directly, stopping at64 triangles or
     * the first non-TRI command. CPU does not scan/repack the index stream. */
    const uintptr_t p=(uintptr_t)cmd,end=(uintptr_t)__EH_FRAME_BEGIN__;
    raw_run_request=NULL;
    if(p<(uintptr_t)&_start||p>=end||sizeof(Gfx)>end-p){raw_dry_stats[7]++;return;}
    uint32_t *w=raw_dry_reserve(4);if(!w)return;
    w[0]=0x05000040u;w[1]=p;w[2]=rsp.geometry_mode&G_CULL_BOTH;w[3]=raw_dry_owned;
    raw_run_request=cmd;raw_run_initial_stale=vc_slot_stale;
    raw_dry_stats[5]++;
}
static __attribute__((noinline)) void raw_dry_run_finish(const struct ColorCombiner *comb,
                                                        float uofs,float vofs,float bias) {
    if(!raw_run_request)return;
    if(comb) {
        /* DRAW uniforms follow the precheck response. Sticky GPU surface and
         * viewport packets remain the existing backend's responsibility.
         * A real transport forwards those before committing this RUN. */
        uint32_t *w=raw_dry_reserve(9);if(!w)return;
        unsigned rgb=triangle_vc_rgb_input<0?255u:comb->shader_input_mapping[0][triangle_vc_rgb_input];
        unsigned alpha=triangle_vc_alpha_input<0?8u:comb->shader_input_mapping[1][triangle_vc_alpha_input];
        w[0]=0x06000008u;w[1]=(uintptr_t)raw_run_request;
        w[2]=1u|((uint32_t)triangle_vc_elig<<8)|((uint32_t)triangle_material_fog<<16)|(alpha<<20)|(rgb<<24);
        w[3]=raw_run_initial_stale;
        memcpy(w+4,&rdp.prim_color,4);memcpy(w+5,&rdp.env_color,4);
        memcpy(w+6,&uofs,4);memcpy(w+7,&vofs,4);memcpy(w+8,&bias,4);
        raw_dry_stats[17]++;
    }
    uint32_t *w=raw_dry_reserve(2);if(!w)return;
    w[0]=0x07000001u;w[1]=(uintptr_t)raw_run_request;
    raw_dry_stats[18]++;raw_run_request=NULL;
}
#endif
'''
def main():
    inputs={n:(BASE/n).read_text() for n in json.loads((BASE/'build-normal.json').read_text())['inputs']}
    original=s=inputs['gfx_pc.c']
    a=s.index('#if RAW_DRY_ENABLED\nstruct RawDryRun');b=s.index('\nstatic void gfx_geometry_submit',a)
    s=s[:a]+RUN+s[b:]
    assert s.count('RAW_DRY_WORDS=8192')==1;s=s.replace('RAW_DRY_WORDS=8192','RAW_DRY_WORDS=16384')
    # Capture the post-material cache invalidation before the original CPU scan
    # mutates its local stale mask. The live protocol receives it once per RUN.
    old='            stale = vc_slot_stale;';assert s.count(old)==1
    s=s.replace(old,old+'\n#if RAW_DRY_ENABLED\n            raw_run_initial_stale=stale;\n#endif')
    start=s.index('static __attribute__((noinline)) Gfx *gfx_cpu_indexed_run(')
    pos=s.index('submit:\n    if (count) {',start)
    s=s[:pos]+s[pos:].replace('submit:\n    if (count) {','submit:\n#if RAW_DRY_ENABLED\n    raw_dry_run_finish(comb,uofs,vofs,bias);\n#endif\n    if (count) {',1)
    inputs['gfx_pc.c']=s;OUT.mkdir(exist_ok=False)
    for n,c in inputs.items():(OUT/n).write_text(c)
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),s.splitlines(True),fromfile='raw-producer-v2/gfx_pc.c',tofile='run-producer-v1/gfx_pc.c')))
    write(OUT/'source.json',dict(parent='raw-producer-v2',source_sha256={n:sha(OUT/n) for n in inputs},helper_sha256=sha(Path(__file__)),shadow_only=True,original_rendering_enabled=True,protocol='PRECHECK_RUN immutable pointer/cull/ownedmask; optional DRAW uniforms; END. Original CPU scan supplies shadow completion; host independently reconstructs it.',limits=['No hardware transport or speedup claim.','Geometry precheck and commit completion latencies are not measured; CPU original rendering still runs.','Surface/viewport commands are still original backend packets; DRAW carries only vertex preparation uniforms.']))
    print(OUT)
if __name__=='__main__':main()
