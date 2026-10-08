#!/usr/bin/env python3
"""Read-only accounting of retained F11 profiling; does not build an app."""
import csv, hashlib, json, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005'
F=P/'phase9/checks-state-f11'
OUT=P/'phase10/profiling-overhead-audit-v1'
OUT.mkdir(exist_ok=False)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
evidence={}
def bind(p):
    evidence[str(p.relative_to(ROOT))]=sha(p)
    return p
def read(p):
    return json.loads(bind(p).read_text())
def rows(p,delimiter=','):
    return list(csv.DictReader(bind(p).open(),delimiter=delimiter))
def save(p,v):
    p.write_text(json.dumps(v,indent=2)+'\n')

builds={}
for mode in ['normal','diagnostic']:
    b=read(F/f'build-{mode}.json')
    elf=F/('app.elf' if mode=='normal' else 'diagnostic.elf')
    assert sha(bind(elf))==b['elf_sha256']
    log=bind(F/f'build-{mode}.log').read_text()
    flags=f'-DSM64_PROFILE=0 -DSM64_PERF_DIAG={int(mode=="diagnostic")}'
    assert flags in log and '-DSM64_PERF_DETAILS=1' not in log
    builds[mode]=dict(elf_sha256=sha(elf),flags=flags,details_default=0)
    nm=subprocess.check_output(['llvm-nm',str(elf)],text=True)
    (OUT/f'{mode}-symbols.txt').write_text(nm)
    builds[mode]['sm64_perf_symbol_count']=sum(' sm64_perf_' in s for s in nm.splitlines())
assert builds['normal']['sm64_perf_symbol_count']==0
normal=rows(F/'capture-normal/frames.csv');diag=rows(F/'capture-diagnostic/frames.csv')
assert len(normal)==len(diag)==721
for a,b in zip(normal,diag):
    for key in ['frame','gpu_cmd_words','hash_cmd','hash_px','hash_z','hash_pal']:
        assert a[key]==b[key],(key,a['frame'])
for name in ['scene.csv','geometry.csv','culling.csv']:
    assert bind(F/'capture-normal'/name).read_bytes()==bind(F/'capture-diagnostic'/name).read_bytes()
eager={}
for mode,rs in [('normal',normal),('diagnostic',diag)]:
    sel=[r for r in rs if 360<=int(r['frame'])<=720]
    eager[mode]={k:sum(int(r[k]) for r in sel)/len(sel) for k in ['insns','est_cycles','gpu_cmd_words']}
eager['diagnostic_extra_ms']=(eager['diagnostic']['est_cycles']-eager['normal']['est_cycles'])/100000
eager['diagnostic_extra_cycles_percent']=100*(eager['diagnostic']['est_cycles']/eager['normal']['est_cycles']-1)
eager['scope']='361 frames 360..720, eager modeled CPU/cache timing, original older IEEE-FMA harness; no real GPU wait and not hardware FPS'
coupled=[]
for start in [360,600]:
    nr=read(F/f'coupled-normal-{start}.json')['runs']
    dr=read(F/f'coupled-diagnostic-{start}.json')['runs']
    for traffic in [1,2]:
        n=next(r for r in nr if r['traffic']==traffic and r['name'].endswith('-normal'))
        d=next(r for r in dr if r['traffic']==traffic and r['name'].endswith('-diagnostic'))
        assert n['frames']==d['frames']
        coupled.append(dict(start=start,traffic=traffic,exact_color_depth_frames=len(n['frames']),
            normal_ms=n['modeled_frame_ms'],diagnostic_ms=d['modeled_frame_ms'],
            diagnostic_extra_ms=d['modeled_frame_ms']-n['modeled_frame_ms'],
            diagnostic_extra_percent=100*(d['modeled_frame_ms']/n['modeled_frame_ms']-1),
            diagnostic_publications_per_interval=d['publications_per_interval']))
profiles={}
for mode in ['normal','diagnostic']:
    first={r['function']:r for r in rows(F/f'capture-{mode}/profile-0360.tsv','\t')}
    last={r['function']:r for r in rows(F/f'capture-{mode}/profile-0720.tsv','\t')}
    profiles[mode]=[]
    for name,r in last.items():
        if 'perf_' in name or 'prof_' in name or name=='top_transition':
            d={k:int(r[k])-int(first.get(name,{}).get(k,0)) for k in ['calls','tailcalls','self_cycles','self_insns']}
            profiles[mode].append(dict(function=name,**d))
