#!/usr/bin/env python3
"""Validate bounded F9 publication comparisons and separate useful overlap."""
import csv,hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-overlap-20261006/coupled'
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
def rows(p):
    with p.open() as f:return list(csv.DictReader(f))
def label(name,start,t):return f'{name}{"" if name=="baseline" else "-"}{start}-t{t}'
def run(name,start,t):
    p=OUT/('run-'+label(name,start,t));m=load(p/'inputs.json');assert m['success']
    for path,h in m['sha256'].items():assert sha(Path(path))==h,path
    for rel,h in m['results_sha256'].items():assert sha(p/rel)==h,rel
    assert m['start']==start and m['frames']==8 and m['traffic']==t
    status=load(p/'coupled.json');assert status['frames']==status['presented']==8
    assert not status['memory_errors'] and not status['cram1_errors'] and not status['ring_overflow']
    assert status['texture_fills']*4==status['texture_beats']
    expected=CAMP/'phase7/combined-v1/capture-normal'
    for f in ['scene.csv','geometry.csv','culling.csv']:
        actual=rows(p/f);assert actual==rows(expected/f)[:len(actual)],(name,start,t,f)
    frames=rows(p/'frames.csv');gold=rows(expected/'frames.csv')
    assert len(frames)==start+8
    for i,r in enumerate(frames):
        for key in ['host_framecount','gpu_pixels']:
            assert r[key]==gold[i][key],(name,start,t,i,key,r[key],gold[i][key])
    s=[r for r in rows(p/'counters.csv') if r['kind']=='submit'];assert len(s)==8
    d={key:int(s[-1][key])-int(s[0][key]) for key in s[0] if key!='kind'}
    assert d['cycle']==d['all_total'] and d['render_total']==d['all_cpu_render']
    assert d['all_fragment_productive_advance']<=d['all_fragment']-d['all_fragment_stall']
    assert d['render_fragment_productive_advance']<=d['all_fragment_productive_advance']
    es=rows(p/'events.csv')
    submit={int(r['frame']):int(r['cycle']) for r in es if r['kind']=='submit'}
    complete={int(r['frame']):int(r['cycle']) for r in es if r['kind']=='complete'}
    render={int(r['frame']):int(r['cycle']) for r in es if r['kind']=='render'}
    assert set(submit)==set(complete)==set(render)==set(range(start,start+8))
    render_tail=[max(0,complete[i]-render[i+1]) for i in range(start,start+7)]
    game_slack=[render[i+1]-complete[i] for i in range(start,start+7)]
    images={}
    for i in range(start,start+8):
        b=(p/f'frame_{i:05d}.bin').read_bytes();assert len(b)==307200
        images[i]=dict(color=hashlib.sha256(b[:153600]).hexdigest(),depth=hashlib.sha256(b[153600:]).hexdigest())
    reference=CAMP/f'phase7/combined-v1/replays/normal/{t}/frame_{start:05d}.bin'
    b=reference.read_bytes();a=(p/f'frame_{start:05d}.bin').read_bytes()
    assert len(b)==307212 and a[:153600]==b[:153600] and a[153600:]==b[153612:],(name,start,t,'retained frame')
    pub=rows(p/'publishes.csv');sizes=[];last=0
    for r in pub:
        cumulative=int(r['words']);sizes.append(cumulative-last);last=cumulative
    result=dict(name=name,start=start,traffic=t,inputs_sha256=sha(p/'inputs.json'),
        steady_intervals=7,modeled_frame_ms=d['cycle']/7/1e5,
        renderer_entry_to_flip_publication_ms=d['all_cpu_render']/7/1e5,
        gpu_busy_ms=d['all_gpu_busy']/7/1e5,gpu_fully_idle_ms=(d['all_total']-d['all_gpu_busy'])/7/1e5,
        gpu_fully_idle_during_renderer_ms=(d['render_total']-d['render_gpu_busy'])/7/1e5,
        command_idle_empty_during_renderer_ms=d['render_idle_empty']/7/1e5,
        fragment_state_ms=d['all_fragment']/7/1e5,fragment_stall_ms=d['all_fragment_stall']/7/1e5,
        fragment_bubble_ms=(d['all_fragment']-d['all_fragment_stall']-d['all_fragment_productive_advance'])/7/1e5,
        fragment_valid_advance_ms=d['all_fragment_productive_advance']/7/1e5,
        fragment_valid_advance_during_renderer_percent=100*d['render_fragment_productive_advance']/d['all_fragment_productive_advance'],
        final_publication_to_complete_ms=statistics.mean(complete[i]-submit[i] for i in submit)/1e5,
        completion_after_next_render_begin_ms=statistics.mean(render_tail)/1e5,
        minimum_completion_margin_before_next_render_ms=min(game_slack)/1e5,
        publications_per_interval=d['publications']/7,command_words_per_interval=d['uploaded_words']/7,
        all_eight_frame_batch_sizes=dict(count=len(sizes),mean=statistics.mean(sizes),min=min(sizes),max=max(sizes)),
        rdptr_polls=d['rdptr_polls'],fence_polls=d['fence_polls'],counters=d,
        independent_start_frame_color_depth_exact=True,reference_sha256=sha(reference),frames=images,
        workload={i:{key:frames[i][key] for key in ['buffer','gpu_cmd_words','gpu_pixels','hash_cmd']} for i in range(start,start+8)})
    return result
