#!/usr/bin/env python3
"""Validate first-level captures; summarize CPU estimates and separate GPU work."""
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-bob-20261002'
CAP=OUT/'capture'
load=lambda p:json.loads(p.read_text())
csvrows=lambda p,**kwargs:list(csv.DictReader(p.open(),**kwargs))
frames=csvrows(CAP/'frames.csv')
scenes=csvrows(CAP/'scene.csv')
assert len(frames)==len(scenes)==721
assert all(int(r['level'])==9 and int(r['area'])==1 and int(r['act'])==1
           and int(r['action'],16)!=0 for r in scenes[360:])
for name in ('frames.csv','scene.csv'):
    assert (CAP/name).read_bytes()==(OUT/'verified'/name).read_bytes(),name
for frame in (360,720):
    name=f'gpuvec/frame_{frame:05}.vec'
    assert hashlib.sha256((CAP/name).read_bytes()).digest()==hashlib.sha256((OUT/'verified'/name).read_bytes()).digest()

replays=load(OUT/'replay-results.json')
assert len(replays)==102
base={(r['mode'],r['frame']):r for r in replays if r['label']=='baseline'}
after={(r['mode'],r['frame']):r for r in replays if r['label']=='prefetch'}
assert len(base)==len(after)==51 and base.keys()==after.keys()
for key,before in base.items():
    later=after[key]
    assert before['vector_sha256']==later['vector_sha256']
    assert before['output_sha256']==later['output_sha256'],key

traffic=[]
for mode,name in enumerate(('quiet','scanout_audio_proxy','cpu_scanout_audio_proxy')):
    before=sum(r['stats']['cycles'] for (m,f),r in base.items() if m==mode)
    later=sum(r['stats']['cycles'] for (m,f),r in after.items() if m==mode)
    gains=[100*(r['stats']['cycles']/after[m,f]['stats']['cycles']-1)
           for (m,f),r in base.items() if m==mode]
    traffic.append(dict(mode=mode,name=name,frames=17,before_cycles=before,after_cycles=later,
        throughput_gain_percent=100*(before/later-1),cycle_reduction_percent=100*(1-later/before),
        min_gain_percent=min(gains),max_gain_percent=max(gains)))

def profile(frame):
    return {(r['function'],r['addr']):r for r in csvrows(CAP/f'profile-{frame:04}.tsv',delimiter='\t')}

def penalties(frame):
    text=(CAP/f'profile-{frame:04}.txt').read_text().split('== functions')[0]
    return {k:int(v) for k,v in re.findall(r'^\s+([\w:/*]+)\s+(\d+)',text,re.M)}

windows=[]
names=['Entrance, facing the field','Leaving the entrance','Along the field path',
       'Approaching the field','Field, camera movement','Field, rotated camera']
for lo,name in zip(range(360,720,60),names):
    hi=lo+60
    rows=[r for r in frames if lo<=int(r['frame'])<hi]
    cpu=statistics.mean(int(r['est_cycles']) for r in rows)/100000
    p0,p1=profile(lo),profile(hi)
    delta=[]
    for key,row in p1.items():
        d={field:int(row[field])-int(p0.get(key,{}).get(field,0))
           for field in ('self_cycles','self_insns','calls','tailcalls')}
        if d['self_cycles']:
            delta.append(dict(function=key[0],addr=key[1],**d,ms_per_frame=d['self_cycles']/60/100000))
    delta.sort(key=lambda r:r['self_cycles'],reverse=True)
    pe0,pe1=penalties(lo),penalties(hi)
    pd={k:(v-pe0.get(k,0))/60/100000 for k,v in pe1.items()}
    samples=sorted(f for m,f in base if m==1 and lo<=f<hi)
    gpu=lambda source,mode:statistics.mean(source[mode,f]['stats']['cycles'] for f in samples)/100000
    windows.append(dict(name=name,first_frame=lo,last_frame_exclusive=hi,cpu_frames=len(rows),
        cpu_estimated_ms=cpu,instructions_per_frame=statistics.mean(int(r['insns']) for r in rows),
        command_words_per_frame=statistics.mean(int(r['gpu_cmd_words']) for r in rows),
        gpu_samples=samples,gpu_baseline_ms=gpu(base,1),gpu_prefetch_ms=gpu(after,1),
        gpu_heavy_baseline_ms=gpu(base,2),top_functions=delta[:30],cpu_model_components_ms=pd))

