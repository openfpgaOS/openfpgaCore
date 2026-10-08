#!/usr/bin/env python3
"""Gate the SM64 trial on functional replay, then report measured limitations."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-geometry-20261003'
load=lambda p:json.loads(p.read_text())
rows=load(OUT/'replay-results-gameplay-reference-gameplay-fallback-gameplay.json')
assert len(rows)==72
by={(r['label'],r['mode'],r['frame']):r for r in rows}
frames=(40,120,200,360,400,500,600,720)
for mode in (0,1,2):
    for frame in frames:
        a,b=(by[label,mode,frame] for label in ('gameplay','reference-gameplay'))
        for field in ('framebuffer_sha256','depth_sha256'):
            assert a[field]==b[field],(field,mode,frame)
        for label in ('gameplay','reference-gameplay','fallback-gameplay'):
            for field in ('framebuffer_sha256','depth_sha256'):
                assert by[label,mode,frame][field]==by[label,0,frame][field]
old=load(OUT/'replay-results-v2-reference-v2-fallback-v2-original.json')
old={r['frame']:r for r in old if r['label']=='fallback-v2'}
for frame,b in old.items():
    for field in ('framebuffer_sha256','depth_sha256'):
        assert by['fallback-gameplay',1,frame][field]==b[field],('fallback',frame,field)

def cpu(path):
    rows=list(csv.DictReader((path/'frames.csv').open()))
    assert len(rows)==721
    return [statistics.mean(int(r['est_cycles']) for r in rows
        if f<=int(r['frame'])<f+60)/100000 for f in range(360,720,60)]
before=cpu(ROOT/'docs/measurements/sm64-bob-20261002/capture')
after=cpu(OUT/'capture-gameplay');fallback=cpu(OUT/'capture-fallback-gameplay')
scene=(ROOT/'docs/measurements/sm64-bob-20261002/capture/scene.csv').read_bytes()
for label in ('gameplay','reference-gameplay','fallback-gameplay'):
    assert (OUT/f'capture-{label}/scene.csv').read_bytes()==scene
names=['Entrance','Leaving entrance','Field path','Approaching field','Camera movement','Rotated camera']
windows=[dict(name=name,first_frame=360+60*i,frames=60,before_ms=before[i],
    accelerator_ms=after[i],fallback_ms=fallback[i],
    cpu_time_change_percent=100*(after[i]/before[i]-1)) for i,name in enumerate(names)]

def bytes_at(label,frame,depth=False):
    b=(OUT/f'replays/{label}/1/frame_{frame:05}.bin').read_bytes()
    return b[153612:] if depth else b[:153600]
pixel_differences={}
for frame in frames:
    a,b=bytes_at('gameplay',frame),bytes_at('fallback-gameplay',frame)
    pixel_differences[frame]=sum(a[i:i+2]!=b[i:i+2] for i in range(0,len(a),2))
fit=load(OUT/'fit-v1/results/pocket-os30-s2/result.json')
assert fit['valid'] and fit['seed']==2
report=dict(status='functional experimental trial; no measured speedup',
    cpu_windows=windows,framebuffer_differences_vs_original=pixel_differences,
    paired_framebuffer_depth_comparisons=24,normal_gpu_checks=279,geometry_gpu_checks=378,
    gpu_ms={frame:{label:by[label,1,frame]['stats']['cycles']/100000
        for label in ('gameplay','reference-gameplay','fallback-gameplay')} for frame in frames},
    fit=dict(alms=fit['alms'],m10ks=fit['m10ks'],dsps=41,labs=1843,
        seed=2,configured_mhz=100,fmax_mhz=88.47,setup_ns=fit['worst']['setup'],
        hold_ns=fit['worst']['hold'],timing_closed=fit['all_checks_pass']),
    app_sha256=hashlib.sha256((OUT/'capture-gameplay/sm64.elf').read_bytes()).hexdigest(),
    full_seed_sweep_run=False,hardware_deployment_run=False,
    limitations=['Approximate CPU model; reduced-accuracy hardware FMA is not modeled.',
        'CPU and GPU measured separately, without ring backpressure or submission overlap.',
        'Audio disabled in CPU capture; GPU traffic uses scanout/audio/CPU proxies.',
        'GPU uses production RTL, cache and Pocket SDRAM controller with timing checks.',
        'Eight sampled frames on a 721-frame scripted intro/menu/Bob route; other levels untested.',
        'Fixed-point transforms can change a few pixels versus the original floating-point path.',
        'Configured 100 MHz does not close timing; on-device stability is unverified.'])
(OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
