#!/usr/bin/env python3
"""Validate the selected batching capture and report CPU/GPU work separately."""
import csv,hashlib,json,statistics,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-geometry-batch-20261003'
PRIOR=ROOT/'docs/measurements/sm64-geometry-20261003'
ORIGINAL=ROOT/'docs/measurements/sm64-bob-20261002/capture'
load=lambda p:json.loads(p.read_text())
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
frames=(40,120,200,360,400,500,600,720)
new=load(OUT/'replay-results-final-fallback-final.json')
old=load(PRIOR/'replay-results-gameplay-reference-gameplay-fallback-gameplay.json')
assert len(new)==48 and len(old)==72
by={(r['label'],r['mode'],r['frame']):r for r in new}
previous={(r['label'],r['mode'],r['frame']):r for r in old}
for mode in (0,1,2):
    for frame in frames:
        for label,reference in [('final','gameplay'),('final','reference-gameplay'),('fallback-final','fallback-gameplay')]:
            for field in ('framebuffer_sha256','depth_sha256'):
                assert by[label,mode,frame][field]==previous[reference,mode,frame][field],(label,reference,mode,frame,field)
                assert by[label,mode,frame][field]==by[label,0,frame][field],('traffic dependence',label,frame,field)

def cpu(path):
    rows=list(csv.DictReader((path/'frames.csv').open()))
    assert len(rows)==721
    return [statistics.mean(int(r['est_cycles']) for r in rows if f<=int(r['frame'])<f+60)/100000 for f in range(360,720,60)]
before=cpu(PRIOR/'capture-gameplay');original=cpu(ORIGINAL);final=cpu(OUT/'capture-final')
fallback=cpu(OUT/'capture-fallback-final');old_fallback=cpu(PRIOR/'capture-fallback-gameplay')
for label in ('final','fallback-final'):
    p=OUT/f'capture-{label}'
    assert load(p/'inputs.json')['success']
    assert (p/'scene.csv').read_bytes()==(ORIGINAL/'scene.csv').read_bytes()
    assert load(p/'summary.json')['gpu_sync']['ring_overflows']==0
assert (OUT/'capture-final/geometry.csv').read_bytes()==(PRIOR/'capture-gameplay/geometry.csv').read_bytes()
elf=OUT/'SM64/.obj/sm64/app.elf'
assert digest(elf)==digest(OUT/'capture-final/sm64.elf')==digest(OUT/'capture-fallback-final/sm64.elf')
# Verify the asset cache's immutable range against the actual linked app.
symbols=subprocess.check_output(['readelf','-sW',str(elf)],text=True)
symbols={s[-1]:int(s[1],16) for line in symbols.splitlines() if len(s:=line.split())>=8 and s[0][:-1].isdigit() and s[0].endswith(':')}
assert symbols['_start']<=symbols['bob_seg7_vertex_07002818']<symbols['__EH_FRAME_BEGIN__']
checks=load(OUT/'boundary-checks.json');assert len(checks['tests'])==2
files=['src/sm64/sm64/src/pc/gfx/'+n for n in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h')]
source={r:digest(OUT/'SM64'/r) for r in files}
for rel in files:
    assert source[rel]==checks['source_sha256'][Path(rel).name],('Source changed after boundary check',rel)
previous_hashes=load(OUT/'before.json')
for rel in ('src/sm64/Makefile','runtime/MANIFEST','runtime/pocket/os30.rbf_r','runtime/pocket/ap_core.sof'):
    assert digest(OUT/'SM64'/rel)==previous_hashes[rel]
names=['Entrance','Leaving entrance','Field path','Approaching field','Camera movement','Rotated camera']
windows=[dict(name=names[i],first_frame=360+60*i,frames=60,
    previous_trial_ms=before[i],selected_ms=final[i],original_ms=original[i],
    cpu_time_reduction_percent=100*(1-final[i]/before[i]),
    reduction_vs_original_percent=100*(1-final[i]/original[i]),
    fallback_ms=fallback[i],previous_fallback_ms=old_fallback[i]) for i in range(6)]
report=dict(status='Software batching verified; ready for on-device comparison',
    cpu_windows=windows,paired_framebuffer_depth_comparisons=48,
    additional_matches_to_previous_quantized_reference=24,
    scene_records_matched=721,accelerated_vertex_and_triangle_counts_unchanged=True,
    boundary_checks=checks['tests'],app_sha256=digest(elf),source_sha256=source,
    gpu_ms={frame:dict(previous=previous['gameplay',1,frame]['stats']['cycles']/100000,
        selected=by['final',1,frame]['stats']['cycles']/100000) for frame in frames},
    immutable_vertex_range={k:hex(symbols[k]) for k in ('_start','__EH_FRAME_BEGIN__','bob_seg7_vertex_07002818')},
    fit_unchanged=load(PRIOR/'results.json')['fit'],runtime_unchanged=True,
    full_seed_sweep_run=False,hardware_deployment_run=False,
    limitations=['Approximate CPU model; hardware reduced-accuracy FMA is not modeled.',
      'CPU and GPU work are measured separately, without backpressure or overlap; these are not FPS.',
      'CPU audio is disabled; GPU replay uses scanout/audio/CPU traffic proxies.',
      'Eight sampled images over one 721-frame scripted intro/menu/Bob route; other levels untested.',
      'About 2-3% less CPU work than the original pre-accelerator build, below the 30% target.',
      'The existing bitstream remains configured at 100 MHz with 88.47 MHz estimated Fmax.'])
(OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