report=dict(scope='Bob-omb Battlefield act 1, entrance and walking into the field, at configured 100/100/100 MHz.',
    frames_captured=721,gameplay_cpu_window=[360,720],gpu_frames=17,paired_gpu_comparisons=51,
    comparisons_byte_exact=True,capture_reproduced=True,traffic=traffic,windows=windows,
    limitations=['CPU is an approximate instruction/cache/pipeline model, not CPU RTL.',
        'CPU model uses os30 cache/predictor sizes and radix-4 latencies; other latency estimates derive from SDK qsim.',
        'CPU arithmetic uses qsim; hardware reduced-accuracy FMA is not reproduced.',
        'Audio is disabled in game execution. GPU audio/CPU/scanout readers are traffic proxies.',
        'CPU and GPU are measured separately, without submission overlap, ring backpressure, firmware interrupts or display pacing.',
        'GPU replay flushes the render cache at each idle command-batch boundary.',
        'Functional SDK rasterizer omits os30 triangles; all displayed captures and rendering comparisons use actual GPU RTL.',
        'The harness selects the normal level entrance with a one-shot debugger hook; no ELF, game or production RTL change.',
        'This is a route sample, not coverage of the entire level or a hardware FPS measurement.'],
    production_modified=False,full_seed_sweep_run=False)
(OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')

import numpy as np
from PIL import Image,ImageDraw,ImageFont
font=ImageFont.load_default(size=14)
sheet=Image.new('RGB',(960,536),'#151c29')
draw=ImageDraw.Draw(sheet)
for idx,frame in enumerate((360,440,480,540,600,700)):
    p=OUT/f'replays/baseline/1/frame_{frame:05}.bin'
    data=p.read_bytes();assert len(data)==153612
    a=np.frombuffer(data[:153600],dtype='<u2').reshape(240,320)
    rgb=np.stack(((a>>11)*255//31,((a>>5)&63)*255//63,(a&31)*255//31),-1).astype('uint8')
    im=Image.fromarray(rgb);im.save(p.with_suffix('.png'))
    x,y=(idx%3)*320,(idx//3)*268
    sheet.paste(im,(x,y))
    draw.text((x+9,y+246),f'Frame {frame} | GPU {base[1,frame]["stats"]["cycles"]/100000:.1f} ms',font=font,fill='white')
sheet.save(OUT/'bob-captures.png')

lines=['# Bob-omb Battlefield simulation — 2026-10-02','',
    '**The model points to CPU rendering work as the first priority for larger speedups.**',
    'At the entrance, the CPU estimate is 44.0 ms/frame while the GPU replay takes',
    '24.6 ms with scanout/audio traffic proxies. These components overlap in the',
    'real system, so they must not be added or converted into claimed hardware FPS.','',
    'The unmodified current SM64 ELF was executed for 721 presented frames. A',
    'one-shot debugger hook selects level 9, area 1, act 1 and its normal entrance',
    'warp. Input dismisses the entrance text, idles, walks into the field, and',
    'rotates the camera. All 361 checked gameplay frames remain in Bob-omb',
    'Battlefield with a valid Mario action. A fresh capture reproduces the complete',
    'frame/scene traces and the entrance/final GPU vector hashes.','',
    '## CPU and GPU workload','',
    'CPU values average 60 consecutive frames per row. GPU values average the',
    'sampled frames listed in `results.json`; the baseline uses normal os30 logic.',
    'GPU replay includes the production render cache and actual Pocket SDRAM',
    'arbiter/controller, refresh and command-timing checks. Clocks are modeled at',
    '100 MHz. Sound is disabled during CPU execution.','',
    '| View | Approximate CPU work | Baseline GPU work | Prefetch GPU work |',
    '|---|---:|---:|---:|']
for w in windows:
    lines.append(f'| {w["name"]} | {w["cpu_estimated_ms"]:.1f} ms | {w["gpu_baseline_ms"]:.1f} ms | {w["gpu_prefetch_ms"]:.1f} ms |')
lines+=['','## Entrance CPU hotspots','',
    'These are **self** costs, avoiding nested-function double counting.',
    '`gfx_run_dl` includes inlined vertex transformation, lighting and matrix work;',
    'its full cost must not be attributed to opcode dispatch alone.','',
    '| Function | Estimated ms/frame |','|---|---:|']
for r in windows[0]['top_functions'][:10]:
    lines.append(f'| `{r["function"]}` | {r["ms_per_frame"]:.2f} |')
lines+=['','At the entrance the game executes about **2.41 million instructions/frame**.',
    'The model assigns approximately 24.1 ms to instruction issue, 6.6 ms to',
    'dependency stalls, 6.4 ms to control-flow penalties and 3.9 ms to cache',
    'penalties. The rest covers I/O, division, conversion and other stalls.',
    'Removing SDRAM cache misses alone is therefore insufficient for a 30% gain.',
    'Even removing the model\'s entire predicted-taken branch penalty leaves',
    'about 40.1 ms of CPU work at this view.','',
    '## Depth-prefetch comparison','',
    '**51 paired comparisons** (17 frames × three traffic schedules) match in',
    'framebuffer bytes, fence token and full 64 MiB SDRAM hash.','',
    '| Traffic | Aggregate GPU throughput gain |','|---|---:|']
for r in traffic:
    lines.append(f'| {r["name"].replace("_"," ")} | {r["throughput_gain_percent"]:.2f}% |')
lines+=['','This does not establish an equivalent whole-game FPS gain. The earlier',
    'prefetch remains opt-in because its saved-seed fit regressed; no new bitstream',
    'or fitter sweep was produced for this simulation.','',
    '## Next optimization targets','',
    '1. Reduce repeated triangle/material preparation and vertex-cache upload',
    '   work in the SM64 renderer. Together `gfx_push_triangle` and',
    '   `gfx_gpu_vtx_cache_tri` account for about 10.7 ms at the entrance.',
    '2. Improve the CPU vertex-transform/lighting loop inside `gfx_run_dl`, and',
    '   avoid transforming geometry that can safely be rejected earlier.',
    '3. Reduce projection/clipping fallback work while preserving exact clipping,',
    '   alpha and depth behavior. `gpu_draw_triangles` plus clipping accounts for',
    '   another 6.5 ms at the entrance.',
    '',
    'A 30% speed increase requires about a **23.1% reduction in limiting frame',
    'time**: 44.0 ms would need to become roughly 33.9 ms. This is a target, not',
    'a promised result. The application renderer is the strongest measured',
    'candidate for that reduction; GPU-only changes address a smaller part of',
    'this sampled first-level bottleneck.','',
    '## Limits','']
lines+=['- '+x for x in report['limitations']]
lines+=['','![Bob-omb Battlefield RTL captures](bob-captures.png)','',
    '[Machine-readable results](results.json) · [CPU trace](capture/frames.csv) ·',
    '[Scene trace](capture/scene.csv) · [GPU replay results](replay-results.json) ·',
    '[Reproduction helpers](../../../tools/experiments/sm64_bob_20261002/README.md)','']
(OUT/'README.md').write_text('\n'.join(lines))
for w in windows:
    print(w['name'],round(w['cpu_estimated_ms'],2),round(w['gpu_baseline_ms'],2))
print('PASS: capture reproduced; 51 paired RTL rendering comparisons match.')
