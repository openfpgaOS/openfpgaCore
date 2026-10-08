#!/usr/bin/env python3
"""Validate final captures and report CPU/GPU work, without claiming Pocket FPS."""
import csv
import hashlib
import json
from pathlib import Path
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
BATCH = ROOT / 'docs/measurements/sm64-geometry-batch-20261003'
ORIGINAL = ROOT / 'docs/measurements/sm64-bob-20261002/capture'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
frames = (40,120,200,360,400,500,600,720)
new = load(OUT / 'replay-results-final-candidate-fallback-candidate.json')
ref = load(OUT / 'replay-results-reference-final-candidate.json')
prefetch = load(OUT / 'replay-results-final-candidate-prefetch.json')
previous = load(BATCH / 'replay-results-final-fallback-final.json')
assert (len(new),len(ref),len(prefetch),len(previous)) == (48,24,24,48)
index = lambda rows: {(r['label'],r['mode'],r['frame']):r for r in rows}
n,r,p,b = map(index,(new,ref,prefetch,previous))
for mode in (0,1,2):
    for frame in frames:
        pairs = [(n['final-candidate',mode,frame],r['reference-final-candidate',mode,frame]),
                 (n['fallback-candidate',mode,frame],b['fallback-final',mode,frame]),
                 (n['final-candidate',mode,frame],p['final-candidate',mode,frame])]
        for a,z in pairs:
            for field in ('framebuffer_sha256','depth_sha256'):
                assert a[field] == z[field],(mode,frame,field,a['label'],z['label'])
        for table,label in ((n,'final-candidate'),(n,'fallback-candidate'),(p,'final-candidate')):
            for field in ('framebuffer_sha256','depth_sha256'):
                assert table[label,mode,frame][field] == table[label,0,frame][field]

def cpu(path):
    rows = list(csv.DictReader((path/'frames.csv').open()))
    assert len(rows) == 721
    return [statistics.mean(int(r['est_cycles']) for r in rows[f:f+60])/100000
            for f in range(360,720,60)]

original,old,final,fallback = map(cpu,(ORIGINAL,BATCH/'capture-final',
    OUT/'capture-final-candidate',OUT/'capture-fallback-candidate'))
old_fallback = cpu(BATCH/'capture-fallback-final')
for label in ('final-candidate','fallback-candidate','reference-final-candidate'):
    path = OUT / ('capture-'+label)
    assert load(path/'inputs.json')['success']
    assert (path/'scene.csv').read_bytes() == (ORIGINAL/'scene.csv').read_bytes()
    assert load(path/'summary.json')['gpu_sync']['ring_overflows'] == 0
elf = OUT/'SM64/.obj/sm64/app.elf'
assert sha(elf) == sha(OUT/'capture-final-candidate/sm64.elf') == sha(OUT/'capture-fallback-candidate/sm64.elf')
symbols = subprocess.check_output(['readelf','-sW',str(elf)],text=True)
symbols = {s[-1]:int(s[1],16) for line in symbols.splitlines()
           if len(s:=line.split())>=8 and s[0][:-1].isdigit() and s[0].endswith(':')}
assert symbols['_start'] <= symbols['bob_seg7_vertex_07002818'] < symbols['__EH_FRAME_BEGIN__']
checks = load(OUT/'boundary-checks.json')
visibility = load(OUT/'visibility-checks.json')
names = ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h','gfx_vertex_cull.h')
files = ['src/sm64/sm64/src/pc/gfx/'+name for name in names]
source = {rel:sha(OUT/'SM64'/rel) for rel in files}
for rel,digest in source.items():
    assert digest == checks['source_sha256'][Path(rel).name]
for name,digest in visibility['source_sha256'].items():
    assert source['src/sm64/sm64/src/pc/gfx/'+name] == digest
runtime = load(OUT/'runtime-staged.json')
for rel,digest in runtime['files_sha256'].items():
    assert sha(OUT/'SM64'/rel) == digest
accept = load(OUT/'accept-prefetch-v1/results.json')['os30']
assert all(v['failed']==0 and v['passed']==126 and v['exit_code']==0 for v in accept.values())
names = ('Entrance','Leaving entrance','Field path','Approaching field','Camera movement','Rotated camera')
windows = [dict(name=names[i],first_frame=360+60*i,frames=60,
    original_ms=original[i],published_batch_ms=old[i],selected_ms=final[i],
    reduction_vs_original_percent=100*(1-final[i]/original[i]),
    throughput_vs_original_percent=100*(original[i]/final[i]-1),
    reduction_vs_published_batch_percent=100*(1-final[i]/old[i]),
    fallback_ms=fallback[i],published_fallback_ms=old_fallback[i]) for i in range(6)]
gpu = []
for mode in (0,1,2):
    for frame in frames:
        old_cycles = b['final',mode,frame]['stats']['cycles']
        software_cycles = n['final-candidate',mode,frame]['stats']['cycles']
        cycles = p['final-candidate',mode,frame]['stats']['cycles']
        gpu.append(dict(mode=mode,frame=frame,published_batch_ms=old_cycles/100000,
            software_only_ms=software_cycles/100000,selected_ms=cycles/100000,
            throughput_vs_published_batch_percent=100*(old_cycles/cycles-1),
            throughput_from_prefetch_percent=100*(software_cycles/cycles-1)))
fit = dict(seed=2,configured_mhz=100,fmax_mhz=91.04,alms=17918,m10ks=307,dsps=41,labs=1847,
           timing_closed=False,**runtime['fit']['worst'])
report = dict(status='Verified local candidate; Pocket FPS unmeasured',cpu_windows=windows,
    cpu_aggregate_throughput_vs_original_percent=100*(sum(original)/sum(final)-1),
    cpu_aggregate_throughput_vs_published_batch_percent=100*(sum(old)/sum(final)-1),
    paired_framebuffer_depth_comparisons=72,scene_records_matched=721,
    boundary_checks=checks['tests'],visibility_checks=visibility['result'],gpu_acceptance=accept,
    app_sha256=sha(elf),source_sha256=source,runtime_sha256=runtime['files_sha256'],
    os_bin_unchanged_sha256=runtime['os_bin_unchanged_sha256'],fit=fit,gpu_work=gpu,
    immutable_vertex_range={k:hex(symbols[k]) for k in ('_start','__EH_FRAME_BEGIN__','bob_seg7_vertex_07002818')},
    original_renderer_bit_identical=False,full_seed_sweep_run=False,hardware_deployment_run=False,
    limitations=[
        'Approximate CPU model; reduced-accuracy hardware FMA is not modeled.',
        'CPU/GPU work is measured separately without GPU backpressure or execution overlap; not whole-game FPS.',
        'CPU audio is disabled; RTL traffic modes approximate scanout/audio/CPU contention.',
        'Eight sampled renderings along one 721-frame intro/menu/Bob route; other levels untested.',
        'Guard-band clipping changes some edge/texel interpolation versus the original renderer; it matches the same-path quantized CPU reference.',
        'Fallback CPU estimate regresses by up to 0.6% versus the previous batch; fallback images match exactly.',
        'Single saved-seed os30 fit improves timing, but does not close 100 MHz.',
        'No measured 30% whole-game speedup; on-device validation remains necessary.'])
(OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(cpu_windows=windows,fit=fit,comparisons=72),indent=2))
