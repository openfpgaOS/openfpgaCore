#!/usr/bin/env python3
"""Bind the qualified existing-register anchor-only stage to exact GPU tests."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
SOURCE=BASE/'gpu-anchor-stage-v1'
OUT=BASE/'validation/gpu-anchor-stage-v1'
RENDER=OUT/'rendering'
def read(p):return json.loads(p.read_text())
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    proof=read(SOURCE/'manifest.json');recipe=read(SOURCE/'recipe.json')
    audit_path=BASE/'gpu-anchor-stage-emitted-audit-v1/manifest.json';audit=read(audit_path)
    operand_path=BASE/'gpu-anchor-stage-operand-proof-v1/manifest.json';operand=read(operand_path)
    manifest=read(RENDER/'manifest.json');results=read(RENDER/'results.json')
    parent=read(BASE/'validation/texture-offer-v1/qualification.json')
    parent_manifest=read(BASE/'validation/texture-offer-v1/rendering/manifest.json')
    assert proof['candidate_sha256']==recipe['candidate_sha256']==audit['candidate_sha256']==operand['candidate_sha256']==manifest['candidate_sha256']==sha(SOURCE/'gpu_core.v')
    assert proof['reference_sha256']==recipe['reference_sha256']==manifest['baseline_sha256']==parent['gpu_sha256']
    phase_path=Path(audit['source_bound_independent_phase_review']);phase=read(phase_path)
    assert sha(phase_path)==audit['phase_review_sha256']
    assert phase['reference_sha256']==proof['reference_sha256']
    for key in ['passed','exact_complete_source_reconstruction_and_inverse',
                'only_two_existing_anchor_captures_and_two_operand_reads_changed',
                'four_edge_register_writers_and_four_slot1_slot2_reads_exact',
                'all_six_operands_identical_by_two16bit_capture_substitutions',
                'state_entry_reset_order_and_coordinate_stability_logic_unchanged',
                'actual_extracted_blocks_verified','focused_binary_rerun_pass']:
        assert audit[key],key
    for key in ['passed','all_original_dds_consumers_closed',
                'order_and_coordinates_stable_between_capture_and_delta','all_three_entries_restart_sort',
                'reset_and_abort_invalidate_scratch_before_later_observation']:
        assert phase[key],key
    assert operand['equal_all_six_16bit_operands'] and operand['actual_old_delta_reads']==operand['resolved_capture_low16_reads']
    assert operand['staged_anchor_bits']==proof['staged_anchor_bits']==32
    assert audit['new_registers']==audit['new_cycles']==0
    assert proof['compiled_arithmetic_check_cases']==3145728 and proof['compiled_check_is_supporting_not_exhaustive_96bit_arithmetic']
    assert sha(SOURCE/'stage_gpu_anchor.py')==recipe['script_sha256']
    for name,digest in recipe['evidence'].items():assert sha(SOURCE/name)==digest,name
    for path in [SOURCE/'patch-replay/gpu_core.v',BASE/'gpu-anchor-stage-replay-v1/gpu_core.v']:
        assert sha(path)==proof['candidate_sha256']
    for key in ['parameters','variant','flags','cflags']:assert manifest[key]==parent_manifest[key],key
    defs=manifest['variant']['defs'].split();assert 'INCLUDE_TEX_QUEUE_RAM' in defs and 'INCLUDE_TEX_QUEUE_MLAB' not in defs
    assert manifest['coordinate_monitor'] and manifest['texture_offer_monitor'] and manifest['ring_monitor']
    assert manifest['texture_cache_candidate_sha256']==manifest['texture_cache_baseline_sha256']==parent['cache_sha256']
    assert manifest['cache_candidate_sha256']==manifest['cache_baseline_sha256']==parent['render_cache_sha256']
    assert sha(RENDER/'runner-snapshot.py')==manifest['script_sha256']
    for name,digest in manifest['sources'].items():assert sha(RENDER/name)==digest,name
    for name,key in [('baseline-gpu_core.v','baseline_sha256'),('candidate-gpu_core.v','candidate_sha256'),
                     ('candidate-gpu_tex_cache.v','texture_cache_candidate_sha256'),('candidate-gpu_color_depth_cache.sv','cache_candidate_sha256')]:
        assert sha(RENDER/name)==manifest[key],name
    logs=[]
    for label in ['baseline','candidate']:
        build=read(RENDER/label/'manifest.json');assert sha(RENDER/label/'obj/Vtb_gpu')==build['simulator_sha256']
        assert ('+define+ANCHOR_STAGED_CANDIDATE' in build['command'])==(label=='candidate')
        for mode in ['quiet','delayed','gapped']:
            item=results['runs'][label][mode];assert sha(RENDER/label/(mode+'.log'))==item['log_sha256']
            assert item['passed']==79 and len(item['cycles'])==38 and item['texture_offer_monitor']['errors']=='0'
            assert item['coordinate_monitor']['captures']==item['coordinate_monitor']['deltas']>0
            assert item['coordinate_monitor']['consumers']>0
    assert results['identical_pixels_cycles_valid_payload_and_axi']
    for mode in ['quiet','delayed','gapped']:
        a,b=[results['runs'][v][mode] for v in ['baseline','candidate']];assert a==b,mode
        logs.append(dict(mode=mode,sha256=a['log_sha256'],entire_logs_identical=True))
    runs=list(results['runs']['candidate'].values())
    totals=lambda field:{k:sum(int(x[field][k]) for x in runs) for k in runs[0][field] if k not in ['payload_hash','axi_hash','hash']}
    monitor=totals('monitor');coords=totals('coordinate_monitor')
    assert monitor['cycles']==42548619 and monitor['hard']==387 and monitor['soft']==6
    files=[SOURCE/x for x in ['gpu_core.v','manifest.json','recipe.json','README.txt','stage_gpu_anchor.py','patch-replay/gpu_core.v']]
    files += [SOURCE/name for name in recipe['evidence']]
    files += [audit_path,audit_path.parent/'proof-rerun.log',phase_path,operand_path,BASE/'gpu-anchor-stage-replay-v1/gpu_core.v',RENDER/'manifest.json',RENDER/'results.json',RENDER/'runner-snapshot.py',Path(__file__)]
    files += [RENDER/v/n for v in ['baseline','candidate'] for n in ['manifest.json','build.log','quiet.log','delayed.log','gapped.log']]
    q=dict(passed=True,candidate_sha256=proof['candidate_sha256'],reference_sha256=proof['reference_sha256'],
        texture_cache_sha256=parent['cache_sha256'],render_cache_sha256=parent['render_cache_sha256'],
        independent_emitted_audit=True,universal_operand_substitution_and_phase_closure=True,
        supporting_extracted_arithmetic_cases=3145728,exact_guarded_replay_and_reversal=True,
        new_register_bits=0,new_cycles=0,staged_anchor_bits=32,selected_os30_bram_queue_configuration_exercised=True,
        all_other_render_configuration_unchanged=True,gpu_workloads=38,gpu_modes=3,gpu_pixel_checks=237,gpu_workload_cycle_pairs=114,
        whole_gpu_logs=logs,gpu_monitor_totals=monitor,coordinate_monitor_totals=coords,
        framebuffer_fifo_totals=totals('payload_pack_monitor'),texture_monitor_totals=totals('texture_offer_monitor'),
        limitations=['Component correctness and cycle comparison, not game FPS or integrated CPU/GPU performance.',
                    'Runtime phase/reset counters describe actual coverage; arbitrary reset/abort safety comes from the independent source/phase closure.',
                    'The supporting arithmetic cases do not exhaust96-bit values; universal equality is the exact operand substitution and unchanged signed arithmetic.',
                    'No added RTL FF/cycle does not guarantee no added LUTs or improved physical timing.'],
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in files})
    text=json.dumps(q,indent=2)+'\n';(OUT/'qualification.json').write_text(text);(SOURCE/'qualification.json').write_text(text)
    (OUT/'REPORT.txt').write_text(f'''GPU existing-register anchor-only staging: PASS

Candidate: {proof['candidate_sha256']}
Reference: {proof['reference_sha256']}
Texture cache: {parent['cache_sha256']}
Render cache: {parent['render_cache_sha256']}

Two 16-bit anchor captures in the existing PROD_L0 state reuse dd_x0/dd_y0.
DELTA retains all four slot1/2 reads; every signed 17-bit difference, DSP operand,
raw vertex, sorting decision, attribute, state transition and later read is
unchanged. No new RTL register bits or pipeline cycles. The independent
emitted audit closes every DDS observer and reset/abort phase boundary.
Actual operand substitution reproduces all six original 16-bit expressions;
the unchanged arithmetic therefore covers every 96-bit coordinate tuple.
The 3,145,728 extracted executable checks are supporting arithmetic evidence,
with 208,896 holds and 12,288 reset events, not exhaustive 96-bit enumeration.
Exact script replay, source reversal and full-module lint also pass.

All 38 retained os30 workloads pass in quiet, delayed and gapped modes with
the selected BRAM texture queue. All three entire logs are byte-identical:
114 workload cycle comparisons, 237 pixel checks, 42,548,619 monitored cycles,
387 hard resets and 6 soft resets per source. Qualified payload and AXI
cycle-indexed hashes match. Both GPUs check original-coordinate DELTA results;
the candidate also checks all 32 anchor bits after capture and while held.

Observed coordinate-phase counters (identical on both sides):
{json.dumps(coords,indent=2)}

Zero counters are not runtime coverage. Reset-aborted temporary DDS values
remain unobservable by the separately audited state/consumer relation.
This comparison does not measure game FPS. Physical fit/timing is separate;
anchor staging can affect LUT area and packing despite no new FFs.
''')
    print('PASS: anchor-stage exact sources/proofs and 38x3 rendering logs bound.')

if __name__=='__main__':main()