def main():
    manifest=load(OUT/'model/inputs.json')
    for rel,h in manifest['sources'].items():assert sha(OUT/'model'/rel)==h,rel
    for rel,h in load(OUT/'qsim-sources.json').items():assert sha(OUT/'qsim'/rel)==h,rel
    runs=[];comparisons=[]
    for start in [360,500,600]:
        for t in [1,2]:
            b=run('baseline',start,t);runs.append(b)
            for name in ['kick128','kick128-tail']:
                c=run(name,start,t);runs.append(c);assert b['frames']==c['frames'],(name,start,t)
                buffer_changes=[]
                for i in range(start,start+8):
                    bw,cw=b['workload'][i],c['workload'][i]
                    assert all(bw[k]==cw[k] for k in ['gpu_cmd_words','gpu_pixels']),(name,start,t,i)
                    if bw['buffer']==cw['buffer']:assert bw['hash_cmd']==cw['hash_cmd'],(name,start,t,i)
                    else:buffer_changes.append(i)
                comparisons.append(dict(name=name,start=start,traffic=t,baseline_ms=b['modeled_frame_ms'],candidate_ms=c['modeled_frame_ms'],
                    modeled_throughput_gain_percent=100*(b['modeled_frame_ms']/c['modeled_frame_ms']-1),
                    frame_time_delta_ms=c['modeled_frame_ms']-b['modeled_frame_ms'],
                    command_idle_empty_reduction_ms=b['command_idle_empty_during_renderer_ms']-c['command_idle_empty_during_renderer_ms'],
                    exact_color_depth_frames=8,exact_per_frame_command_word_and_pixel_counts=True,
                    raw_command_hash_exact_when_draw_buffer_same=True,presentation_draw_buffer_changes=buffer_changes))
    limits=[
      'CPU instruction/cache time is an approximation with os30 cache sizes and older calibrated/guessed instruction and memory latencies; not CPU RTL or device FPS.',
      'GPU, render cache, arbiter, Pocket SDRAM and CRAM1 upload/fill controllers are retained-A RTL. CPU memory requests do not traverse RTL; GPU/cache contention feedback to CPU is absent. External scanout/audio/extra CPU traffic is synthetic.',
      'The app executes a fixed game update per submitted frame. Kernel interrupt/mixer service costs are absent; 60Hz presentation bookkeeping is modeled.',
      'At frame-boundary handoff SDRAM and fast texture residency are copied from the functional warmup; GPU caches start cold. Handoff through first submission is excluded; all seven subsequent submit-to-submit intervals are retained. First output is independently checked against retained F9 RTL replay.',
      'Renderer scope is gfx_run entry to flip publication and includes guest wait loops. Poll counts are not converted into exact waiting milliseconds.',
      'Fragment-valid-advance counts cycles with at least one valid pipeline stage and no fp_pipe_stall; it is not pixel retirement. Busy, fragment occupancy, stalls and empty pipeline cycles are separate.',
      'Command-idle-empty may include pending memory drain; fully-idle counters additionally require GPU busy clear. No idle count proves an unpublished batch was ready.',
      'GPU tail can overlap next-frame game/audio. Complete GPU time is never added to CPU time.',
      'MMIO calls execute normal guest instruction cost and qsim uncached penalty (base3+extra2 default). Exact CPU fabric latency is not simulated.',
      'The kick128-tail variant also changes instruction layout and I-cache misses. Timing changes cannot be attributed solely to publication overlap.',
      'Modeled display timing can change which free framebuffer is selected. Paired command words/pixels match every frame; raw command hashes match when draw-buffer identities match, and all paired color/depth outputs match. Raw address-bearing command hashes are not claimed identical across changed buffers.'
    ]
    report=dict(passed=True,scope='Current F9 retained-A normal app, bounded publication scheduling experiment',
        retained_core_sha256=manifest['retained_core_sha256'],model_inputs_sha256=sha(OUT/'model/inputs.json'),
        simulator_sha256=sha(OUT/'model/obj/Vtb_gpu'),analyzer_sha256=sha(Path(__file__)),
        runs=runs,comparisons=comparisons,validation=dict(coupled_frames=144,paired_color_depth_frames=96,
        retained_reference_matches=18,scene_geometry_culling_exact=True,
        paired_command_words_pixels_exact=True,paired_same_buffer_command_hashes_exact=True),limits=limits)
    with (OUT/'results.json').open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    lines=['Current F9 / retained A coupled scheduling experiment','',
      'CPU timing is modeled; these are not observed Pocket FPS.','',
      'Candidate        Start Traffic  Baseline ms  Candidate ms  Throughput change']
    for r in comparisons:lines.append(f"{r['name']:16} {r['start']:5} {r['traffic']:7} {r['baseline_ms']:12.5f} {r['candidate_ms']:13.5f} {r['modeled_throughput_gain_percent']:17.4f}%")
    lines+=['','Baseline stage attribution (steady seven intervals):',
      'Start Traffic Render ms  GPU busy ms Fully idle in render ms Productive fragment ms Final tail ms']
    for r in runs:
        if r['name']=='baseline':lines.append(f"{r['start']:5} {r['traffic']:7} {r['renderer_entry_to_flip_publication_ms']:9.4f} {r['gpu_busy_ms']:12.4f} {r['gpu_fully_idle_during_renderer_ms']:23.4f} {r['fragment_valid_advance_ms']:22.4f} {r['final_publication_to_complete_ms']:13.4f}")
    lines+=['','Validation: '+json.dumps(report['validation']),'','Limits:']+['- '+x for x in limits]
    with (OUT/'REPORT.txt').open('x') as f:f.write('\n'.join(lines)+'\n')
    print('\n'.join(lines[:17]))
if __name__=='__main__':main()
