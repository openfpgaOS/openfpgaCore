#!/usr/bin/env python3
"""Seal paired scheduler evidence without extrapolating Pocket performance."""
import csv,json,re
from pathlib import Path
import phase10_span_coupled as c
P=c.P;OUT=c.OUT;sha=c.sha;save=c.save
def main():
    parents={'simple':P/'gpu-critical-bound-v2/coupled/run-baseline-8','combined':P/'cram-source-coupled-v1/run-cram-8'}
    results={};f11=json.loads((parents['simple']/'coupled.json').read_text())['cpu_cycles']/800000
    for label,parent in parents.items():
        d=OUT/label;run=d/'run-window-8';prep=json.loads((d/'inputs.json').read_text())
        for n,h in prep['sources'].items():assert sha(d/n)==h,n
        build=json.loads((d/'model/build.json').read_text());assert sha(d/'model/obj/Vtb_gpu')==build['simulator_sha256']
        inv=json.loads((run/'inputs.json').read_text());assert inv['success']
        for n,h in inv['results_sha256'].items():assert sha(run/n)==h,n
        assert 'FATAL' not in (run/'run.log').read_text()
        outputs=[]
        for f in sorted(parent.glob('frame_*.bin')):
            assert f.read_bytes()==(run/f.name).read_bytes() and f.stat().st_size==307200
            outputs.append(dict(frame=int(f.stem.split('_')[1]),sha256=sha(f),color_exact=True,depth_exact=True))
        assert len(outputs)==8
        for n in ['scene.csv','geometry.csv','culling.csv']:assert (parent/n).read_bytes()==(run/n).read_bytes(),n
        a=json.loads((parent/'coupled.json').read_text());b=json.loads((run/'coupled.json').read_text())
        assert b['frames']==b['presented']==8 and b['cram1_errors']==0
        before=a['cpu_cycles']/800000;after=b['cpu_cycles']/800000
        ac=list(csv.DictReader((parent/'counters.csv').open()))[-1];bc=list(csv.DictReader((run/'counters.csv').open()))[-1]
        groups={}
        for kind,i,n in re.findall(r'CRITICAL_(\w+) (\d+) (\d+)',(run/'run.log').read_text()):groups.setdefault(kind,{})[int(i)]=int(n)
        results[label]=dict(baseline_ms=before,candidate_ms=after,saved_ms=before-after,time_reduction_pct=100*(1-after/before),
            gpu_busy_ms=[int(ac['all_gpu_busy'])/800000,int(bc['all_gpu_busy'])/800000],rdptr_polls=[int(ac['rdptr_polls']),int(bc['rdptr_polls'])],
            actual_render_outputs=outputs,all368_scene_geometry_culling_exact=True,critical_counter_cycles=groups,
            input_manifest_sha256=sha(d/'inputs.json'),run_manifest_sha256=sha(run/'inputs.json'),simulator_sha256=sha(d/'model/obj/Vtb_gpu'))
    save(OUT/'result.json',dict(passed=True,private_only=True,results=results,
        combined_vs_original_f11_time_reduction_pct=100*(1-results['combined']['candidate_ms']/f11),
        limits=['Actual unchanged-command GPU/cache/SDRAM/CRAM1 RTL with modeled CPU time; eight fixed-step frames only.',
            'Combined path retains real LOAD/BRAM alias and shared CRAM1 source transport, but pool startup upload and CPU address-remap cost are excluded as in its parent.',
            'No nativeCPU fullsystem measurement, fit, timing or deployment. Correctness evidence covers these eight actual buffers; separate adversarial component qualification is required.',
            'Lookahead handoff count is inferred from fewer main-FSM EMIT states, not an independent dedicated counter. Mainspan setup is concurrent under FRAG and cannot be summed with its new FRAG accounting.']))
    report='''One-span lookahead paired test, eight actual frames360..367

Both simple F11 and LOD4+CRAM source pairs retain exact color/depth buffers and
all368 scene/geometry/culling records. Candidate GPU source is span-lookahead-v2
SHA d9e58835f5c2e8dacfcba8ac60a4cb905e523471e566df3293cd0542708530c7.

Simple F11:37.82757375 ->37.33883875 ms/frame (0.488735ms saved,1.292% time).
LOD4+CRAM:35.96002250 ->34.87629250 ms/frame (1.083730ms saved,3.014% time).
Combined is7.802% lower modeled time than original F11.

On simple F11, GPU busy23.67252375 ->21.62072000ms/frame. Main span setup phases
shrink1.31290875 ->0.38369875ms. NormalEMIT count82011 ->23923 implies58088
lookahead handoffs,70.83% of emitted spans. Exposed perspective issue stalls
fall3.18922125 ->1.96967625ms. Source-finished drain5.64844125 ->2.96586875ms.
These categories overlap; they are not additive independent frame savings.

This supplies a real complementary reduction in GPU waiting; the simple-core
CPU floor masks much of it. It is still a private modeled-performance test.
The combined model excludes geometry-pool startup upload and CPU remapping.
No fit/timing qualification or Pocket deployment has occurred.
'''
    (OUT/'REPORT.txt').write_text(report)
    save(OUT/'final-manifest.json',dict(files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},helpers={Path(__file__).name:sha(Path(__file__)),Path(c.__file__).name:sha(Path(c.__file__))}))
    print(json.dumps({k:{n:v[n] for n in ('baseline_ms','candidate_ms','saved_ms','time_reduction_pct')} for k,v in results.items()},indent=2))
if __name__=='__main__':main()
