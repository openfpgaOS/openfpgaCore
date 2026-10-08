#!/usr/bin/env python3
"""Bind incremental cross-chunk span overlap against the tested v2 scheduler."""
import csv,json,re
from pathlib import Path
import phase10_span_nextchunk_coupled as c
P=c.P;OUT=c.OUT;sha=c.sha;save=c.save
def main():
    result={};f11=json.loads((P/'gpu-critical-bound-v2/coupled/run-baseline-8/coupled.json').read_text())['cpu_cycles']/800000
    for label in ['simple','combined']:
        model=OUT/label;a=P/'span-lookahead-coupled-v1'/label/'run-window-8';b=model/'run-window-8'
        prep=json.loads((model/'inputs.json').read_text())
        for n,h in prep['sources'].items():assert sha(model/n)==h,n
        build=json.loads((model/'model/build.json').read_text());assert sha(model/'model/obj/Vtb_gpu')==build['simulator_sha256']
        invocation=json.loads((b/'inputs.json').read_text());assert invocation['success']
        for n,h in invocation['results_sha256'].items():assert sha(b/n)==h,n
        assert 'FATAL' not in (b/'run.log').read_text()
        outputs=[]
        for i in range(360,368):
            n=f'frame_{i:05}.bin';assert (a/n).read_bytes()==(b/n).read_bytes()
            outputs.append(dict(frame=i,sha256=sha(b/n),color_exact=True,depth_exact=True))
        for n in ['scene.csv','geometry.csv','culling.csv']:assert (a/n).read_bytes()==(b/n).read_bytes()
        before=json.loads((a/'coupled.json').read_text())['cpu_cycles']/800000
        after=json.loads((b/'coupled.json').read_text())['cpu_cycles']/800000
        oldc=list(csv.DictReader((a/'counters.csv').open()))[-1];newc=list(csv.DictReader((b/'counters.csv').open()))[-1]
        counters={}
        for kind,i,n in re.findall(r'CRITICAL_(\w+) (\d+) (\d+)',(b/'run.log').read_text()):counters.setdefault(kind,{})[int(i)]=int(n)
        result[label]=dict(baseline_v2_ms=before,candidate_v3_ms=after,saved_ms=before-after,time_reduction_pct=100*(1-after/before),
            actual_render_outputs=outputs,all368_scene_geometry_culling_exact=True,
            gpu_busy_ms=[int(oldc['all_gpu_busy'])/800000,int(newc['all_gpu_busy'])/800000],
            rdptr_polls=[int(oldc['rdptr_polls']),int(newc['rdptr_polls'])],critical_counts=counters,
            source_manifest_sha256=sha(model/'inputs.json'),run_manifest_sha256=sha(b/'inputs.json'))
    save(OUT/'result.json',dict(passed=True,results=result,combined_vs_original_f11_time_reduction_pct=100*(1-result['combined']['candidate_v3_ms']/f11),
        limits=['Same eight fixed-step frames; CPU timing estimated, LOAD/GPU/cache/memory handshakes actualRTL.',
            'Combined CRAM geometry source still assumes free startup preload/address remapping. This test inherits that optimistic bound.',
            'No nativefullCPU timing, broadgame qualification, FPGAfit, physical timing or deployment. Separate adversarial validation remains required.']))
    (OUT/'REPORT.txt').write_text('''Cross-chunk lookahead v3 compared with tested intra-chunk v2 scheduler.

Simple F11:37.33883875 ->37.11836875ms/frame;0.220470ms saved.
Combined LOD4+CRAM:34.87629250 ->34.65349625ms/frame;0.22279625ms saved.
Combined total versus retained F11:8.391% lower modeled frame time.

All eight actual color/depth buffers and all368 scene/geometry/culling records
match each parent. Actual GPU busy improves another0.776630ms/frame in the
simple model, while CPU scheduling reduces the end-to-end gain to0.220470ms.

Frozen candidate:span-lookahead-v3/gpu_core.v,
SHA ac840c18f263714257eebbb219486d9d345657c4db52bbbc69dd2ec6b4032a5d.
Actual hardware fit/timing and separate adversarial qualification are not
covered. Combined model still excludes source-pool startup/remapping overhead.
No production files or deployment changed.
''')
    save(OUT/'final-manifest.json',dict(files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},helpers={Path(__file__).name:sha(Path(__file__)),Path(c.__file__).name:sha(Path(c.__file__))}))
    print(json.dumps({k:{x:v[x] for x in ['baseline_v2_ms','candidate_v3_ms','saved_ms','time_reduction_pct']} for k,v in result.items()},indent=2))
if __name__=='__main__':main()
