#!/usr/bin/env python3
"""Assemble feature coverage, fitted resources, and measured sensitivity."""
import collections
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/os30-feature-audit-20261003'
PRIOR = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
FIT = PRIOR / 'fit-prefetch-v1/results/pocket-os30-s2/ap_core.fit.rpt'
load = lambda p: json.loads(p.read_text())
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def resources():
    lines = FIT.read_text(errors='replace').splitlines()
    start = next(i for i,l in enumerate(lines) if l.startswith('; Fitter Resource Utilization by Entity'))
    rows = []
    for line in lines[start+5:]:
        if line.startswith('+'):
            break
        c = [x.strip() for x in line.split(';')][1:-1]
        if len(c) < 17:
            continue
        rows.append(dict(hierarchy=c[14], alms=float(c[1].split()[0]),
            block_memory_bits=int(c[9].split()[0]), m10ks=int(c[10].split()[0]), dsps=int(c[11].split()[0])))
    with (OUT / 'resources.csv').open('w') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    return rows

# Every resolved switch in the SM64 bitstream is classified, including cuts.
# Resources attributed to a block are not additive feature-removal savings.
FEATURES = [
 ('triangle_setup', 'INCLUDE_VERT_TRI', 'Keep rasterization; possible worker replacement of setup',
  'Required by 0x4E and indexed triangles. Current shared derive is about 9% of entrance GPU time. Removing its equations requires a new full-attribute plane-input ABI, not simply turning off the flag.'),
 ('rgb565', 'INCLUDE_DIRECT_COLOR', 'Keep',
  'Rendering format and Gouraud shading, not an optional speed hack. Current SM64 requires it.'),
 ('combiner', 'INCLUDE_COMBINE', 'Keep',
  'Per-pixel texel*C+D preserves specular/additive materials. Historical cut saved about 884 ALMs but visibly darkened Mario head and did not improve timing; not a lossless cut.'),
 ('depth_window', 'INCLUDE_Z_BURST INCLUDE_Z_WINDOW8', 'Keep',
  'Amortizes z reads. Historical cache-equipped model regressed when reduced to four words. Published gains combine several changes and are not independent current-Pocket FPS deltas.'),
 ('cpu_select', 'VEXII_CPU_OS30', 'Keep current main CPU',
  'Single-issue RV32IMAFC with cache management; already no second issue lane or MMU. CPU-specific choices are audited separately.'),
 ('audio_mixer', 'INCLUDE_HW_MIXER', 'Keep hardware sample mixing',
  '776.1 attributed ALMs, 4 M10Ks, 10 DSPs. SM64 delegates per-note resampling/ramping/mixing here. Removing it moves substantial work onto the main CPU. Stock note presets can require 20, so 16 voices is not sufficient.'),
 ('fast_texture_memory', 'INCLUDE_TEX_MEM', 'Keep pending hardware A/B',
  'CRAM1 controller+adapter are about 195 ALMs before GPU routing/upload logic. SM64 selects it when available; avoids SDRAM contention. New replays use SDRAM textures and cannot quantify this hardware benefit.'),
 ('blend_window', 'INCLUDE_CB_WINDOW2', 'Keep',
  'Two-word destination read window amortizes real RGB565 alpha blending. Indexed translucency is disabled, but alpha blending is used. Historical ALM swings across sizes were packing noise, not hundreds of ALMs of direct savings.'),
 ('vi_filter', 'INCLUDE_VI_FILTER', 'Optional visual trade; low priority',
  'No game-throughput benefit. About 30 ALMs by prior estimate; changes output sharpness. Far too small to fund a geometry worker.'),
 ('vertex_cache', 'INCLUDE_XFORM', 'Keep or reuse worker output storage',
  'Indexed reuse/projection saves uploads and computation. Current vc_mem uses 5 M10Ks. Turning off the bundle also removes features required by the renderer.'),
 ('old_light_and_clip', 'EXCLUDE_GPU_LIGHT EXCLUDE_CLIP_TRI', 'Already cut',
  'Old one-light engine and direct clip-triangle opcode consume no removable feature block now. CPU SM64 lighting and near/far clipping still execute.'),
 ('command_transport', 'INCLUDE_CPU_RING EXCLUDE_GPU_COMMAND_DMA', 'Keep; redesign ownership for worker',
  'CPU upload transport avoids the command-DMA reader. Recent DMA experiment did not provide reliable net gain. The 16 KiB ring costs 16 M10Ks; shortening it needs a backpressure/batch-size audit.'),
 ('span_frontend', 'EXCLUDE_GPU_PARAM_SPAN_LIST', 'Already cut',
  '0x48 long-form input removed. Saved-seed earlier A/B was -254 ALMs/-1 M10K, retained pixels and cycles identical. Triangle-driven span producer must stay.'),
 ('coordinate_ram', 'INCLUDE_XFORM_VERTEX_RAM', 'Packing trade; keep until replaced',
  'Three tiny coordinate stores use 3 M10Ks. Historical register alternative freed those blocks but increased ALMs and worsened placement; unsuitable when the goal is logic headroom.'),
 ('projection_pipeline', 'INCLUDE_XFORM_RATIO_PIPE', 'Keep timing protection',
  'Adds two geometry cycles to break a long DSP feedback path; not a throughput accelerator. Remove only with a validated replacement pipeline.'),
 ('clamp_pipeline', 'INCLUDE_GPU_CLAMP_PIPE INCLUDE_GPU_CLAMP_FLAGS_PIPE', 'Keep timing structure',
  'Retimed coordinate clamp decisions preserve rendering cycle counts. No isolated frame-rate gain; protects critical paths.'),
 ('depth_pipeline', 'INCLUDE_Z_COMPARE_PARALLEL INCLUDE_Z_WINDOW_PREDECODE INCLUDE_EARLY_Z_CAPTURE', 'Keep timing structure',
  'Parallel compares, address predecode and early capture shorten depth paths; live data/forwarding semantics retained. Primarily timing, not fewer fragment cycles.'),
 ('blend_multipliers', 'INCLUDE_CB_SPLIT_MULT', 'Keep unless an A/B fit supports resharing',
  'Separates parked/normal blend multiplier selection. Same rendering work and schedule, simpler timing cone. Spare DSPs are not the current capacity limit.'),
 ('perspective_speculation', 'INCLUDE_PSS_SPECULATIVE_STEP', 'Keep',
  'Speculative constant-depth product removes a control dependency without changing consumed results; timing-oriented, no demonstrated independent whole-frame gain.'),
 ('span_multipliers', 'INCLUDE_SPAN_DEDICATED_MULT', 'Keep unless an A/B fit supports resharing',
  'Dedicated span multiplier pair reduces shared input muxing. Prior transport fixture had identical cycle counts; this is a timing/packing trade.'),
 ('texture_queues', 'INCLUDE_TEX_QUEUE_RAM INCLUDE_TEX_QUEUE_MLAB', 'Candidate RAM-for-logic trade',
  'Preserves elastic queue schedule. Texture request/result/metadata MLABs are attributed about 90 ALMs. After freeing main-cache RAM, test moving these back to approximately five M10Ks; physical savings must be refitted.'),
 ('edge_predecode', 'INCLUDE_EW_STEP_PREDECODE', 'Keep',
  'Prepares row transitions early; timing structure, not a rendering-feature cut. No independent current-game FPS claim.'),
 ('diagnostic_readback', 'EXCLUDE_SYSREG_DIAG_READBACK', 'Already cut',
  'Unconsumed system-register readbacks and CDC sources are pruned; required capabilities/status remain. Nothing further to reclaim from those excluded paths.'),
 ('render_cache', 'INCLUDE_GPU_RENDER_CACHE', 'Keep; 4 KiB is an optional small cut',
  '597.1 attributed ALMs/10 M10Ks at 8 KiB. Historical no-cache runs lose on memory-bound scenes. Fresh 4 KiB replay has modest gameplay cost; saves an estimated four M10Ks, not the whole controller.'),
 ('parallel_derivative_scale', 'INCLUDE_DERIVE_PARALLEL_SCALE', 'Keep',
  'Uses both already-present DSP ports to remove two wait/capture states per derivative without arithmetic changes. Real component-cycle reduction; little area to recover.'),
 ('depth_early_restart', 'INCLUDE_Z_EARLY_RESTART', 'Keep',
  'Uses requested z word while remaining window beats arrive. Real latency overlap with ordering/invalidation retained; not an independent whole-game FPS measurement.'),
 ('posted_write_queue', 'INCLUDE_GPU_WQ16', 'Keep',
  'Two eight-word bursts can be queued; physical-commit semantics retained. Earlier selected bundle cost +119 ALMs/+1 M10K and shortened clear/memory workloads. Removing it worsens an existing bandwidth bottleneck.'),
 ('depth_prefetch', 'INCLUDE_Z_PREFETCH', 'Keep in current SM64 trial',
  'Current-renderer replay improves GPU work by about 5-8% in gameplay; final combined fit costs +7 ALMs versus prior geometry fit. Not enabled by default in stock os30; no measured Pocket FPS improvement.'),
 ('geometry_frontend', 'INCLUDE_GPU_SM64_GEOMETRY', 'Replace rather than duplicate in worker design',
  'Adds roughly 918 ALMs and one M10K versus earlier normal os30 before depth-prefetch. Shares main GPU DSP/divider/FSM. Matrix/projection alone is only 5-7.5% of sampled GPU busy cycles; hardware improvement remains unverified.'),
]

