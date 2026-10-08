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
def run(name,start,t,count=8,suffix=''):
    p=OUT/('run-'+label(name,start,t)+suffix);m=load(p/'inputs.json');assert m['success']
    for path,h in m['sha256'].items():assert sha(Path(path))==h,path
    for rel,h in m['results_sha256'].items():assert sha(p/rel)==h,rel
    assert m['start']==start and m['frames']==count and m['traffic']==t
    status=load(p/'coupled.json');assert status['frames']==status['presented']==count
    assert not status['memory_errors'] and not status['cram1_errors'] and not status['ring_overflow']
    assert status['texture_fills']*4==status['texture_beats']
    expected=CAMP/'phase7/combined-v1/capture-normal'
    for f in ['scene.csv','geometry.csv','culling.csv']:
        actual=rows(p/f);assert actual==rows(expected/f)[:len(actual)],(name,start,t,f)
    frames=rows(p/'frames.csv');gold=rows(expected/'frames.csv')
    assert len(frames)==start+count
    for i,r in enumerate(frames):
        for key in ['host_framecount','gpu_pixels']:
            assert r[key]==gold[i][key],(name,start,t,i,key,r[key],gold[i][key])
    s=[r for r in rows(p/'counters.csv') if r['kind']=='submit'];assert len(s)==count
    d={key:int(s[-1][key])-int(s[0][key]) for key in s[0] if key!='kind'}
    assert d['cycle']==d['all_total'] and d['render_total']==d['all_cpu_render']
    assert d['all_fragment_productive_advance']<=d['all_fragment']-d['all_fragment_stall']
    assert d['render_fragment_productive_advance']<=d['all_fragment_productive_advance']
    es=rows(p/'events.csv')
    submit={int(r['frame']):int(r['cycle']) for r in es if r['kind']=='submit'}
    complete={int(r['frame']):int(r['cycle']) for r in es if r['kind']=='complete'}
    render={int(r['frame']):int(r['cycle']) for r in es if r['kind']=='render'}
    assert set(submit)==set(complete)==set(render)==set(range(start,start+count))
    render_tail=[max(0,complete[i]-render[i+1]) for i in range(start,start+count-1)]
    game_slack=[render[i+1]-complete[i] for i in range(start,start+count-1)]
    images={}
    for i in range(start,start+count):
        b=(p/f'frame_{i:05d}.bin').read_bytes();assert len(b)==307200
        images[i]=dict(color=hashlib.sha256(b[:153600]).hexdigest(),depth=hashlib.sha256(b[153600:]).hexdigest())
    reference=CAMP/f'phase7/combined-v1/replays/normal/{t}/frame_{start:05d}.bin'
    b=reference.read_bytes();a=(p/f'frame_{start:05d}.bin').read_bytes()
    assert len(b)==307212 and a[:153600]==b[:153600] and a[153600:]==b[153612:],(name,start,t,'retained frame')
    pub=rows(p/'publishes.csv');sizes=[];last=0
    for r in pub:
        cumulative=int(r['words']);sizes.append(cumulative-last);last=cumulative
    intervals=count-1
    result=dict(name=name,start=start,traffic=t,inputs_sha256=sha(p/'inputs.json'),
        steady_intervals=intervals,modeled_frame_ms=d['cycle']/intervals/1e5,
        renderer_entry_to_flip_publication_ms=d['all_cpu_render']/intervals/1e5,
        gpu_busy_ms=d['all_gpu_busy']/intervals/1e5,gpu_fully_idle_ms=(d['all_total']-d['all_gpu_busy'])/intervals/1e5,
        gpu_fully_idle_during_renderer_ms=(d['render_total']-d['render_gpu_busy'])/intervals/1e5,
        command_idle_empty_during_renderer_ms=d['render_idle_empty']/intervals/1e5,
        fragment_state_ms=d['all_fragment']/intervals/1e5,fragment_stall_ms=d['all_fragment_stall']/intervals/1e5,
        fragment_bubble_ms=(d['all_fragment']-d['all_fragment_stall']-d['all_fragment_productive_advance'])/intervals/1e5,
        fragment_valid_advance_ms=d['all_fragment_productive_advance']/intervals/1e5,
        fragment_valid_advance_during_renderer_percent=100*d['render_fragment_productive_advance']/d['all_fragment_productive_advance'],
        final_publication_to_complete_ms=statistics.mean(complete[i]-submit[i] for i in submit)/1e5,
        completion_after_next_render_begin_ms=statistics.mean(render_tail)/1e5,
        minimum_completion_margin_before_next_render_ms=min(game_slack)/1e5,
        publications_per_interval=d['publications']/intervals,command_words_per_interval=d['uploaded_words']/intervals,
        all_eight_frame_batch_sizes=dict(count=len(sizes),mean=statistics.mean(sizes),min=min(sizes),max=max(sizes)),
        rdptr_polls=d['rdptr_polls'],fence_polls=d['fence_polls'],counters=d,
        independent_start_frame_color_depth_exact=True,reference_sha256=sha(reference),frames=images,
        workload={i:{key:frames[i][key] for key in ['buffer','gpu_cmd_words','gpu_pixels','hash_cmd']} for i in range(start,start+count)})
    return result
