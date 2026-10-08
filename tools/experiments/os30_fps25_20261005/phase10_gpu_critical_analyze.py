#!/usr/bin/env python3
"""Bind identical-work CPU/GPU scheduling and memory-oracle ceilings."""
import csv,json,re,statistics
from pathlib import Path
import phase10_gpu_critical as c
OUT=c.OUT;P=c.P;sha=c.sha;save=c.save
MAIN={0:'idle',1:'ring_read_wait',2:'decode',3:'payload',4:'execute',5:'framebuffer_flush',6:'fragment_pipe',7:'clear_setup',8:'clear_words',9:'span_setup',10:'span_mul_wait',11:'span_capture',12:'span_emit',13:'span_select',14:'edge_fill',15:'triangle_derive',16:'project_transform',17:'vertex_cache_read',18:'payload_wait',19:'vertex_cache_write'}
FRAG={0:'unused',1:'depth_response',2:'depth_accumulator_evaluate',3:'other_pixel_memory_state',4:'depth_write_barrier',5:'depth_fill_already_busy',6:'other_depth_dispatch',7:'framebuffer_write_queue_full',8:'texture_response',9:'colormap',10:'other_blocker',11:'pipeline_advances_valid_pixel',12:'unstalled_source_done',13:'unstalled_perspective_active_without_pixel',14:'unstalled_other_no_pixel'}
def profile(d):
    old={r['function']:int(r['self_cycles']) for r in csv.DictReader((d/'profile-0360.tsv').open(),delimiter='\t')}
    return {r['function']:(int(r['self_cycles'])-old.get(r['function'],0))/800000 for r in csv.DictReader((d/'profile.tsv').open(),delimiter='\t')}
def groups(d):
    out={}
    for kind,i,n in re.findall(r'CRITICAL_(\w+) (\d+) (\d+)',(d/'run.log').read_text()):out.setdefault(kind,{})[int(i)]=int(n)
    return out