CPU = [
 ('ICACHE_SETS', '256 / 32 KiB', 'Keep', '29 fitted M10Ks; halving costs 7.49% CPU time in fresh Bob model for about 12 M10Ks saved.'),
 ('DCACHE_SETS', '1024 / 128 KiB', 'Best RAM-budget candidate: 512 / 64 KiB', '128 data + 6 tag/metadata/victim M10Ks; approximately 66 M10Ks saved at +2.71% modeled CPU time. Real extra GPU contention is omitted.'),
 ('BTB_SETS', '1024', 'Keep initially; optional 512', '6 M10Ks now; about 3 saved for +0.85% CPU time in fresh model.'),
 ('GSHARE_BYTES', '4096 inherited default', 'Keep initially', '4 M10Ks now; about 3 saved at 1 KiB for +0.82% modeled CPU time. Earlier RTL display-list kernel also regressed with smaller GShare.'),
 ('DIV_RADIX', '4', 'Keep', 'Shared divider attributed 182.8 ALMs; earlier RTL transform kernel improved 13.1%, display-list kernel unchanged. Geometry offload reduces some demand but game/render fallbacks still use it.'),
 ('FPU_I2F_PIPELINED', '1', 'Keep', 'Prior RTL: 16 independent int-to-float conversions 66.3 -> 19.2 cycles; exact rounding tests pass. Current SM64 still converts values.'),
 ('EXTRA_FLAGS', '--fma-reduced-accuracy', 'Keep current behavior', 'Timing/area concession, not a proved whole-game speedup; preserves existing reduced-accuracy contract. Exact FMA would be a compatibility/area change.'),
 ('NO_SHIFT_TAPS', '1', 'Candidate RAM-for-logic trade after freeing RAM', 'Cycle-exact synthesis restriction saves M10Ks by retaining FFs. Once RAM is available, re-enable shift-register inference in an isolated fit; possible LAB relief, not guaranteed.'),
 ('FETCH_L1_PREFETCH', 'none', 'Already off', 'Earlier kernels had identical cycles with it removed; about 65 ALMs saved historically.'),
 ('LSU_SW_PREFETCH', '0', 'Already off', 'Unused prefetch instruction decode removed; software/cache-management flush instructions remain.'),
 ('RELAXED_LEARN', '0', 'Keep until timing A/B warrants change', 'Registered predictor update measured roughly +0.5% display-list cycles; not enabled.'),
 ('FETCH_READ_HOLD ALIGNER_PC_HOLD FETCH_READY_FACTOR LSU_WRITE_REG MAXFAN_HINT', '1 / 1 / 1 / 1 / 16', 'Keep', 'Correctness-preserving fetch holds, Boolean factoring, cache-write register+bypass and placement hint. Timing tools, not independent IPC improvements.'),
 ('RV32I M A F C + Zicbom', 'Enabled', 'Keep ABI and cache coherency', 'Hardware FP and multiply are fundamental to current software. Atomics/privileged CSR are ABI/runtime features, not geometry helpers. No safe unused-ISA cut established. Compressed instructions lower code footprint. Zicbom is required for CPU/GPU ownership changes.'),
 ('Issue/MMU/cache organization', 'One lane; no S-mode MMU; 2-way caches; 2 refill / 2 writeback slots; 2-line/16-op store buffer; RAS', 'Keep base CPU', 'No second lane, supervisor MMU, vector unit or L2 is hiding in os30. Predictors, bypass and queues support the busy main CPU; costs cannot be credited to a removable multicore subsystem.'),
 ('FPU pipeline and subnormal handling', 'Relaxed pipeline; ignore subnormal', 'Keep qualified pipeline', 'Shortened stressed-FPU experiment saved about 300 ALMs but fell to roughly 75 MHz fit timing. Cutting latency or precision further is not a free improvement.'),
]

