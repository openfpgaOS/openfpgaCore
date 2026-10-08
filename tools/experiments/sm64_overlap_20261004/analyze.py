#!/usr/bin/env python3
"""Validate paired coupled runs and report overlap without claiming device FPS."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-overlap-20261004'
PRIOR=ROOT/'docs/measurements/sm64-vertex-perf-20261004'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
rows=lambda p:list(csv.DictReader(p.open()))

def run_result(kind,start,traffic):
    p=OUT/f'run-{kind[0]}{start}-t{traffic}'
    manifest=load(p/'inputs.json')
    assert manifest['success'] and manifest['kind']==kind
    for name,digest in manifest['sha256'].items(): assert sha(Path(name))==digest,name
    e=rows(p/'events.csv');c=rows(p/'counters.csv')
    assert len([x for x in e if x['kind']=='complete'])==8
    assert len([x for x in e if x['kind']=='present'])==8
    for r in c:
        for scope in ['all','render']:
            n=lambda k:int(r[scope+'_'+k])
            if kind=='worker':
                assert sum(n(k) for k in ['input_starved','output_blocked','vertex_math','cull_math','worker_other'])==n('total')
                assert n('math_fragment_overlap')<=n('math_raster_overlap')<=n('vertex_math')+n('cull_math')<=n('total')
            assert n('fragment_stall')<=n('fragment')<=n('total')
        assert int(r['render_total'])==int(r['all_cpu_render'])
        assert int(r['render_total'])<=int(r['all_total'])
        assert int(r['max_upload'])<=4095
        if kind=='worker': assert int(r['max_output'])<=511
    # Exclude the handoff interval: use seven complete submit-to-submit periods.
    s=[r for r in c if r['kind']=='submit'];assert len(s)==8
    d={k:int(s[-1][k])-int(s[0][k]) for k in s[0] if k!='kind'}
    assert d['cycle']==d['all_total']
    total=d['all_total'];math=d['all_vertex_math']+d['all_cull_math']
    submit={int(r['token']):int(r['cycle']) for r in e if r['kind']=='submit'}
    complete={int(r['token']):int(r['cycle']) for r in e if r['kind']=='complete'}
    assert submit.keys()==complete.keys()
    status=load(p/'coupled.json')
    assert status['memory_errors']==0 and not status['ring_overflow']
    result=dict(kind=kind,start=start,traffic=traffic,intervals=7,
        modeled_frame_ms=total/7/100000,cpu_command_renderer_ms=d['all_cpu_render']/7/100000,
        gpu_busy_ms=d['all_gpu_busy']/7/100000,
        final_batch_tail_ms=statistics.mean(complete[t]-submit[t] for t in submit)/100000,
        worker_math_ms=math/7/100000,
        worker_math_pipeline_overlap_percent=100*d['all_math_raster_overlap']/math if math else None,
        worker_math_fragment_overlap_percent=100*d['all_math_fragment_overlap']/math if math else None,
        rdptr_polls=d['rdptr_polls'],fence_polls=d['fence_polls'],
        max_upload=int(s[-1]['max_upload']),max_output=int(s[-1]['max_output']),
        counters=d,frames={})
    if kind=='worker':
        result['worker_fraction_percent']={k:100*d['all_'+k]/total for k in
            ['input_starved','output_blocked','vertex_math','cull_math','worker_other']}
        result['worker_during_cpu_renderer_percent']={k:100*d['render_'+k]/d['render_total'] for k in
            ['input_starved','output_blocked','vertex_math','cull_math','worker_other']}
    reference=rows(PRIOR/'capture-final-hidden/scene.csv')
    scene=rows(p/'scene.csv');assert scene==reference[:len(scene)]
    gold=[r for r in load(PRIOR/'replay-results-final-hidden-final-panel-worker.json')
        if r['label']=='final-hidden' and r['mode']==1 and r['frame']==start][0]
    for f in range(start,start+8):
        data=(p/f'frame_{f:05}.bin').read_bytes();assert len(data)==307200
        color=hashlib.sha256(data[:153600]).hexdigest();depth=hashlib.sha256(data[153600:]).hexdigest()
        result['frames'][f]=dict(color=color,depth=depth)
        if f==start:
            assert color==gold['framebuffer_sha256'] and depth==gold['depth_sha256'],(kind,start,traffic)
    return result

def hotspots():
    p=OUT/'run-w360-t1'
    rd=lambda path:{r['addr']:r for r in csv.DictReader(path.open(),delimiter='\t')}
    a=rd(p/'profile-0360.tsv');b=rd(p/'profile.tsv')
    result=[]
    for addr,r in b.items():
        cycles=int(r['self_cycles'])-int(a.get(addr,{}).get('self_cycles',0))
        assert cycles>=0
        if cycles:result.append(dict(function=r['function'],address=addr,self_ms_per_frame=cycles/8/100000))
    return sorted(result,key=lambda r:r['self_ms_per_frame'],reverse=True)[:16]

def main():
    output=dict(scope='SM64 Bob-omb Battlefield, three deterministic eight-frame windows, paired GPU RTL, two memory schedules.',
        clock_hz=100000000,runs=[],comparisons=[],validation={},cpu_hotspots=hotspots())
    for traffic in [1,2]:
        for start in [360,500,600]:
            w=run_result('worker',start,traffic);d=run_result('direct',start,traffic)
            assert w['frames']==d['frames'],(start,traffic)
            output['runs'] += [w,d]
            output['comparisons'].append(dict(start=start,traffic=traffic,
                direct_ms=d['modeled_frame_ms'],worker_ms=w['modeled_frame_ms'],
                modeled_throughput_gain_percent=100*(d['modeled_frame_ms']/w['modeled_frame_ms']-1),
                gpu_busy_reduction_ms=d['gpu_busy_ms']-w['gpu_busy_ms'],
                color_and_depth_identical=True))
    for kind in ['worker','direct']:
        manifest=load(OUT/kind/'manifest.json')
        for name,digest in manifest['sources'].items():assert sha(OUT/kind/name)==digest,name
    for name,digest in load(OUT/'qsim-sources.json').items():
        assert sha(OUT/'qsim'/name)==digest,name
    output['validation']=dict(coupled_frames=96,paired_color_depth_matches=48,
        retained_reference_color_depth_matches=12,scene_trace_identical=True,
        counter_partitions_exact=True,all_fences_and_presentations_completed=True,
        ring_overflows=0,sdram_protocol_timing_errors=0,frozen_sources_verified=True)
    output['limitations']=[
        'CPU instruction/cache timing is a model, not VexiiRiscv RTL. Absolute hardware FPS is not predicted.',
        'Game input and clock retain one fixed update per submitted frame; no actual 30 Hz pacing or catch-up workload is inferred.',
        'GPU, worker, color/depth cache, SDRAM arbiter/controller and SDRAM pin timing use the retained installed worker-v6 RTL at 100 MHz.',
        'Schedule 1 adds synthetic scanout/audio reads. Schedule 2 additionally injects synthetic CPU reads; real CPU bus requests/latency feedback and mixer interrupt costs are not coupled.',
        'The software GPU stays on separate original memory for command/frame accounting only; the CPU uses RTL memory and reads real RTL GPU progress. Only RTL framebuffer/depth dumps are validated.',
        'Pipeline overlap includes command consumption, setup, clear/draw work and memory stalls. Fragment-only overlap is reported separately.',
        'Final-batch tail excludes upload-ring stalls earlier in the frame. GPU busy time is not added to CPU time.',
        'The installed worker snapshot precedes the separate edge-overflow repair; no fitted hardware, deployment, seed sweep or production app change is part of this experiment.'
    ]
    (OUT/'results.json').write_text(json.dumps(output,indent=2)+'\n')
    lines=['SM64 geometry overlap simulation — 2026-10-04','',
        'The independent worker overlaps GPU work, but it removes little from the',
        'modeled critical path. CPU preparation and command production remain dominant.','',
        'Paired fixed-workload results (milliseconds; these are not Pocket FPS):',
        'Schedule  Start    Direct    Worker    Throughput gain    GPU busy reduction']
    for r in output['comparisons']:
        lines.append(f"{r['traffic']:8d}  {r['start']:5d}  {r['direct_ms']:8.3f}  {r['worker_ms']:8.3f}"
            f"  {r['modeled_throughput_gain_percent']:16.3f}%  {r['gpu_busy_reduction_ms']:18.3f}")
    lines += ['','Worker utilization (all submit-to-submit time, excluding first handoff):',
        'Schedule Start  Math ms  Pipeline overlap  Fragment overlap  Input wait  Output blocked']
    for r in output['runs']:
        if r['kind']!='worker':continue
        f=r['worker_fraction_percent']
        lines.append(f"{r['traffic']:8d} {r['start']:5d} {r['worker_math_ms']:8.3f}"
            f" {r['worker_math_pipeline_overlap_percent']:16.2f}% {r['worker_math_fragment_overlap_percent']:16.2f}%"
            f" {f['input_starved']:10.2f}% {f['output_blocked']:14.2f}%")
    lines += ['','Remaining CPU hotspots, entrance window (self time, excludes callees):']
    for r in output['cpu_hotspots'][:10]:lines.append(f"  {r['self_ms_per_frame']:6.3f} ms  {r['function']}")
    lines += ['','Interpretation:',
        '  Faster transform arithmetic alone has limited remaining value: most of it',
        '  already runs alongside downstream GPU work. A larger output FIFO can',
        '  let the worker run further ahead, but blocked time alone is not evidence',
        '  that more buffering improves the frame critical path.',
        '  Larger gains need less CPU eligibility/bounds work, lighting/attribute',
        '  preparation, triangle/clipping processing and command packing. Moving',
        '  a coherent part of that preparation would differ from accelerating the',
        '  matrix/project operations already handled by this worker.','',
        'Validation:',json.dumps(output['validation'],indent=2),'','Limits:']
    lines.extend('  '+s for s in output['limitations'])
    lines += ['','Reproduce with tools/experiments/sm64_overlap_20261004/{build.py,run.py,analyze.py}.',
        'Raw events, counters, 0.1 ms timeline bins, profiles and rendered frames',
        'are retained in run-{w,d}{360,500,600}-t{1,2}/.']
    (OUT/'REPORT.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:15]))

if __name__=='__main__':main()