assert profiles['normal']==[]
transition=next(r for r in profiles['diagnostic'] if r['function']=='top_transition')
sources=['Makefile.sm64','gfx_pc.c','gfx_gpu.c','perf_pocket.c','perf_pocket.h','of_gpu.h']
for name in sources:bind(F/name)
bind(F/'SM64/src/sm64/sm64/src/pc/pc_main.c')
lod=P/'phase10/load-cram-driver-v1'
lb=read(lod/'build-normal.json');bind(lod/'app.elf')
assert sha(lod/'app.elf')==lb['elf_sha256']
assert '-DSM64_PROFILE=0 -DSM64_PERF_DIAG=0' in bind(lod/'build-normal.log').read_text()
bind(P/'phase10/lod5-real-coupled-v2/hot-profile.json')
bind(P/'phase9/published.json')
state={}
for p in [Path('/home/alberto/Repos/SM64/.obj/sm64/app.elf'),
          Path('/home/alberto/Repos/SM64/build/pocket/sm64/Assets/sm64/common/sm64.elf'),
          Path('/run/media/alberto/POCKETDEV/Assets/sm64/common/sm64.elf')]:
    state[str(p)]=dict(present=p.exists(),sha256=sha(p) if p.exists() else None)
save(OUT/'result.json',dict(passed=True,read_only=True,new_builds=0,builds=builds,
    lod5_normal_elf_sha256=lb['elf_sha256'],current_local_state=state,
    eager=eager,coupled=coupled,profile_snapshot_interval='frame 360 snapshot to frame 720 snapshot, 360 frames',
    diagnostic_functions=profiles['diagnostic'],normal_profiling_functions_executed=0,
    sampled_top_timer_reads_per_frame=(transition['calls']+transition['tailcalls'])/360,
    limitations=['No captured per-address timer-read counter exists in these artifacts. Top reads are inferred exactly from executed top_transition calls and source; SDK submit counts imply two timer reads per nonempty publication, while wait-episode counts are not recorded separately.',
        'Older paired comparison uses modeled CPU/cache with IEEE-FMA, synthetic traffic and actual retained GPU/memory RTL. It does not qualify present LOD5 hardware/CPU timing or predict Pocket FPS.',
        'No instrumentation-stripped variant was made. Release normal already disables detailed profiling. Always-active wire/debug counters and four flip/acquire timer reads remain; their removal has no measured gain here.'],
    evidence_sha256=evidence))
report='''The normal F11 and measured LOD5 app already disable PROFILE and PERF_DIAG.
The diagnostic package enables PERF_DIAG but leaves PROFILE and PERF_DETAILS off.
All detailed bucket scopes/counts and the old per-vertex/triangle/UART timers are
therefore absent in normal builds; profile captures contain zero sm64_perf calls.

Diagnostic overhead is bounded by existing matched outputs: 721-frame eager
scene/geometry/culling/command hashes are identical. The diagnostic averages
0.499519 modeled CPU ms extra (1.6453%) over frames360..720. Four eight-frame
coupled comparisons (360 and600, two traffic schedules, seven post-entry
intervals each) have all32 paired color/depth buffers equal and add0.274299 to
0.325120ms/frame, approximately0.82% to0.99%. These are older modeled timing
comparisons, not Pocket measurements or new native-FMA LOD5 qualifications.

Frequency: diagnostic top LOAD/TRI scopes sample one in16 rendered frames after
warmup. Every entry/exit still tests an active flag on unsampled frames. Captured
top_transition calls imply87.4722 timer reads/frame on average in the360-frame
profile interval; each transition updates exclusive time and reads wait counters.
SDK publication adds two timer reads per nonempty batch plus five counter/state
updates. Sample coupled publications average35.86..73.29/frame. SDK waits add two
reads per blocking episode, never per spin. Per-frame frame/game/audio/render
brackets and two-second snapshot reduction remain; panel drawing is frozen while
visible and was not invoked in these normal gameplay comparisons.

Normal residuals: flip and buffer-acquire each retain a timer pair (four reads
per rendered frame). Wire word/triangle/upload counters still exist and increment
in the emitters; the ring minimum-free metric still compares on reservations and
polls. The ring spin count also enforces a50-million-spin trap; deleting that
loop counter would remove a liveness guard, not just profiling. No measured
millisecond gain can be assigned to removing these counters from source alone.

The current local package matches F11 diagnostic648b7c4e; local normal remains
fb4ebb1c. The SD application path was absent when read, so its current bytes are
not claimed. An FPS-only diagnostic is feasible but its maximum observed saving
versus the existing diagnostic is about1%, and it cannot recover a substantial
gain in the normal LOD5 experiments because their detailed profiler is already
off. No code, app, FPGA, or SD was changed by this audit.
'''
(OUT/'REPORT.txt').write_text(report)
bind(Path(__file__))
save(OUT/'manifest.json',dict(passed=True,evidence_sha256={**evidence,**{str(p.relative_to(ROOT)):sha(p) for p in OUT.rglob('*') if p.is_file()}}))
print(sha(OUT/'manifest.json'))
