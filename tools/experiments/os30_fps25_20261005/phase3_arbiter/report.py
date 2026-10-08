#!/usr/bin/env python3
"""Bind and summarize the bounded, unselected arbiter feasibility trial."""
import hashlib,json
from pathlib import Path
from run import ROOT,BASE,OUT,PRIOR,FRAMES,sha,write

inputs=json.loads((OUT/'inputs.json').read_text())
for rel,h in inputs['sources'].items():assert sha(OUT/rel)==h,rel
assert sha(BASE/'manifest.json')==inputs['baseline_manifest_sha256']
rows={}
evidence={str((OUT/'inputs.json').relative_to(ROOT)):sha(OUT/'inputs.json')}
for threshold in (8,2):
    d=OUT/f'threshold{threshold}';build=json.loads((d/'build.json').read_text())
    assert sha(d/'obj/Vtb_gpu')==build['simulator_sha256']
    assert build['inputs_sha256']==sha(OUT/'inputs.json')
    results=json.loads((d/'results.json').read_text())
    assert [r['frame'] for r in results]==list(FRAMES)
    for r in results:
        assert r['simulator_sha256']==build['simulator_sha256']
        stem=f"frame_{r['frame']:05d}"
        assert sha(d/(stem+'.log'))==r['log_sha256']
        assert sha(d/(stem+'.bin'))==r['output_sha256']==sha(PRIOR/(stem+'.bin'))
        assert sha(PRIOR/(stem+'.vec'))==r['vector_sha256']
        assert sha(PRIOR/(stem+'.log'))==r['original_log_sha256']
    for p in (d/'build.json',d/'results.json',d/'build.log'):
        evidence[str(p.relative_to(ROOT))]=sha(p)
    rows[threshold]=results
comparisons=[]
for a,b in zip(rows[8],rows[2]):
    s=a['stats'];t=b['stats']
    qm=lambda x:x['cpu_queue_sum']/x['cpu_queue_n']
    sm=lambda x:x['cpu_service_sum']/x['cpu_service_n']
    comparisons.append(dict(frame=a['frame'],gpu_cycles_8=s['cycles'],gpu_cycles_2=t['cycles'],
        gpu_time_increase_percent=(t['cycles']/s['cycles']-1)*100,
        gpu_extra_ms_at100MHz=(t['cycles']-s['cycles'])/100000,
        cpu_queue_mean_8=qm(s),cpu_queue_mean_2=qm(t),cpu_queue_mean_saved=qm(s)-qm(t),
        cpu_response_service_mean_8=sm(s),cpu_response_service_mean_2=sm(t),
        cpu_completions_per_100k_cycles_8=s['cpu_completed']/s['cycles']*100000,
        cpu_completions_per_100k_cycles_2=t['cpu_completed']/t['cycles']*100000,
        cpu_completion_rate_change_percent=((t['cpu_completed']/t['cycles'])/(s['cpu_completed']/s['cycles'])-1)*100,
        hypothetical_ms_at7850_reads_fully_exposed=(qm(s)-qm(t))*7850/100000,
        stats_8=s,stats_2=t,exact_color_depth_fence_full_memory_hash=True,
        observer_noninterference_proven_at8=a['baseline_noninterference_checked']))
limits=[
    'Synthetic CPU performs fixed-address16-word reads, no instructions, cache lookup, writebacks or pipeline stall feedback.',
    'Next CPU request is scheduled97cycles after previous ARacceptance; threshold2 changes request density and total completed work.',
    'Queue latency is first presented ARVALID to AR handshake; service latency is AR handshake to RLAST. Grant occupancy includes service overhead.',
    'GPU AR/AW/W wait counts are interface backpressure and may overlap; AW is posted so these are not additive pipeline stalls.',
    'Latency samples whose request arrived before the measurement window are excluded; arrivals/accepts/completions and sample counts remain in raw stats.',
    'Unfinished final requests are included in occupancy/wait but not completed latency samples. No claim of a disjoint cycle breakdown.',
    'Synthetic audio and scanout request completion are checked; there is no PCM mixer execution, listening test or device scanout test.',
    '7850read scaling is a model-derived magnitude illustration assuming every latency saving is exposed; it is not a measured CPU/frame saving.',
    'All fits, production RTL changes and device deployments remain unselected and unperformed by this trial.'
]
report=dict(scope='Bounded real M1 arbitration feasibility, not FPS qualification',passed=True,selected=False,
    recommendation='Do not fit/deploy threshold2 from these results; GPU time worsens and the expected recoverable CPU time is unproven.',
    production_rtl_unchanged=True,quartus_fit_run=False,device_deployed=False,
    full_frame_cpu_executed=False,comparisons=comparisons,limitations=limits,evidence_sha256=evidence,
    independent_source_screen='simple_validation reviewed preposedge sampling, hardwired RREADY, pending/inflight handling and output gates; no change requested. This is a source screen, not independent runtime rerun.',
    prepared_but_not_selected_fit_script='tools/experiments/os30_fps25_20261005/phase3_arbiter_fit.py')