def main():
    runtime = load(PRIOR / 'runtime-staged.json')
    assert sha(ROOT.parent / 'SM64/runtime/pocket/os30.rbf_r') == runtime['files_sha256']['runtime/pocket/os30.rbf_r']
    defs = runtime['configuration']['defs'].split()
    covered = [flag for _, flags, _, _ in FEATURES for flag in flags.split()]
    assert len(covered) == len(set(covered))
    assert set(defs) == set(covered), (set(defs)-set(covered), set(covered)-set(defs))
    rows = resources()
    cpu = load(OUT / 'cpu-screen.json')
    gpu = load(OUT / 'gpu-screen.json')
    assert cpu['baseline_matches_prior_framewise']
    assert len(gpu['results']) == 32
    aggregates = []
    for case in sorted({r['case'] for r in gpu['results']}):
        for mode in (1,2):
            a = [r for r in gpu['results'] if r['case']==case and r['mode']==mode]
            b = [r for r in a if r['frame'] >= 360]
            pct = lambda rr: 100*(sum(x['cycles'] for x in rr)/sum(x['baseline_cycles'] for x in rr)-1)
            aggregates.append(dict(case=case, mode=mode, all_extra_gpu_time_percent=pct(a),
                gameplay_extra_gpu_time_percent=pct(b), max_extra_gpu_time_percent=max(r['extra_gpu_time_percent'] for r in a)))
    path = OUT / 'cpu-baseline'
    profile = lambda f: {r['function']:r for r in csv.DictReader((path/f'profile-{f:04}.tsv').open(), delimiter='\t')}
    aa, bb = profile(360), profile(720)
    costs = {n:int(r['self_cycles'])-int(aa.get(n,{}).get('self_cycles',0)) for n,r in bb.items()}
    total = sum(costs.values())
    profile_rows = [dict(function=n, self_cycles=v, percent=100*v/total, ms_per_rendered_frame=v/360/100000)
        for n,v in sorted(costs.items(),key=lambda x:x[1],reverse=True)]
    states = []
    for frame in (360,400,500,600,720):
        text = (PRIOR / f'replays-prefetch/final-candidate/1/frame_{frame:05}.log').read_text()
        total_gpu = int(re.search(r'BN total (\d+)', text)[1])
        count = {int(a):int(b) for a,b in re.findall(r'BN state (\d+) (\d+)',text)}
        states.append(dict(frame=frame, cycles=total_gpu, ms=total_gpu/100000,
            transform_projection_percent=100*count.get(16,0)/total_gpu,
            triangle_derive_percent=100*count.get(15,0)/total_gpu,
            fragment_percent=100*count.get(6,0)/total_gpu))
    commands = collections.Counter()
    for row in load(PRIOR / 'replay-results-final-candidate-prefetch.json'):
        if row['mode']==1:
            commands.update(row['commands'])
    params = {flag.split('=')[0][2:]:int(flag.split('=')[1]) for flag in load(PRIOR / 'replay-prefetch-v1/manifest.json')['command'] if flag.startswith('-G')}
    report = dict(scope='Read-only production review, private sensitivity models; no core or SM64 implementation change',
        hardware_fps_improvement_verified=False, fit_source=str(FIT.relative_to(ROOT)), fit_sha256=sha(FIT),
        current_sm64_bitstream_sha256=runtime['files_sha256']['runtime/pocket/os30.rbf_r'],
        current_resources=dict(alms=17918, alm_capacity=18480, labs=1847, lab_capacity=1848,
            m10ks=307, m10k_capacity=308, dsps=41, dsp_capacity=66, configured_mhz=100,
            main_clock_fmax_mhz=91.04, setup_ns=-0.984, timing_closed=False),
        enabled_define_count=len(defs), enabled_defines=defs, gpu_parameters=params,
        feature_review=[dict(id=i,defines=d.split(),decision=v,evidence=e) for i,d,v,e in FEATURES],
        cpu_review=[dict(setting=i,value=v,decision=d,evidence=e) for i,v,d,e in CPU],
        attributed_entities=[r for r in rows if r['hierarchy'].count('|')==3],
        cpu_screen=cpu, gpu_screen_summary=aggregates, rendering_comparisons=32,
        cpu_profile=profile_rows, gpu_state_profile=states, sampled_command_counts=dict(commands),
        full_seed_sweep_run=False, new_fpga_fit_run=False, runtime_changed=False,
        limitations=[
            'No Pocket telemetry returned; the user reports unchanged subjective first-world performance.',
            'CPU timings are sensitivity estimates, not cycle-accurate full-system FPS; extra D-cache traffic does not feed back into GPU timing.',
            'GPU replay runs the real Pocket SDRAM path with synthetic background reads and SDRAM textures; not actual CRAM1 usage or CPU/GPU scheduling.',
            'New cache alternatives have not been fitted, so RAM savings are structural estimates and ALM/LAB savings are unknown.',
            'Fitter per-entity attribution and historical whole-core deltas are not additive removable-feature budgets.',
            'Only the 721-frame first-level route and eight sampled renderings are newly measured; other levels and Quake2 remain unmeasured.',
            'An independent worker and its queues/coherency/custom geometry instructions have not been implemented or fitted.'])
    (OUT / 'audit.json').write_text(json.dumps(report,indent=2)+'\n')
    details = [
        'OS30 FEATURE REVIEW - 2026-10-03',
        'Current SM64 geometry + depth-prefetch trial; default os30 differs.',
        'All 36 resolved build switches, grouped into 29 features, plus 15 CPU groups.',
        'Review coverage is not a claim of an isolated performance test for every switch.',
        'Fitted entity attribution is not an additive feature-removal budget.',
        '',
    ]
    for i, (name, flags, decision, evidence) in enumerate(FEATURES, 1):
        details.extend([f'G{i:02d}. {name}', f'  Switches: {flags}',
                        f'  Decision: {decision}', f'  Evidence: {evidence}', ''])
    for i, (setting, value, decision, evidence) in enumerate(CPU, 1):
        details.extend([f'C{i:02d}. {setting}', f'  Current: {value}',
                        f'  Decision: {decision}', f'  Evidence: {evidence}', ''])
    details.extend(['Resolved GPU replay parameters:', json.dumps(params, indent=2), ''])
    (OUT / 'feature-details.txt').write_text('\n'.join(details))
    with (OUT / 'features.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['feature','defines or setting','decision','evidence'])
        for i,d,v,e in FEATURES:w.writerow([i,d,v,e])
        for i,v,d,e in CPU:w.writerow([i,v,d,e])
    print(f'Covered all {len(defs)} fitted switches; {len(FEATURES)} feature groups, {len(CPU)} CPU groups; 32 matched render/depth comparisons.')

if __name__ == '__main__':
    main()