def main():
    baseline=OUT/'coupled/run-baseline-8';oracle=OUT/'coupled/run-ideal-memory-8';old=P/'load-alias-coupled-v1/run-baseline-8';cap=OUT/'capture-native'
    paired=[]
    for f in sorted(baseline.glob('frame_*.bin')):
        b=oracle/f.name;o=old/f.name;r=OUT/'replay'/f.name
        raw=r.read_bytes();assert len(raw)==307212
        assert f.read_bytes()==b.read_bytes()==o.read_bytes()==raw[:153600]+raw[153612:]
        paired.append(dict(frame=int(f.stem.split('_')[1]),coupled_sha256=sha(f),oracle_sha256=sha(b),max_supply_sha256=sha(r),color_exact=True,depth_exact=True))
    assert len(paired)==8
    for n in ('scene.csv','geometry.csv','culling.csv'):
        assert (baseline/n).read_bytes()==(oracle/n).read_bytes()==(old/n).read_bytes()==(cap/n).read_bytes()
    for n in ('coupled.json','counters.csv','events.csv','publishes.csv'):assert (baseline/n).read_bytes()==(old/n).read_bytes(),n
    result={}
    for name,d in [('baseline',baseline),('ideal_gpu_memory',oracle)]:
        status=json.loads((d/'coupled.json').read_text());g=groups(d)
        assert sum(g['MAIN'].values())==status['cycles']
        assert sum(g['FRAG'].values())==g['MAIN'][6]
        assert sum(g['DRV'].values())==g['MAIN'][15]
        assert sum(g['PSS'].values())==g['MAIN'][6]
        invoke=json.loads((d/'inputs.json').read_text());assert invoke['success']
        for n,h in invoke['results_sha256'].items():assert sha(d/n)==h,n
        assert 'FATAL' not in (d/'run.log').read_text()
        counter=list(csv.DictReader((d/'counters.csv').open()))[-1]
        result[name]=dict(modeled_frame_ms=status['cpu_cycles']/800000,
            main_state_ms={MAIN.get(i,str(i)):v/800000 for i,v in g['MAIN'].items() if v},
            exclusive_fragment_ms={FRAG.get(i,str(i)):v/800000 for i,v in g['FRAG'].items() if v},
            raw_counter_cycles=g,self_profile_ms=profile(d),gpu_busy_ms=int(counter['all_gpu_busy'])/800000,
            rdptr_polls=int(counter['rdptr_polls']),oracle=json.loads((d/'oracle-memory.json').read_text()))
    replay=json.loads((OUT/'replay/result.json').read_text())
    replay_ms=[r['stats']['cycles']/100000 for r in replay['runs']]
    floor=sum(profile(cap).values());before=result['baseline']['modeled_frame_ms'];after=result['ideal_gpu_memory']['modeled_frame_ms']
    result.update(passed=True,scope='Retained simple F11, eight matched frames360..367, real GPU/cache/memory with modeled CPU timing.',
        cpu_only_same_window_ms=floor,modeled_wait_added_to_cpu_ms=before-floor,
        ideal_gpu_memory_saved_ms=before-after,ideal_gpu_memory_saved_pct=100*(1-after/before),
        max_supply_gpu_ms=dict(mean=statistics.mean(replay_ms),minimum=min(replay_ms),maximum=max(replay_ms),per_frame=replay_ms),
        actual_render_comparisons=paired,passive_control_cycle_identical=True,
        limits=['CPU35.746666ms floor is the SAME eight frames, unlike30.36ms averaged over361 gameplayframes. Do not mix these denominators.',
            'Maximal supply replay starts each frame with cold GPU caches, captures nativeFMA commands, preserves ringcapacity and required texture/MMIObarriers, but has no CPU instruction delay. It is a GPU work bound, not appFPS.',
            'Ideal GPU-M0 memory bypasses physical SDRAM and its arbitration only for GPU traffic: one ordered burst at a time, one32-bit word/cycle, real byte-strobe writes and identical backing RAM. Actual render cache/CRAM1/GPU stay present. This is an oracle, not deployable hardware.',
            'Main-state bins and fragment bins are exclusive within their domains; PSS, drain and cache counters overlap and must not be added as independent savings.',
            'GPU counters include final completion/presentation tail; idle mainstate includes that tail and game/CPU prep. CPU frame denominator excludes final presentation wait.',
            'Existing timeline.csv files retain10,000-cycle CPUrender/GPUbusy/fragment-stall bins. Operand-level reciprocal traces are separately bound.',
            'No fullCPU RTL timing, broadlevel claim, production edit, fit or Pocket deployment.'])
    save(OUT/'result.json',result)
    text=f'''Retained F11 critical-path audit, matched frames360..367

CPU-only instruction/cache model: {floor:.8f} ms/frame.
Coupled actual GPU/SDRAM/CRAM1: {before:.8f} ms/frame.
Additional modeled CPU waiting: {before-floor:.8f} ms/frame.
Maximal-rate command replay through actual GPU/memory: {statistics.mean(replay_ms):.8f} ms/frame.
Ideal GPU SDRAM endpoint: {after:.8f} ms/frame ({100*(1-after/before):.6f}% time reduction).
All eight actual color/depth outputs match across all schedules and the retained baseline.

Exclusive GPU work:
  Fragment pipeline: 16.381110 ms/frame.
  Triangle attribute derivation: 2.052381 ms; serial determinant divide is0.439390 ms within it.
  Clear writes: 1.825568 ms.
  Span select/setup/multiply/capture/emit: 1.312909 ms.
  Vertex projection: 0.723791 ms.
  Edge fill: 0.361869 ms.

Within fragment processing, depth-response wait4.930710ms and pre-read global
write-barrier hold3.120973ms dominate blocked cycles. Perspective source issue
stall WITHOUT simultaneous pixel-pipe blocking is3.189221ms. Its all-zero UV
subset is0.496864ms. Source-finished span drain occupies5.648441ms, overlapping
depth-response2.423639ms and global-write-barrier1.158161ms.

These are dependency ceilings, not additive speedup promises. Removing memory
latency cuts GPU busy23.672524->17.889318ms, but wholeframe saves only2.011154ms
because CPU work remains35.746666ms. Span/PSS scheduling may reduce GPU queue
pressure while a complementary CPU geometry change removes the CPU floor.
Simply making source DMA faster cannot exploit the full GPU-only throughput.

The earlier30.36ms CPU figure averages361 gameplayframes and must not be used
to infer7ms of waiting in this harder eight-frame window: actual extra wait is2.08ms.
No production hardware or deployment changes; ideal-memory endpoint is only an oracle.
'''
    (OUT/'REPORT.txt').write_text(text)
    save(OUT/'final-manifest.json',dict(files={str(p.relative_to(OUT)):sha(p) for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts and 'build' not in p.parts},helpers={p.name:sha(p) for p in [Path(__file__),Path(c.__file__),c.HERE/'phase10_gpu_critical_replay.py']}))
    print(json.dumps({k:result[k] for k in ('cpu_only_same_window_ms','modeled_wait_added_to_cpu_ms','ideal_gpu_memory_saved_ms','max_supply_gpu_ms')},indent=2))
if __name__=='__main__':main()