write(OUT/'REPORT.json',report)
lines=['Threshold8 versus2 on frozen A: bounded arbiter feasibility','',
 'Result: not selected. CPU mean/tail queue latency improves, but GPU completion worsens7.9-9.5%.',
 'No Quartus fit, production change or device deployment. This does not establish an FPS gain.','',
 'Frozen replay traffic2 genuinely enters production M1; GPU M0, audio M3 and Pocket SDRAM/scanout are exercised.',
 'Frames360(entrance),600,720 use the exact deployed F5 captures with appended depth dumps.',
 'Threshold8 reproduces original cycles/busy_cycles/batches and all307212 output bytes exactly.',
 'Threshold2 matches color,depth,fence and full-memory hash exactly for all three frames.',
 'CPU/audio reads remain16beats; no SDRAM protocol/timing assertion fires; scanout completes before6361cycle schedule.','',
 'Frame  GPU cycles8->2       GPU +%  CPU queue mean8->2   p95 8->2   max 8->2']
for r in comparisons:
    s=r['stats_8'];t=r['stats_2']
    lines.append(f"{r['frame']:5d}  {s['cycles']:7d}->{t['cycles']:7d}  {r['gpu_time_increase_percent']:6.2f}  {r['cpu_queue_mean_8']:6.2f}->{r['cpu_queue_mean_2']:6.2f}       {s['cpu_queue_p95']:3d}->{t['cpu_queue_p95']:3d}    {s['cpu_queue_max']:3d}->{t['cpu_queue_max']:3d}")
lines += ['', 'Per-frame details (cycles;100000cycles=1ms at100MHz):']
for r in comparisons:
    s=r['stats_8'];t=r['stats_2']
    lines += [f"Frame{r['frame']}: CPU arrivals {s['cpu_arrivals']}->{t['cpu_arrivals']}, accepts {s['cpu_accepts']}->{t['cpu_accepts']}, completions {s['cpu_completed']}->{t['cpu_completed']}.",
      f"  CPU response service mean {r['cpu_response_service_mean_8']:.2f}->{r['cpu_response_service_mean_2']:.2f}; p95 {s['cpu_service_p95']}->{t['cpu_service_p95']}; max {s['cpu_service_max']}->{t['cpu_service_max']}.",
      f"  CPU completion rate +{r['cpu_completion_rate_change_percent']:.2f}% with acceptance-relative synthetic work; GPU takes +{r['gpu_extra_ms_at100MHz']:.3f}ms.",
      f"  GPU grant occupancy {s['gpu_service']}->{t['gpu_service']}; CPU grant occupancy {s['cpu_service']}->{t['cpu_service']}.",
      f"  GPU AR wait {s['gpu_ar_wait']}->{t['gpu_ar_wait']}; AW wait {s['gpu_aw_wait']}->{t['gpu_aw_wait']}; W wait {s['gpu_w_wait']}->{t['gpu_w_wait']}.",
      f"  Audio total latency p95 {s['audio_total_p95']}->{t['audio_total_p95']}, max {s['audio_total_max']}->{t['audio_total_max']}; scanout max {s['scan_latency_max']}->{t['scan_latency_max']}."]
lines += ['', 'Magnitude only: the29-44cycle mean CPU queue saving multiplied by7850 modeled I/D read misses',
 'would be2.3-3.4ms if every request matched and every saved cycle were exposed. This optimistic',
 'arithmetic still does not guarantee the roughly5ms goal and ignores the measured1.9-2.1ms GPU penalty.',
 'The synthetic workload is denser than modeled average CPU traffic and changes intensity across policies.','',
 'Limits:']+[f'  {s}' for s in limits]
lines += ['', 'Independent source screen: simple_validation found no observer attribution/gating blocker.',
 'The root prepared phase3_arbiter_fit.py but did not select or run it.',
 'Reproduction: run.py prepare; build --threshold8/2; run --threshold8/2; report.py.',
 'CLI threshold spelling is --threshold 8 or --threshold 2. Preparation/build/run paths are private and refuse overwrites.',
 'Source manifest, executable/log/output hashes and all raw statistics are bound in REPORT.json.']
(OUT/'REPORT.txt').write_text('\n'.join(lines)+'\n')
print(OUT/'REPORT.txt')
