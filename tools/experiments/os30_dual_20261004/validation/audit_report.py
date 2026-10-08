#!/usr/bin/env python3
"""Audit existing evidence and regenerate its report; never run simulations."""
import hashlib
import json
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
OUT=BASE/'validation'
PROGRAMS=['stress','i2f','kernels','icache','frontend','decode-traps','fp-legality']
PROFILES={
    'baseline128':'Shipping single issue; I32/D128; original predictors.',
    'single64':'Matched single issue control; I32/D64; original predictors.',
    'stock64':'Ordinary dual issue; I32/D64; original predictors.',
    'lean64':'Lane1 integer ALU only; I32/D64; original predictors.',
    'immediate64':'Lane1 immediate ALU/LUI/AUIPC only; I32/D64.',
    'single_i16_d128':'Matched single issue control; I16/D128; original predictors.',
    'lean_i16_d128':'Lane1 integer ALU only; I16/D128; original predictors.',
    'lean_smallpred_d128':'Lean dual; I32/D128; BTB256/GShare1KiB.',
    'rvc_stage1':'A: lean-smallpred with RVC expansion after existing raw register.',
    'rvc_pipeline':'B: A plus separate ISA decode stage; one extra pipeline stage.',
    'rvc_immediate':'C: A with immediate-only lane1; no added pipeline stage.',
    'rvc_pipeline_control':'B with combinational control factoring.',
    'rvc_stage1_control_rawfp':'A with control factoring and raw-instruction FP flags.',
    'rvc_skid_control':'REJECTED: B plus skid/control factoring; unmasked flushed lanes.',
    'rvc_skid_debug':'Diagnostic simulator of the same rejected CPU; additional taps.',
    'rvc_skid_masked_control':'B plus corrected full-ALU skid and control factoring.',
    'rvc_skid_immediate_control':'Corrected skid with immediate-only lane1.',
    'rvc_skid_fpu_match':'Corrected full-ALU skid plus registered FPU forwarding tag matches.',
    'rvc_skid_fpu_tag':'FPU-match CPU plus early D-cache tag data selection; original write gates.',
    'rvc_skid_fpu_tag_rs2':'Full-ALU FPU/tag CPU plus registered integer RS2 forwarding matches.',
    'rvc_skid_immediate_fpu_tag':'Immediate skid plus FPU matches and early D-cache tag data selection.',
    'rvc_skid_immediate_fpu_tag_rs2':'Immediate FPU/tag CPU plus registered integer RS2 forwarding matches.',
    'rvc_skid_address_fpu_tag_rs2':'Smallest lane1: ADDI/LUI/AUIPC only; corrected skid, full caches, all three timing fixes.',
    'rvc_skid_immediate_ready':'Immediate all-fixes CPU plus cycle-equivalent factored final decode ready cone.',
    'rvc_skid_immediate_ready_fp_preselect':'Ready-only CPU with eight FP tag comparisons moved before dispatch operand selection; no new registers.',
    'rvc_skid_immediate_preselect_src1':'Eight-endpoint FP preselection plus balanced nine-source integer SRC1 OR; priority and KEEP unchanged.',
    'rvc_skid_immediate_preselect_all':'SRC1 CPU with all 27 FP and eight integer RS2 matches preselected; live gates unchanged, no new registers or cycles.',
    'rvc_skid_immediate_raw_normalize':'All-match CPU with raw-instruction upper-half normalization factored into per-source qualifiers; exact RVC and no-selection semantics.',
    'rvc_skid_immediate_raw_hold_load':'RAW CPU plus held-frame aggregate trap factoring and byte-before-way load data; no new register or pipeline cycle.',
    'rvc_skid_immediate_pingpong':'RAW/hold/load CPU with two rotating payload banks; original occupancy/masks, one new pointer FF, no extra cycle.',
    'rvc_skid_immediate_pingpong_lsuhit':'Two-bank CPU plus aggregate LSU hit captured beside the original vector; one new FF, no extra cycle.',
    'rvc_skid_immediate_pingpong_trapdirect':'Aggregate-hit CPU with two direct aligner trap classifications before priority selection; no new register or cycle.',
    'rvc_skid_immediate_pingpong_trap_nohit':'Direct trap classification on two-bank CPU without the optional sampled-hit FF; controlled cycle-equivalent placement comparison.',
    'rvc_skid_immediate_pingpong_fp_last':'Direct-trap CPU plus exact lastFromBuffer classification and grouped FP RS1 forwarding; no new registers or cycles.',
    'rvc_skid_immediate_pingpong_pointer_packer_rs3':'FP/last CPU plus equivalent pointer D, mode-qualified FP packer rounding and grouped RS3 data; no added registers or cycles.',
    'rvc_skid_immediate_pingpong_pointer_packer':'FP/last CPU plus equivalent pointer D and mode-qualified FP packer rounding; exact pair now has its own fourteen-case suite.',
    'rvc_skid_immediate_pingpong_integer_rs1_fp_rs2':'Qualified pair plus integerRS1 2+7 and FP RS2 2+8 data groups; all original selectors, live gates and FFs unchanged.',
    'rvc_skid_immediate_pingpong_all_operand_groups':'Qualified integer/RS2 groups plus prior FP RS3 4+6 grouping; data-only composition, no new FFs or cycles.',
    'rvc_skid_immediate_pingpong_second_usable':'All-operand CPU with direct second-start survivor mask; exactly one combinational assignment, all state and ownership predicates unchanged.',
    'rvc_skid_immediate_pingpong_integer_rs1_reuse':'Pair5209 reuses two existing FP RS1 tag matches for integer forwarding; original live/write-enable gates retained, no new FF or cycle.',
    'rvc_skid_immediate_pingpong_integer_rs1_sample_lane1':'RS1 reuse CPU plus one sampled lane1 producer match; original live/write-enable qualification and cycle schedule retained.',
    'rvc_skid_immediate_pingpong_lane1_rdid':'Pair5209 specializes lane1 destination RF type to integer; decoded eligibility, traps and all live consumer boundaries proved, no capability or cycle change.',
    'rvc_skid_immediate_pingpong_shared_rs2_qualified':'Pair5209 absorbs aligned producer validity into an existing shared integer/FP RS2 match, preserving live qualification and the cycle schedule.',
    'rvc_skid_immediate_second_decode':'Pair5209 adds elastic storage for decoded instructions before dispatch. ISA results match; cycles can change and are measured separately.',
    'rvc_skid_immediate_pingpong_headvalid':'Pair5209 gives the existing head-valid register a direct default-one D input; exact from reset-established binary state, no new register or cycle.',
}

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def relative(path):return str(Path(path).relative_to(ROOT))
def read(path):return json.loads(path.read_text())

def main():
    report={'scope':__doc__,'profiles':{},'unexecuted_netlists':[]}
    for label,description in PROFILES.items():
        folder=OUT/label
        if not (folder/'build-manifest.json').exists():continue
        manifest=read(folder/'build-manifest.json')
        net=Path(manifest['netlist']);net_sha=sha(net)
        assert manifest['sources'][str(net)]==net_sha,label
        assert sha(folder/'obj/Vtb_system')==manifest['simulator_sha256'],label
        for filename in ['tb_irq.v','main.cpp']:
            path=folder/filename
            assert sha(path)==manifest['sources'][str(path)],path
        drift=[]
        for name,expected in manifest['sources'].items():
            path=Path(name)
            if not path.exists() or sha(path)!=expected:drift.append(name)
        # The monitor evolved for rejected-v7 diagnosis and ping-pong banks. The exact generated
        # C++ and Verilog that were compiled remain above and are hash-verified.
        assert all(Path(x)==HERE/'skid_monitor.py' for x in drift),(label,drift)
        profile=report['profiles'][label]=dict(description=description,
            netlist=relative(net),netlist_sha256=net_sha,
            simulator_sha256=manifest['simulator_sha256'],
            build_manifest_sha256=sha(folder/'build-manifest.json'),
            historical_generator_source_drift=drift,runs=[],coverage={})
        for program in PROGRAMS:
            statuses=[]
            for mode in ['quiet','scanout']:
                log=folder/f'{program}-{mode}.log'
                if not log.exists():continue
                text=log.read_text();uart=text.split('=== UART BEGIN ===')[-1].split('=== UART END ===')[0]
                passed='PASS' in uart and 'FAIL' not in uart
                if passed:
                    assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text,log
                statuses.append('pass' if passed else 'fail')
                profile['runs'].append(dict(program=program,mode=mode,passed=passed,log_sha256=sha(log)))
            profile['coverage'][program]=statuses
        profile['passed_runs']=sum(x['passed'] for x in profile['runs'])
        profile['failed_runs']=len(profile['runs'])-profile['passed_runs']
        # Bind every completed result record to its unchanged log and simulator.
        for filename in ['results','icache-results','frontend-results','decode-traps-results','fp-legality-results']:
            path=folder/(filename+'.json')
            if not path.exists():continue
            data=read(path)
            if 'simulator_sha256' in data:assert data['simulator_sha256']==manifest['simulator_sha256'],path
            for item in data['results']:
                program=item.get('program',filename.removesuffix('-results'))
                log=folder/f'{program}-{item["mode"]}.log'
                assert sha(log)==item['log_sha256'],log
                if 'simulator_sha256' in item:assert item['simulator_sha256']==manifest['simulator_sha256'],path
        results=folder/'results.json'
        if results.exists():
            rows=read(results)['results'];profile['kernels']={}
            for mode in ['quiet','scanout']:
                row=next(x for x in rows if x['program']=='kernels' and x['mode']==mode)
                profile['kernels'][mode]={x['name']:x['cycles'] for x in row['kernels'][7:]}
        coverage=folder/'skid-coverage.json'
        if coverage.exists():
            data=read(coverage);assert data['passed'];profile['skid_coverage']=data['totals']
            for item in data['runs']:
                assert sha(folder/f'{item["program"]}-{item["mode"]}.log')==item['log_sha256']
    report['shared_simulation_derivatives']={}
    ready=report['profiles'].get('rvc_skid_immediate_ready')
    if ready:
        directory=BASE/'decode-ready-v1'
        recipe=read(directory/'recipe.json')
        assert ready['netlist_sha256']==recipe['ready_sha256']
        assert sha(ROOT/recipe['reference'])==recipe['reference_sha256']
        for name,digest in recipe['patch_sha256'].items():assert sha(directory/name)==digest,name
        assert sha(directory/'immediate/decode_ready.py')==recipe['generator_script_sha256']
        equivalence=read(OUT/'rvc_skid_immediate_ready/cycle-equivalence.json')
        assert equivalence['identical_program_mode_logs']==14
        for item in equivalence['results']:
            assert sha(OUT/'rvc_skid_immediate_ready'/item['log'])==item['sha256']
            assert sha(OUT/equivalence['reference']/item['log'])==item['sha256']
        ready['all_fourteen_logs_identical_to']=equivalence['reference']
        ready['proof_evidence']={relative(p):sha(p) for p in [directory/'recipe.json',directory/'ready.patch',
            directory/'immediate/decode_ready.py',directory/'immediate/manifest.json',
            directory/'immediate/proof.log',directory/'immediate/tb.sv']}
        reference=directory/'immediate/VexiiRiscv.v'
        candidate=directory/'immediate-rs2-unkept/VexiiRiscv.v'
        manifest=read(directory/'immediate-rs2-unkept/manifest.json')
        assert sha(reference)==manifest['reference_sha256']==recipe['ready_sha256']
        assert sha(candidate)==manifest['candidate_sha256']==recipe['unkept_sha256']
        old,new=manifest['old_declaration'],manifest['new_declaration']
        assert reference.read_text().count(old)==1 and candidate.read_text().count(new)==1
        assert reference.read_text().replace(old,new)==candidate.read_text()
        # Exact, guarded reversal is stronger than stripping all attributes or
        # whitespace; no unrelated source difference can hide in normalization.
        report['shared_simulation_derivatives']['rvc_skid_immediate_ready_rs2_unkept']=dict(
            netlist=relative(candidate),netlist_sha256=sha(candidate),reference_profile='rvc_skid_immediate_ready',
            reference_netlist_sha256=sha(reference),direct_simulated_runs=0,shared_reference_runs=14,
            qualification='Exact one-declaration reversal; only keep/syn_keep synthesis attributes removed.',
            all_other_source_bytes_identical=True,
            proof_manifest_sha256=sha(directory/'immediate-rs2-unkept/manifest.json'),
            patch_sha256=sha(directory/'single-rs2-keep.patch'))
    fp_preselect=report['profiles'].get('rvc_skid_immediate_ready_fp_preselect')
    if fp_preselect:
        directory=BASE/'fp-preselect-v1/immediate';manifest=read(directory/'manifest.json')
        recipe=read(directory/'recipe.json')
        for name,digest in recipe['files'].items():assert sha(directory/name)==digest,name
        assert fp_preselect['netlist_sha256']==manifest['candidate_sha256']
        assert sha(ROOT/manifest['reference'])==manifest['reference_sha256']
        assert sha(directory/'fp_preselect.py')==manifest['script_sha256']
        assert sha(directory/'check_vexii_os20_timing.py')==manifest['checker_sha256']
        equivalence=read(OUT/'rvc_skid_immediate_ready_fp_preselect/cycle-equivalence.json')
        assert equivalence['identical_program_mode_logs']==14
        for item in equivalence['results']:
            assert sha(OUT/'rvc_skid_immediate_ready_fp_preselect'/item['log'])==item['sha256']
            assert sha(OUT/equivalence['reference']/item['log'])==item['sha256']
        fp_preselect['all_fourteen_logs_identical_to']=equivalence['reference']
        fp_preselect['proof_evidence']={relative(p):sha(p) for p in [directory/'recipe.json',directory/'manifest.json',
            directory/'fp_preselect.py',directory/'check_vexii_os20_timing.py',directory/'preselect.patch',
            directory/'dispatch/proof.log',directory/'matches/proof.log']}
    src1=report['profiles'].get('rvc_skid_immediate_preselect_src1')
    if src1:
        directory=BASE/'src1-balance-v1/immediate';recipe=read(directory/'recipe.json')
        for name,digest in recipe['files'].items():assert sha(directory/name)==digest,name
        assert sha(ROOT/recipe['base'])==recipe['base_sha256']
        assert src1['netlist_sha256']==recipe['replay']['output_sha256']
        assert recipe['replay']['input_sha256']==recipe['base_sha256'] and recipe['replay']['exact_patch_replay']
        emitted_path=BASE/'src1-balance-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['base_sha256']==recipe['base_sha256'] and emitted['candidate_sha256']==src1['netlist_sha256']
        assert emitted['exact_emitted_cones_match_proof'] and emitted['reverse_source_equality']
        for name,digest in emitted['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        equivalence=read(OUT/'rvc_skid_immediate_preselect_src1/cycle-equivalence.json')
        assert equivalence['identical_program_mode_logs']==14
        for item in equivalence['results']:
            assert sha(OUT/'rvc_skid_immediate_preselect_src1'/item['log'])==item['sha256']
            assert sha(OUT/equivalence['reference']/item['log'])==item['sha256']
        src1['all_fourteen_logs_identical_to']=equivalence['reference']
        src1['proof_evidence']={relative(p):sha(p) for p in [directory/'recipe.json',directory/'manifest.json',
            directory/'src1_balance.py',directory/'check_vexii_os20_timing.py',directory/'src1-balance.patch',
            directory/'tb.sv',directory/'proof.log',emitted_path]}
        src1['independent_emitted_audit_verified']=True
        src1['exact_patch_replay_matches_tested_rtl']=True
    all_matches=report['profiles'].get('rvc_skid_immediate_preselect_all')
    if all_matches:
        directory=BASE/'match-preselect-all-v1/immediate';recipe=read(directory/'recipe.json')
        manifest=read(directory/'manifest.json')
        for name,digest in recipe['files'].items():assert sha(directory/name)==digest,name
        assert sha(ROOT/recipe['base'])==recipe['base_sha256']
        assert all_matches['netlist_sha256']==recipe['replay']['output_sha256']==manifest['candidate_sha256']
        assert recipe['replay']['input_sha256']==recipe['base_sha256'] and recipe['replay']['exact_patch_replay']
        emitted_path=BASE/'all-match-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['base_sha256']==recipe['base_sha256'] and emitted['candidate_sha256']==all_matches['netlist_sha256']
        for key in ['all_other_source_bytes_reverse_exactly','all_35_actual_rhs_match_exhaustive_harness',
                    'actual_endpoint_inputs_checked_independently','previous_down_rd_enable_preserved',
                    'all_ff_enables_and_current_gating_unchanged','actual_candidate_dispatch_cone_matches_onehot_proof']:
            assert emitted[key],key
        for name,digest in emitted['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        equivalence=read(OUT/'rvc_skid_immediate_preselect_all/cycle-equivalence.json')
        assert equivalence['identical_program_mode_logs']==14
        assert equivalence['candidate_cpu_sha256']==all_matches['netlist_sha256']
        for item in equivalence['results']:
            assert sha(OUT/'rvc_skid_immediate_preselect_all'/item['log'])==item['sha256']
            assert sha(OUT/equivalence['reference']/item['log'])==item['sha256']
        all_matches['all_fourteen_logs_identical_to']=equivalence['reference']
        all_matches['proof_evidence']={relative(p):sha(p) for p in [directory/'recipe.json',directory/'manifest.json',
            directory/'match_preselect_all.py',directory/'check_vexii_os20_timing.py',directory/'preselect-all.patch',
            directory/'endpoint-input-audit.json',directory/'dispatch/tb.sv',directory/'dispatch/proof.log',
            directory/'matches/tb.sv',directory/'matches/proof.log',emitted_path]}
        all_matches['independent_emitted_audit_verified']=True
        all_matches['exact_patch_replay_matches_tested_rtl']=True
    for label in ['rvc_skid_immediate_raw_normalize','rvc_skid_immediate_raw_hold_load','rvc_skid_immediate_pingpong_lsuhit','rvc_skid_immediate_pingpong_trapdirect','rvc_skid_immediate_pingpong_trap_nohit','rvc_skid_immediate_pingpong_fp_last']:
        profile=report['profiles'].get(label)
        if not profile:continue
        equivalence=read(OUT/label/'cycle-equivalence.json')
        assert profile['passed_runs']==14 and profile['failed_runs']==0
        assert equivalence['identical_program_mode_logs']==14
        assert equivalence['candidate_cpu_sha256']==profile['netlist_sha256']
        for item in equivalence['results']:
            assert sha(OUT/label/item['log'])==item['sha256']
            assert sha(OUT/equivalence['reference']/item['log'])==item['sha256']
        profile['all_fourteen_logs_identical_to']=equivalence['reference']
    raw=report['profiles'].get('rvc_skid_immediate_raw_normalize')
    if raw:
        directory=BASE/'raw-normalize-v1/immediate';manifest=read(directory/'manifest.json')
        assert sha(ROOT/manifest['reference'])==manifest['reference_sha256']
        assert raw['netlist_sha256']==manifest['candidate_sha256']
        assert sha(directory/'raw_normalize.py')==manifest['script_sha256']
        assert sha(directory/'check_vexii_os20_timing.py')==manifest['checker_sha256']
        emitted_path=BASE/'raw-normalize-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['candidate_sha256']==raw['netlist_sha256']
        assert emitted['reference_sha256']==manifest['reference_sha256']
        assert emitted['all_other_source_bytes_reverse_exactly'] and emitted['exact_emitted_cones_match_proof']
        for path,digest in emitted['evidence_sha256'].items():assert sha(ROOT/path)==digest,path
        files=[directory/name for name in ['manifest.json','raw_normalize.py','raw-normalize.patch',
            'bit-dependency-audit.json','selectors/tb.sv','selectors/proof.log','data/tb.sv','data/proof.log']]
        raw['proof_evidence']={relative(p):sha(p) for p in files+[emitted_path]}
        raw['independent_emitted_audit_verified']=True
    combined=report['profiles'].get('rvc_skid_immediate_raw_hold_load')
    if combined:
        directory=BASE/'raw-hold-load-v1/immediate';manifest=read(directory/'manifest.json')
        assert sha(ROOT/manifest['reference'])==manifest['reference_sha256']
        assert combined['netlist_sha256']==manifest['candidate_sha256']
        assert sha(directory/'compose_raw_hold_load.py')==manifest['script_sha256']
        assert manifest['both_orders_byte_identical'] and manifest['all_unrelated_source_unchanged']
        files=[directory/'manifest.json',directory/'compose_raw_hold_load.py']
        for name,item in manifest['parts'].items():
            part=ROOT/item['path'];pm=read(part/'manifest.json')
            patch='inflight-hold.patch' if name=='hold' else 'byte-before-way.patch'
            assert sha(part/'VexiiRiscv.v')==item['candidate_sha256']==pm['candidate_sha256']
            assert pm['reference_sha256']==manifest['reference_sha256']
            assert sha(part/patch)==item['patch_sha256']==sha(directory/patch)
            assert (part/'manifest.json').read_bytes()==(directory/(name+'-manifest.json')).read_bytes()
            files += [part/'manifest.json',part/patch,directory/(name+'-manifest.json')]
            if name=='hold':
                assert sha(part/'inflight_hold.py')==pm['script_sha256']
                assert sha(part/'check_vexii_os20_timing.py')==pm['checker_sha256']
                files += [part/'inflight_hold.py',part/'check_vexii_os20_timing.py']
                for proof in ['lsu-implication','decode-decomposition','held-bypass']:
                    assert 'PASS' in (part/proof/'proof.log').read_text()
                    files += [part/proof/'tb.sv',part/proof/'proof.log']
        for item in manifest['order_proofs']:
            assert item['candidate_sha256']==combined['netlist_sha256'] and item['reverse_restores_base']
        load=BASE/'load-byte-factor-v1/immediate';recipe=read(load/'recipe.json')
        for name,digest in recipe['files'].items():assert sha(load/name)==digest,name
        assert sha(load/'replay/VexiiRiscv.v')==recipe['replay_sha256']
        emitted_path=BASE/'load-byte-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['candidate_sha256']==manifest['parts']['load']['candidate_sha256']
        for path,digest in emitted['evidence_sha256'].items():assert sha(ROOT/path)==digest,path
        assert emitted['actual_harness_statements_and_declarations_verified']
        assert emitted['fixed_control_zero_and_or_preservation_independently_reviewed']
        assert emitted['arbitrary_96_bit_word_equality_follows_from_basis_and_or_preservation']
        files += [load/'recipe.json',emitted_path]
        hold_audit_path=BASE/'inflight-hold-emitted-audit-v1/manifest.json';hold_audit=read(hold_audit_path)
        assert hold_audit['reference_sha256']==manifest['reference_sha256']
        assert hold_audit['combined_sha256']==combined['netlist_sha256']
        assert hold_audit['hold_sha256']==manifest['parts']['hold']['candidate_sha256']
        assert hold_audit['load_sha256']==manifest['parts']['load']['candidate_sha256']
        for path,digest in hold_audit['evidence_sha256'].items():assert sha(ROOT/path)==digest,path
        for key in ['ordinary_decode_contribution_is_exact_original_expression',
                    'actual_skid_valid_flushing_storage_lane_masks_unchanged',
                    'implication_and_decomposition_actual_cones_verified',
                    'actual_held_bypass_cones_verified','no_unproved_sequential_invariant',
                    'combined_edits_disjoint_and_reverse_both_orders_exactly']:
            assert hold_audit[key],key
        files.append(hold_audit_path)
        combined['proof_evidence']={relative(p):sha(p) for p in files}
        combined['byte_patch_independent_emitted_audit_verified']=True
        combined['hold_and_combination_independent_emitted_audit_verified']=True
        combined['both_patch_orders_match_tested_rtl']=True
    pingpong=report['profiles'].get('rvc_skid_immediate_pingpong')
    if pingpong:
        label='rvc_skid_immediate_pingpong';folder=OUT/label
        assert pingpong['passed_runs']==14 and pingpong['failed_runs']==0
        equivalence=read(folder/'cycle-equivalence.json')
        assert equivalence['candidate_cpu_sha256']==pingpong['netlist_sha256']
        assert equivalence['excluded_lines']==['PINGPONG COVERAGE']
        assert equivalence['identical_program_mode_logs_after_filtering_extra_bank_coverage']==14
        for item in equivalence['results']:
            log=folder/item['log'];reference=OUT/equivalence['reference']/item['log']
            assert sha(log)==item['candidate_sha256'] and sha(reference)==item['reference_sha256']
            lines=log.read_bytes().splitlines(keepends=True)
            assert sum(x.startswith(b'PINGPONG COVERAGE') for x in lines)==1
            normalized=b''.join(x for x in lines if not x.startswith(b'PINGPONG COVERAGE'))
            assert normalized==reference.read_bytes()
            assert hashlib.sha256(normalized).hexdigest()==item['normalized_sha256']
        coverage=read(folder/'pingpong-coverage.json');totals={}
        assert coverage['passed'] and coverage['candidate_cpu_sha256']==pingpong['netlist_sha256']
        for item in coverage['runs']:
            line=next(x for x in (folder/item['log']).read_text().splitlines() if x.startswith('PINGPONG COVERAGE'))
            counters={key:int(value) for key,value in re.findall(r'(\w+)=(\d+)',line)}
            assert counters==item['counters'] and counters['errors']==0
            for key,value in counters.items():totals[key]=totals.get(key,0)+value
        assert totals==coverage['totals']
        directory=BASE/'decode-pingpong-v1/immediate';recipe=read(directory/'recipe.json')
        for name,digest in recipe['files_sha256'].items():assert sha(directory/name)==digest,name
        assert sha(directory/'reverse-replay/VexiiRiscv.v')==pingpong['netlist_sha256']
        manifest=read(directory/'manifest.json')
        assert manifest['candidate_sha256']==pingpong['netlist_sha256']
        assert sha(ROOT/manifest['reference'])==manifest['reference_sha256']
        assert manifest['extra_control_ffs']==1 and manifest['extra_pipeline_cycles']==0
        emitted_path=BASE/'pingpong-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['candidate_sha256']==pingpong['netlist_sha256']
        for name,digest in emitted['proof_sha256'].items():assert sha(directory/name)==digest,name
        assert emitted['exact_scope_reconstruction'] and emitted['unchanged_ready_occupancy_masks_flush_reset']
        assert emitted['head_payload_exact_after_nonreset_edge'] and emitted['tail_payload_only_observed_when_occupied']
        consumer=OUT/'pingpong-consumer-audit-v1';cm=read(consumer/'manifest.json')
        assert cm['candidate_sha256']==pingpong['netlist_sha256']
        assert sha(consumer/'consumers.json')==cm['consumer_census_sha256']
        assert sha(consumer/'guards.json')==cm['guard_extracts_sha256']
        assert sha(HERE/'audit_pingpong_consumers.py')==cm['audit_script_sha256']
        files=[directory/name for name in recipe['files_sha256']]+[directory/'recipe.json',emitted_path,
            consumer/'manifest.json',consumer/'consumers.json',consumer/'guards.json',consumer/'REVIEW.txt',
            OUT/'skid-pingpong-map.json',folder/'cycle-equivalence.json',folder/'pingpong-coverage.json']
        pingpong['proof_evidence']={relative(p):sha(p) for p in files}
        pingpong['pingpong_coverage']=totals
        pingpong['normalized_logs_identical_to']=equivalence['reference']
        pingpong['only_excluded_log_lines']=['PINGPONG COVERAGE']
        pingpong['independent_emitted_and_consumer_audits_verified']=True
    lsuhit=report['profiles'].get('rvc_skid_immediate_pingpong_lsuhit')
    if lsuhit:
        directory=BASE/'lsu-hit-sampled-v1/immediate';recipe=read(directory/'recipe.json');manifest=read(directory/'manifest.json')
        for name,digest in recipe['files_sha256'].items():assert sha(directory/name)==digest,name
        assert manifest['candidate_sha256']==lsuhit['netlist_sha256']
        assert sha(directory/'patch-replay/VexiiRiscv.v')==lsuhit['netlist_sha256']
        assert manifest['reference_sha256']==pingpong['netlist_sha256']
        assert manifest['added_register_bits']==1 and manifest['extra_pipeline_cycles']==0
        emitted_path=BASE/'lsu-hit-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['candidate_sha256']==lsuhit['netlist_sha256']
        for key in ['exactly_one_declaration_one_capture_one_alias_changed','original_vector_and_all_five_consumers_exact',
                    'unreset_payload_and_identical_advance_enable_verified','stage4_selects_reset_clear_and_capture_with_same_enable']:
            assert emitted[key],key
        coverage=read(OUT/'rvc_skid_immediate_pingpong_lsuhit/pingpong-coverage.json')
        assert coverage['passed'] and coverage['candidate_cpu_sha256']==lsuhit['netlist_sha256']
        assert coverage['totals']==pingpong['pingpong_coverage']
        files=[directory/name for name in recipe['files_sha256']]+[directory/'recipe.json',emitted_path]
        lsuhit['proof_evidence']={relative(p):sha(p) for p in files}
        lsuhit['independent_emitted_audit_verified']=True
        lsuhit['pingpong_coverage']=coverage['totals']
    trapdirect=report['profiles'].get('rvc_skid_immediate_pingpong_trapdirect')
    if trapdirect:
        folder=OUT/'rvc_skid_immediate_pingpong_trapdirect';qualification=read(folder/'qualification.json')
        assert qualification['passed'] and qualification['passed_program_mode_cases']==14
        assert qualification['candidate_sha256']==trapdirect['netlist_sha256']
        assert qualification['reference_sha256']==lsuhit['netlist_sha256']
        for name,digest in qualification['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        directory=BASE/'aligner-trap-direct-v1/immediate';manifest=read(directory/'manifest.json')
        assert manifest['candidate_sha256']==trapdirect['netlist_sha256']
        assert manifest['reference_sha256']==lsuhit['netlist_sha256']
        assert manifest['independent_lane_cases']==393216 and manifest['actual_extracted_source_blocks']==69
        assert manifest['extra_registers']==0 and manifest['extra_cycles']==0
        assert sha(directory/'patch-replay/VexiiRiscv.v')==trapdirect['netlist_sha256']
        emitted=read(BASE/'aligner-trap-emitted-audit-v1/manifest.json')
        assert emitted['candidate_sha256']==trapdirect['netlist_sha256']
        assert emitted['reference_sha256']==lsuhit['netlist_sha256']
        for key in ['exact_two_assignment_reconstruction','proof_wrappers_match_emitted',
                    'lane_dependency_cut_checked','actual_binary_rerun_passed','no_register_or_cycle_changes']:
            assert emitted[key],key
        assert emitted['actual_extracted_blocks_checked']==69 and emitted['independent_math_cases']==393216
        coverage=read(folder/'pingpong-coverage.json')
        assert coverage['passed'] and coverage['candidate_cpu_sha256']==trapdirect['netlist_sha256']
        assert coverage['totals']==lsuhit['pingpong_coverage']
        for item in coverage['runs']:assert sha(folder/item['log'])==item['log_sha256']
        trapdirect['proof_evidence']=qualification['evidence_sha256']
        trapdirect['independent_emitted_audit_verified']=True
        trapdirect['pingpong_coverage']=coverage['totals']
        trapdirect['limitations']=qualification['limitations']
    trap_nohit=report['profiles'].get('rvc_skid_immediate_pingpong_trap_nohit')
    if trap_nohit:
        directory=BASE/'aligner-trap-no-sampled-hit-v1/immediate';folder=OUT/'rvc_skid_immediate_pingpong_trap_nohit'
        manifest=read(directory/'manifest.json');recipe=read(directory/'recipe.json');qualification=read(directory/'qualification.json')
        assert qualification['passed']
        assert qualification['candidate_cpu_sha256']==manifest['candidate_sha256']==recipe['candidate_sha256']==trap_nohit['netlist_sha256']
        assert manifest['reference_sha256']==pingpong['netlist_sha256']
        assert manifest['controlled_comparison_sha256']==trapdirect['netlist_sha256']
        assert manifest['actual_trap_cone_blocks_identical']==69 and manifest['qualified_trap_cases_rerun']==393216
        for key in ['commuting_composition_exact','reverse_each_component_exact','sampled_hit_vector_and_five_consumers_unchanged',
                    'sampled_shadow_register_absent','all_sequential_source_identical_to_d471','exact_patch_replay']:
            assert manifest[key],key
        assert sha(directory/'without_sampled_hit.py')==recipe['script_sha256']
        for name,digest in recipe['patches'].items():assert sha(directory/name)==digest,name
        assert sha(directory/'patch-replay/VexiiRiscv.v')==trap_nohit['netlist_sha256']
        for key,name in [('source_recipe_sha256','recipe.json')]:assert sha(directory/name)==qualification[key]
        for key,name in [('skid_coverage_sha256','skid-coverage.json'),('pingpong_coverage_sha256','pingpong-coverage.json'),('cycle_equivalence_sha256','cycle-equivalence.json')]:
            assert sha(folder/name)==qualification[key],name
        equivalence=read(folder/'cycle-equivalence.json')
        for item in equivalence['results']:
            for reference,equal in item['byte_identical_to'].items():assert equal and sha(OUT/reference/item['log'])==item['sha256']
        emitted_path=BASE/'aligner-trap-no-hit-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert sha(emitted_path)==qualification['independent_audit_sha256']
        assert emitted['candidate_sha256']==trap_nohit['netlist_sha256']
        for key in ['exactly_pingpong_plus_two_qualified_trap_equations','exactly_full_trap_candidate_minus_three_sampled_hit_edits',
                    'all_baseline_sequential_source_and_hit_vector_consumers_exact','zero_added_state_or_cycles',
                    'qualified_trap_equations_identical_to_exhaustively_proved_ec69']:
            assert emitted[key],key
        coverage=read(folder/'pingpong-coverage.json');assert coverage['passed'] and coverage['totals']==pingpong['pingpong_coverage']
        assert coverage['candidate_cpu_sha256']==trap_nohit['netlist_sha256']
        files=[directory/n for n in ['manifest.json','qualification.json','recipe.json','without_sampled_hit.py',
               'trap-only.patch','remove-sampled-hit.patch','proof-rerun.log','lint.log']]+[emitted_path]
        trap_nohit['proof_evidence']={relative(p):sha(p) for p in files}
        trap_nohit['independent_emitted_audit_verified']=True
        trap_nohit['all_fourteen_logs_also_identical_to']=equivalence['additional_identical_references']
        trap_nohit['pingpong_coverage']=coverage['totals']
    fp_last=report['profiles'].get('rvc_skid_immediate_pingpong_fp_last')
    if fp_last:
        directory=BASE/'fp-last-combined-v1/immediate';folder=OUT/'rvc_skid_immediate_pingpong_fp_last'
        manifest=read(directory/'manifest.json');recipe=read(directory/'recipe.json');qualification=read(directory/'qualification.json')
        assert qualification['passed'] and qualification['candidate_cpu_sha256']==manifest['candidate_sha256']==fp_last['netlist_sha256']
        assert manifest['reference_sha256']==trapdirect['netlist_sha256']
        assert manifest['new_register_bits']==0 and manifest['new_pipeline_cycles']==0
        assert manifest['focused_fp_cases_rerun']==1048576 and manifest['focused_last_cases_rerun']==393216
        for key in ['source_composition_orders_identical','original_patch_orders_identical','reversals_restore_each_parent_exactly',
                    'full_sequential_source_unchanged','composed_fp_actual_cone_identical','composed_last_actual69_blocks_identical','exact_patch_replay']:
            assert manifest[key],key
        assert sha(directory/'compose_fp_last.py')==recipe['script_sha256']
        for name,digest in recipe['patch_sha256'].items():assert sha(directory/name)==digest,name
        for name in ['order-last-fp','order-fp-last','replay-base','replay-fp','replay-last']:
            assert sha(directory/name/'VexiiRiscv.v')==fp_last['netlist_sha256']
        assert sha(directory/'recipe.json')==qualification['source_recipe_sha256']
        for key,name in [('skid_coverage_sha256','skid-coverage.json'),('pingpong_coverage_sha256','pingpong-coverage.json'),('cycle_equivalence_sha256','cycle-equivalence.json')]:
            assert sha(folder/name)==qualification[key],name
        equivalence=read(folder/'cycle-equivalence.json')
        for item in equivalence['results']:
            for reference,equal in item['byte_identical_to'].items():assert equal and sha(OUT/reference/item['log'])==item['sha256']
        emitted_path=BASE/'fp-last-composition-audit-v1/manifest.json';emitted=read(emitted_path)
        assert sha(emitted_path)==qualification['independent_audit_sha256']
        assert emitted['source_sha256']['combined']==fp_last['netlist_sha256']
        for key in ['both_source_orders_identical','both_component_reversals_exact','composed_equations_identical_to_independently_audited_components','no_other_source_changes','all_sequential_source_exact']:
            assert emitted[key],key
        files=[directory/n for n in ['manifest.json','qualification.json','recipe.json','compose_fp_last.py','from-base.patch',
               'from-fp.patch','from-last.patch','fp-proof-rerun.log','last-proof-rerun.log','lint.log']]+[emitted_path]
        for name,digest in qualification['component_audits'].items():
            path=BASE/name/'manifest.json';assert sha(path)==digest;files.append(path)
        coverage=read(folder/'pingpong-coverage.json');assert coverage['passed'] and coverage['totals']==pingpong['pingpong_coverage']
        assert coverage['candidate_cpu_sha256']==fp_last['netlist_sha256']
        fp_last['proof_evidence']={relative(p):sha(p) for p in files}
        fp_last['independent_emitted_audit_verified']=True
        fp_last['all_fourteen_logs_also_identical_to']=equivalence['additional_identical_references']
        fp_last['pingpong_coverage']=coverage['totals']
    triple=report['profiles'].get('rvc_skid_immediate_pingpong_pointer_packer_rs3')
    if triple:
        directory=BASE/'pointer-packer-rs3-combined-v1/immediate';folder=OUT/'rvc_skid_immediate_pingpong_pointer_packer_rs3'
        manifest=read(directory/'manifest.json');recipe=read(directory/'recipe.json');qualification=read(directory/'qualification.json')
        assert qualification['passed'] and qualification['candidate_cpu_sha256']==manifest['candidate_sha256']==triple['netlist_sha256']
        assert manifest['reference_sha256']==fp_last['netlist_sha256']
        assert manifest['new_register_bits']==manifest['new_cycles']==0
        for key in ['all_six_original_patch_orders_identical','all_six_reverse_orders_restore_base',
                    'reversals_restore_dual_and_rs3_parents','actual_rs3_full_cone_identical',
                    'actual_packer_and_pointer_proof_statements_unchanged','all_three_focused_proofs_rerun','full_netlist_lint']:
            assert manifest[key],key
        assert sha(directory/'compose_pointer_packer_rs3.py')==recipe['script_sha256']
        for name,digest in recipe['patch_sha256'].items():assert sha(directory/name)==digest,name
        # Each six-order worktree is deliberately left at the base after
        # checking the candidate and reversing its complete component order.
        for path in directory.glob('order-*/VexiiRiscv.v'):assert sha(path)==fp_last['netlist_sha256'],path
        assert len(list(directory.glob('order-*/VexiiRiscv.v')))==6
        for name in ['replay-base','replay-dual','replay-rs3']:
            assert sha(directory/name/'VexiiRiscv.v')==triple['netlist_sha256']
        assert sha(BASE/'pointer-packer-rs3-combined-replay-v1/immediate/VexiiRiscv.v')==qualification['clean_source_reproduction_sha256']==triple['netlist_sha256']
        assert sha(directory/'recipe.json')==qualification['source_recipe_sha256']
        assert sha(folder/'build-manifest.json')==qualification['test_build_manifest_sha256']
        for key,name in [('skid_coverage_sha256','skid-coverage.json'),('pingpong_coverage_sha256','pingpong-coverage.json'),('cycle_equivalence_sha256','cycle-equivalence.json')]:
            assert sha(folder/name)==qualification[key],name
        equivalence=read(folder/'cycle-equivalence.json')
        assert len(equivalence['results'])==14 and equivalence['candidate_cpu_sha256']==triple['netlist_sha256']
        for item in equivalence['results']:
            assert sha(folder/item['log'])==item['sha256']
            for reference,equal in item['byte_identical_to'].items():assert equal and sha(OUT/reference/item['log'])==item['sha256']
        emitted_path=BASE/'pointer-packer-rs3-composition-audit-v1/manifest.json';emitted=read(emitted_path)
        assert sha(emitted_path)==qualification['independent_audit_sha256']
        assert emitted['source_sha256']['triple']==triple['netlist_sha256']
        for key in ['both_dual_orders_match','all_six_triple_orders_match','all_six_triple_reverse_orders_restore_base',
                    'removing_only_rs3_restores_dual','no_other_source_edits','components_match_independently_audited_equations',
                    'existing_pointer_next_state_equivalent_all_other_state_exact']:
            assert emitted[key],key
        files=[directory/n for n in ['manifest.json','qualification.json','recipe.json','compose_pointer_packer_rs3.py',
               'from-base.patch','from-dual.patch','from-rs3.patch','pointer-proof-rerun.log','packer-proof-rerun.log','rs3-proof-rerun.log','lint.log']]+[emitted_path]
        for name,digest in qualification['component_audits'].items():
            path=BASE/name/'manifest.json';assert sha(path)==digest;files.append(path)
        coverage=read(folder/'pingpong-coverage.json');assert coverage['passed'] and coverage['totals']==pingpong['pingpong_coverage']
        assert coverage['candidate_cpu_sha256']==triple['netlist_sha256']
        triple['proof_evidence']={relative(p):sha(p) for p in files}
        triple['independent_emitted_audit_verified']=True
        triple['all_fourteen_logs_also_identical_to']=equivalence['additional_identical_references']
        triple['pingpong_coverage']=coverage['totals']
    for label,directory_name,script,patch_key,audit_name,source_key in [
        ('rvc_skid_immediate_pingpong_pointer_packer','pointer-packer-combined-v1','compose_pointer_packer.py','patch_hashes','pointer-packer-rs3-composition-audit-v1','dual'),
        ('rvc_skid_immediate_pingpong_integer_rs1_fp_rs2','integer-rs1-fp-rs2-combined-v1','compose_integer_rs1_fp_rs2.py','patches','integer-rs1-fp-rs2-composition-audit-v1','combined'),
        ('rvc_skid_immediate_pingpong_all_operand_groups','all-operand-groups-v1','compose_all_operand_groups.py','patches','all-operand-groups-composition-audit-v1','final')]:
        profile=report['profiles'].get(label);directory=BASE/directory_name/'immediate';folder=OUT/label
        if not profile or not (directory/'qualification.json').exists():continue
        manifest=read(directory/'manifest.json');recipe=read(directory/'recipe.json');qualification=read(directory/'qualification.json')
        assert qualification['passed'] and qualification['candidate_cpu_sha256']==manifest['candidate_sha256']==profile['netlist_sha256']
        assert sha(directory/script)==recipe['script_sha256']
        for name,digest in recipe[patch_key].items():assert sha(directory/name)==digest,name
        for path in directory.glob('replay-*/VexiiRiscv.v'):assert sha(path)==profile['netlist_sha256'],path
        replay=BASE/directory_name.replace('-v1','-replay-v1')/'immediate/VexiiRiscv.v'
        assert sha(replay)==qualification['clean_source_reproduction_sha256']==profile['netlist_sha256']
        assert sha(directory/'recipe.json')==qualification['source_recipe_sha256']
        assert sha(folder/'build-manifest.json')==qualification['test_build_manifest_sha256']
        for key,name in [('skid_coverage_sha256','skid-coverage.json'),('pingpong_coverage_sha256','pingpong-coverage.json'),('cycle_equivalence_sha256','cycle-equivalence.json')]:
            assert sha(folder/name)==qualification[key],name
        equivalence=read(folder/'cycle-equivalence.json')
        assert len(equivalence['results'])==14 and equivalence['candidate_cpu_sha256']==profile['netlist_sha256']
        for item in equivalence['results']:
            assert sha(folder/item['log'])==item['sha256']
            for reference,equal in item['byte_identical_to'].items():assert equal and sha(OUT/reference/item['log'])==item['sha256']
        emitted_path=BASE/audit_name/'manifest.json';emitted=read(emitted_path)
        assert sha(emitted_path)==qualification['independent_audit_sha256']
        assert emitted['source_sha256'][source_key]==profile['netlist_sha256']
        if source_key=='dual':
            assert manifest['no_new_state_or_cycles'] and manifest['source_orders_and_original_external_patch_orders_identical']
            assert emitted['both_dual_orders_match'] and emitted['no_other_source_edits']
        elif source_key=='combined':
            assert manifest['new_register_bits']==manifest['new_cycles']==0
            assert manifest['integer_actual_cone_cases']==262144 and manifest['fp_actual_cone_cases']==1048576
            for key in ['both_application_orders_exact','both_reversals_restore_parents',
                        'all_composed_equations_identical_to_independently_audited_components',
                        'no_other_source_changes','all_sequential_blocks_byte_identical']:
                assert emitted[key],key
        else:
            assert manifest['new_register_bits']==manifest['new_cycles']==0
            assert manifest['exhaustive_cases']==dict(integer=262144,rs2=1048576,rs3=1048576)
            for key in ['exact_rs3_reuse_from_independently_proved_186a','only_rs3_data_block_added_to_fb18',
                        'all_six_orders_and_reversals_exact','no_other_source_changes','all_sequential_blocks_byte_identical',
                        'existing_integer_output_keep_and_all_live_gates_preserved']:
                assert emitted[key],key
        files=[directory/n for n in ['manifest.json','qualification.json','recipe.json',script,'lint.log']]+[emitted_path,replay]
        files += [directory/name for name in recipe[patch_key]]
        for name,digest in qualification['component_audits'].items():
            path=BASE/name/'manifest.json';assert sha(path)==digest;files.append(path)
        coverage=read(folder/'pingpong-coverage.json');assert coverage['passed'] and coverage['totals']==pingpong['pingpong_coverage']
        assert coverage['candidate_cpu_sha256']==profile['netlist_sha256']
        profile['proof_evidence']={relative(p):sha(p) for p in files}
        profile['independent_emitted_audit_verified']=True
        profile['all_fourteen_logs_also_identical_to']=equivalence['additional_identical_references']
        profile['pingpong_coverage']=coverage['totals']
    direct=report['profiles'].get('rvc_skid_immediate_pingpong_second_usable')
    if direct:
        directory=BASE/'second-usable-direct-v1/immediate';folder=OUT/'rvc_skid_immediate_pingpong_second_usable'
        manifest=read(directory/'manifest.json');recipe=read(directory/'recipe.json');qualification=read(directory/'qualification.json')
        assert qualification['passed'] and qualification['candidate_cpu_sha256']==manifest['candidate_sha256']==recipe['candidate_sha256']==direct['netlist_sha256']
        assert manifest['reference_sha256']==report['profiles']['rvc_skid_immediate_pingpong_all_operand_groups']['netlist_sha256']
        assert manifest['exhaustive_cases']==65536 and manifest['observed_bits']==31
        assert manifest['extra_registers']==manifest['extra_cycles']==0
        for key in ['arbitrary_valid_and_required_patterns','no_contiguity_reachability_or_valid_control_assumption',
                    'original_shared_scanner_to_first_usable_cone_included','independent_first_and_second_priority_oracle',
                    'no_selection_last_true_preserved','all_sequential_source_unchanged',
                    'current_lastFromBuffer_and_trap_predicates_unchanged','exact_reverse_replay','full_netlist_lint']:
            assert manifest[key],key
        assert sha(directory/'factor_second_usable.py')==recipe['script_sha256']
        assert sha(directory/'check_vexii_os20_timing.py')==recipe['cone_extractor_sha256']
        for name,digest in recipe['evidence'].items():assert sha(directory/name)==digest,name
        for path in [directory/'patch-replay/VexiiRiscv.v',BASE/'second-usable-direct-replay-v1/immediate/VexiiRiscv.v']:
            assert sha(path)==qualification['clean_source_reproduction_sha256']==direct['netlist_sha256']
        assert sha(directory/'recipe.json')==qualification['source_recipe_sha256']
        assert sha(folder/'build-manifest.json')==qualification['test_build_manifest_sha256']
        for key,name in [('skid_coverage_sha256','skid-coverage.json'),('pingpong_coverage_sha256','pingpong-coverage.json'),('cycle_equivalence_sha256','cycle-equivalence.json')]:
            assert sha(folder/name)==qualification[key],name
        equivalence=read(folder/'cycle-equivalence.json');assert len(equivalence['results'])==14
        for item in equivalence['results']:
            assert sha(folder/item['log'])==item['sha256']
            for reference,equal in item['byte_identical_to'].items():assert equal and sha(OUT/reference/item['log'])==item['sha256']
        audit_path=BASE/'second-usable-emitted-audit-v1/manifest.json';audit=read(audit_path)
        assert sha(audit_path)==qualification['independent_audit_sha256'] and audit['candidate_sha256']==direct['netlist_sha256']
        assert audit['independent_mask_math_cases']==65536 and audit['empty_second_cases']==3200 and audit['sparse_mask_cases']==56064
        for name,digest in audit['inputs'].items():assert sha(ROOT/name)==digest,name
        assert audit['fresh_cones_and_wrapper_match'] and audit['actual_cone_binary_rerun'] and audit['unchanged_state_and_other_consumers']
        math_path=BASE/'frontend-second-usable-analysis-v1/manifest.json';math=read(math_path)
        for name,digest in math['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert math['cases']==65536 and math['rtl_files_written']==0 and math['runtime_suites_run']==0
        coverage=read(folder/'pingpong-coverage.json');assert coverage['passed'] and coverage['totals']==pingpong['pingpong_coverage']
        direct['pingpong_coverage']=coverage['totals']
        direct['all_fourteen_logs_also_identical_to']=equivalence['additional_identical_references']
        direct['independent_emitted_audit_verified']=True
        files=[directory/n for n in ['manifest.json','qualification.json','recipe.json','factor_second_usable.py','lint.log']]+[audit_path,math_path]
        files += [directory/name for name in recipe['evidence']]
        direct['proof_evidence']={relative(p):sha(p) for p in files}
    for label,source_dir,script in [
        ('rvc_skid_immediate_pingpong_integer_rs1_reuse','integer-rs1-reuse-v1/immediate','reuse_integer_rs1.py'),
        ('rvc_skid_immediate_pingpong_integer_rs1_sample_lane1','integer-rs1-sample-lane1-v2/immediate','sample_integer_rs1_lane1.py'),
        ('rvc_skid_immediate_pingpong_lane1_rdid','lane1-rdid-constant-v1/immediate','specialize_lane1_rdid.py'),
        ('rvc_skid_immediate_pingpong_shared_rs2_qualified','shared-rs2-qualified-v1/immediate','qualify_shared_rs2.py'),
        ('rvc_skid_immediate_pingpong_headvalid','decode-head-valid-direct-v3/immediate','simplify_head_valid.py')]:
        folder=OUT/label;directory=BASE/source_dir
        if label not in report['profiles'] or not (directory/'qualification.json').exists():continue
        profile=report['profiles'][label];q=read(directory/'qualification.json');recipe=read(directory/'recipe.json')
        assert q['passed'] and q['candidate_cpu_sha256']==profile['netlist_sha256']==recipe['candidate_sha256']
        assert sha(directory/'recipe.json')==q['source_recipe_sha256']
        assert sha(directory/'manifest.json')==q['source_proof_manifest_sha256']
        assert sha(folder/'qualification.json')==sha(directory/'qualification.json')
        for key,name in [('test_build_manifest_sha256','build-manifest.json'),('driver_manifest_sha256','driver-manifest.json'),
                         ('skid_coverage_sha256','skid-coverage.json'),('pingpong_coverage_sha256','pingpong-coverage.json'),
                         ('cycle_equivalence_sha256','cycle-equivalence.json')]:
            assert sha(folder/name)==q[key],name
        driver=read(folder/'driver-manifest.json');assert sha(Path(driver['driver']))==driver['driver_sha256']
        audit_path=ROOT/q['independent_audit'];assert sha(audit_path)==q['independent_audit_sha256']
        audit=read(audit_path);assert audit['candidate_sha256']==profile['netlist_sha256']
        for name,digest in recipe.get('evidence',{}).items():assert sha(directory/name)==digest,name
        scripts=[p for p in directory.glob('*.py') if sha(p)==recipe['script_sha256']];assert len(scripts)==1
        equivalence=read(folder/'cycle-equivalence.json');assert len(equivalence['results'])==14
        for item in equivalence['results']:
            assert sha(folder/item['log'])==item['sha256']
            for reference,equal in item['byte_identical_to'].items():assert equal and sha(OUT/reference/item['log'])==item['sha256']
        coverage=read(folder/'pingpong-coverage.json');assert coverage['passed'] and coverage['totals']==pingpong['pingpong_coverage']
        profile['pingpong_coverage']=coverage['totals'];profile['independent_emitted_audit_verified']=True
        profile['all_fourteen_logs_also_identical_to']=q['fourteen_cases_byte_identical_to']
        files=[directory/n for n in ['manifest.json','recipe.json','qualification.json']]+scripts+[audit_path,folder/'driver-manifest.json']
        files += [directory/name for name in recipe.get('evidence',{})]
        if label.endswith('_headvalid'):
            source=read(directory/'manifest.json')
            assert sha(directory/'head-valid-direct.patch')==source['patch_sha256']
            assert sha(BASE/'decode-head-valid-direct-replay-v3/immediate/VexiiRiscv.v')==q['clean_source_reproduction_sha256']==profile['netlist_sha256']
            for key in ['passed','exact_only_constant_default_before_existing_capture','reset_and_sole_writer_scope_verified',
                        'actual_ready_cone_and_aliases_bound','actual_131072_pattern_binary_rerun_pass',
                        'all_other_register_control_payload_masks_and_flush_equations_identical','no_extra_registers_or_cycles']:
                assert audit[key],key
            assert not audit['unrestricted_unknown_head_equivalence']
            profile['proof_state_scope']=audit['qualification_scope']
            files += [directory/name for name in ['actual-ready.v','reference-state.v','candidate-state.v','proof.sv',
                'proof.log','four-state-scope.json','head-valid-direct.patch','reverse.log','replay.log','lint.log']]
        profile['proof_evidence']={relative(p):sha(p) for p in files}
        if label.endswith('_lane1_rdid'):
            for key in ['passed','all_decoder_input_words_covered','decode_live_mask_cannot_assert','decode_payload_and_masks_same_advance',
                        'reset_before_first_advance_qualified','shared_execute_advance_and_cancellation_verified','complete_consumer_census','invalid_match_difference_unobservable']:
                assert audit[key],key
            assert sha(BASE/'lane1-rdid-constant-replay-v1/immediate/VexiiRiscv.v')==q['clean_source_reproduction_sha256']==profile['netlist_sha256']
    second=report['profiles'].get('rvc_skid_immediate_second_decode')
    if second:
        folder=OUT/'rvc_skid_immediate_second_decode';directory=BASE/'decode-second-skid-draft-v1/immediate'
        q=read(directory/'qualification.json');runtime=read(folder/'runtime-qualification.json')
        assert q['passed'] and runtime['passed'] and q['candidate_sha256']==runtime['candidate_sha256']==second['netlist_sha256']
        assert q['retained_program_modes']==runtime['retained_program_modes']==14 and not q['cycle_identity_to_original_pipeline']
        files=[directory/'qualification.json',folder/'runtime-qualification.json']
        for evidence in q['evidence'].values():
            path=ROOT/evidence['path'];assert sha(path)==evidence['sha256'];files.append(path)
        assert sha(directory/'runtime-qualification.json')==sha(folder/'runtime-qualification.json')
        for key,name in [('build_manifest_sha256','build-manifest.json'),('driver_manifest_sha256','driver-manifest.json'),
                         ('monitor_map_sha256','skid-observations.json'),('decoded_skid_coverage_sha256','decoded-skid-coverage.json'),
                         ('first_skid_coverage_sha256','skid-coverage.json'),('first_pingpong_coverage_sha256','pingpong-coverage.json'),
                         ('performance_comparison_sha256','performance-comparison.json')]:
            assert sha(folder/name)==runtime[key],name;files.append(folder/name)
        driver=read(folder/'driver-manifest.json')
        assert sha(HERE/'second_decode_runtime.py')==driver['driver_sha256']
        assert sha(HERE/'second_decode_monitor.py')==runtime['added_monitor_sha256']==driver['added_monitor_sha256']
        for name,digest in runtime['independent_and_actual_source_evidence'].items():assert sha(BASE/name/'manifest.json')==digest,name
        for item in runtime['retained_logs']:assert sha(folder/f'{item["program"]}-{item["mode"]}.log')==item['log_sha256']
        performance=read(folder/'performance-comparison.json');assert performance['all_fourteen_architectural_results_equal']
        assert len(performance['results'])==14 and all(x['architectural_result_equal'] for x in performance['results'])
        coverage=read(folder/'decoded-skid-coverage.json');assert coverage['passed'] and coverage['totals']['errors']==0
        assert coverage['totals']['cycles']==33373292
        actual_dir=BASE/'decode-second-skid-actual-proof-v1';actual=read(actual_dir/'manifest.json')
        for key,name in [('proof_sha256','actual.v'),('result_sha256','proof.log'),('script_sha256','check_second_decode_actual.py')]:
            assert sha(actual_dir/name)==actual[key],name;files.append(actual_dir/name)
        audit=read(BASE/'decode-second-skid-actual-emitted-audit-v1/manifest.json')
        assert audit['passed'] and audit['proof_rerun_pass'] and audit['candidate_sha256']==second['netlist_sha256']
        assert audit['actual_source_runtime_cycles']==1340480 and audit['off_clock_reset_events']==146892
        second['decoded_skid_coverage']=coverage['totals'];second['performance_comparison']=performance
        second['architectural_results_equal_to']=runtime['all_architectural_results_equal_to']
        second['cycle_identity_to_reference']=False
        second['proof_evidence']={relative(p):sha(p) for p in files+[HERE/'second_decode_runtime.py',HERE/'second_decode_monitor.py']}
    report['focused_only_candidates']={}
    pointer_path=BASE/'pointer-d-emitted-audit-v1/manifest.json'
    if pointer_path.exists():
        pointer=read(pointer_path)
        for name,digest in pointer['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        for key in ['exact_only_original_pointer_attribute_and_next_state','one_existing_pointer_register',
                    'same_async_reset_sensitivity_polarity_value','same_toggle_predicate',
                    'all_other_source_bytes_and_consumers_identical','actual_proof_wrappers_bound_to_emitted',
                    'off_clock_reset_assertion_and_release_checked','actual_compiled_proof_rerun_passed',
                    'independent_zero_fuzz_forward_reverse_replay']:
            assert pointer[key],key
        assert pointer['exhaustive_binary_transition_cases']==16
        report['focused_only_candidates']['pointer_unconditional_d']=dict(audit=pointer,audit_sha256=sha(pointer_path))
    rs3_dir=BASE/'fp-rs3-priority-groups-v1/immediate'
    if (rs3_dir/'manifest.json').exists():
        rs3=read(rs3_dir/'manifest.json');recipe=read(rs3_dir/'recipe.json')
        assert sha(rs3_dir/'VexiiRiscv.v')==rs3['candidate_sha256']==recipe['candidate_sha256']
        assert sha(ROOT/rs3['reference'])==rs3['reference_sha256']==recipe['reference_sha256']
        for key,name in [('script_sha256','factor_fp_rs3.py'),('patch_sha256','fp-rs3-priority-groups.patch'),
                         ('reference_cone_sha256','reference.v'),('candidate_cone_sha256','candidate.v'),
                         ('proof_sha256','proof.v'),('harness_sha256','proof.cpp'),('checker_sha256','check_vexii_os20_timing.py')]:
            assert sha(rs3_dir/name)==recipe[key],name
        assert rs3['actual_cone_cases']==1048576 and rs3['new_register_bits']==rs3['new_cycles']==0
        assert sha(rs3_dir/'patch-replay/VexiiRiscv.v')==rs3['candidate_sha256']
        emitted_path=BASE/'fp-rs3-priority-emitted-audit-v1/manifest.json';emitted=read(emitted_path)
        assert emitted['candidate_sha256']==rs3['candidate_sha256'] and emitted['actual_proof_rerun_cases']==1048576
        report['focused_only_candidates']['fp_rs3_groups']=dict(manifest=rs3,recipe_sha256=sha(rs3_dir/'recipe.json'),proof_log_sha256=sha(rs3_dir/'proof.log'),independent_emitted_audit_sha256=sha(emitted_path))
    for name,script,patch in [('fp-rs2-priority-groups-v1','factor_fp_rs2.py','fp-rs2-priority-groups.patch'),
                             ('fp-rs2-priority-2plus8-v1','factor_fp_rs2_2plus8.py','fp-rs2-priority-2plus8.patch')]:
        directory=BASE/name/'immediate'
        if not (directory/'manifest.json').exists():continue
        item=read(directory/'manifest.json');recipe=read(directory/'recipe.json')
        assert sha(directory/'VexiiRiscv.v')==item['candidate_sha256']==recipe['candidate_sha256']
        assert sha(ROOT/item['reference'])==item['reference_sha256']==recipe['reference_sha256']
        for key,filename in [('script_sha256',script),('patch_sha256',patch),('reference_cone_sha256','reference.v'),
                             ('candidate_cone_sha256','candidate.v'),('proof_sha256','proof.v'),
                             ('harness_sha256','proof.cpp'),('checker_sha256','check_vexii_os20_timing.py')]:
            assert sha(directory/filename)==recipe[key],filename
        assert item['actual_cone_cases']==1048576 and item['new_register_bits']==item['new_cycles']==0
        assert sha(directory/'patch-replay/VexiiRiscv.v')==item['candidate_sha256']
        report['focused_only_candidates'][name]=dict(manifest=item,recipe_sha256=sha(directory/'recipe.json'),proof_log_sha256=sha(directory/'proof.log'))
        if name=='fp-rs2-priority-2plus8-v1':
            audit=BASE/'fp-rs2-priority-2plus8-emitted-audit-v1/manifest.json';data=read(audit)
            assert data['candidate_sha256']==item['candidate_sha256'] and data['actual_proof_rerun_cases']==1048576
            report['focused_only_candidates'][name]['independent_emitted_audit_sha256']=sha(audit)
    directory=BASE/'integer-rs1-priority-groups-v2/immediate'
    if (directory/'manifest.json').exists():
        item=read(directory/'manifest.json');recipe=read(directory/'recipe.json')
        assert sha(directory/'VexiiRiscv.v')==item['candidate_sha256']==recipe['candidate_sha256']
        assert sha(ROOT/item['reference'])==item['reference_sha256']==recipe['reference_sha256']
        for key,name in [('script_sha256','factor_integer_rs1_pair.py'),('patch_sha256','integer-rs1-priority-groups.patch'),
                         ('checker_sha256','check_vexii_os20_timing.py'),('proof_sha256','proof.v'),('harness_sha256','proof.cpp')]:
            assert sha(directory/name)==recipe[key],name
        for name,digest in recipe['actual_cone_sha256'].items():assert sha(directory/name)==digest,name
        audit=BASE/'integer-rs1-priority-emitted-audit-v2/manifest.json';data=read(audit)
        assert data['candidate_sha256']==item['candidate_sha256'] and data['actual_proof_rerun_cases']==262144
        assert item['new_register_bits']==item['new_cycles']==0
        report['focused_only_candidates']['integer_rs1_groups']=dict(manifest=item,recipe_sha256=sha(directory/'recipe.json'),independent_emitted_audit_sha256=sha(audit))
    tested={x['netlist_sha256'] for x in report['profiles'].values()}
    shared={x['netlist_sha256'] for x in report['shared_simulation_derivatives'].values()}
    candidates=[]
    for directory in BASE.glob('generator-v*'):candidates+=list(directory.glob('VexiiRiscv*.v'))
    for pattern in ['timing-rescue-v*','raw-fp-v*','fpu-match-v*','tag-select-v*','integer-rs2-v*','decode-ready-v*','fp-preselect-v*','src1-balance-v*','match-preselect-all-v*','load-byte-factor-v*','raw-normalize-v*','inflight-hold-v*','raw-hold-load-v*','decode-pingpong-v*','lsu-hit-sampled-v*','aligner-trap-direct-v*','aligner-trap-no-sampled-hit-v*','fp-last-combined-v*','aligner-last-direct-v*','fp-cmp-abs-factor-v*','pointer-unconditional-d-v*','fp-rs3-priority-groups-v*','packer-round-mode-v*','pointer-packer-combined-v*','pointer-packer-rs3-combined-v*','fp-rs2-priority-groups-v*','fp-rs2-priority-2plus8-v*','integer-rs1-priority-groups-v*','integer-rs1-fp-rs2-combined-v*','all-operand-groups-v*','second-usable-direct-v*']:
        for directory in BASE.glob(pattern):candidates+=list(directory.glob('*/VexiiRiscv.v'))
    for path in sorted(candidates):
        digest=sha(path)
        if digest not in tested|shared:report['unexecuted_netlists'].append(dict(path=relative(path),sha256=digest))
    tiny=report['profiles'].get('rvc_skid_address_fpu_tag_rs2')
    if tiny:
        directory=BASE/'postprocess-qualified-v2-address'
        recipe=read(directory/'recipe.json')
        for name,digest in recipe['file_sha256'].items():assert sha(directory/name)==digest,name
        replay=read(BASE/'postprocess-qualified-v2-address-replay/reproduction.json')
        assert replay['recipe_sha256']==sha(directory/'recipe.json')
        assert replay['final_sha256']==tiny['netlist_sha256']
        files=[BASE/'generator-v10'/name for name in ['source-manifest.json','manifest.json',
            'structure.json','reproducibility-audit.json','address-only.patch']]
        files += [directory/'recipe.json',BASE/'postprocess-qualified-v2-address-replay/reproduction.json',
                  BASE/'integer-rs2-v4/address-skid-fpu-tag/manifest.json']
        tiny['reproduction_evidence']={relative(p):sha(p) for p in files}
        tiny['timing_transform_replay_matches_tested_rtl']=True
    report['gpu_derivatives']={}
    posted=BASE/'posted-write-factor-v1';pq=read(posted/'qualification.json')
    assert pq['status']=='passed' and pq['exhaustive_control_cases']==262144
    for name,digest in pq['files_sha256'].items():assert sha(posted/name)==digest,name
    assert sha(posted/'replay/cpu_system.v')==pq['candidate_sha256']==pq['exact_replay_sha256']
    for label,item in pq['paired_runtime'].items():
        assert sha(posted/(label+'.log'))==item['log_sha256']
        assert sha(posted/(label+'-obj')/'Vlsu_axi_shim')==item['binary_sha256']
        assert item['totals']['errors']==0
    pa=read(ROOT/pq['independent_emitted_audit'])
    assert pa['candidate_sha256']==pq['candidate_sha256'] and pa['reference_sha256']==pq['reference_sha256']
    assert pa['sequential_source_and_fifo_accounting_unchanged'] and pa['accepted_over_held_over_live_priority_exact']
    report['posted_write_component']=dict(qualification=pq,independent_audit_sha256=sha(ROOT/pq['independent_emitted_audit']))
    report['frozen_peripheral_integrations']={}
    for path in sorted((OUT/'peripheral-integration-v1').glob('fit-*.json')):
        integration=read(path);assert sha(HERE/'audit_peripheral_integration.py')==integration['audit_script_sha256']
        assert integration['cpu_evidence']['completed_fourteen']
        for item in integration['five_source_overrides'].values():
            assert sha(BASE/integration['fit']/'frozen'/item['path'])==item['sha256']
        assert sha(BASE/integration['fit']/'jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf')==integration['qsf_sha256']
        report['frozen_peripheral_integrations'][integration['fit']]=dict(audit=integration,audit_sha256=sha(path))
    for path in sorted((OUT/'trap-mmio-integration-v1').glob('fit-*.json')):
        integration=read(path);assert sha(HERE/'audit_trap_mmio_integration.py')==integration['audit_script_sha256']
        assert integration['cpu_evidence']['completed_fourteen']
        for item in integration['six_source_overrides'].values():
            assert sha(BASE/integration['fit']/'frozen'/item['path'])==item['sha256']
        assert sha(BASE/integration['fit']/'jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf')==integration['qsf_sha256']
        assert integration['mmio_attribute_only']['exact_attribute_reversal']
        assert integration['mmio_attribute_only']['extra_behavioral_tests']==0
        for name,digest in integration['mmio_attribute_only']['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        report['frozen_peripheral_integrations'][integration['fit']]=dict(audit=integration,audit_sha256=sha(path))
    for path in sorted((OUT/'fp-last-integration-v1').glob('fit-*.json')):
        integration=read(path);assert sha(HERE/'audit_fp_last_integration.py')==integration['audit_script_sha256']
        assert integration['cpu_evidence']['completed_fourteen'] and integration['cpu_evidence']['all_fourteen_complete_logs_verified']
        for item in integration['source_overrides'].values():
            assert sha(BASE/integration['fit']/'frozen'/item['path'])==item['sha256']
        assert sha(BASE/integration['fit']/'jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf')==integration['qsf_sha256']
        assert integration['gpu_qualification']['exact_selected_variant_macros']
        assert sha(OUT/'texture-offer-v1/qualification.json')==integration['gpu_qualification']['qualification_sha256']
        report['frozen_peripheral_integrations'][integration['fit']]=dict(audit=integration,audit_sha256=sha(path))
    paired_path=OUT/'fp-last-integration-v1/paired-integration.json'
    if paired_path.exists():
        pair=read(paired_path);assert pair['passed']
        for fit,digest in pair['audits_sha256'].items():assert sha(paired_path.parent/(fit+'.json'))==digest,fit
        report['current_fp_last_integration_pair']=dict(audit=pair,sha256=sha(paired_path))
    for path in sorted((OUT/'pointer-packer-integration-v1').glob('fit-*.json')):
        integration=read(path);assert sha(HERE/'audit_pointer_packer_integration.py')==integration['audit_script_sha256']
        for item in integration['source_overrides'].values():
            assert sha(BASE/integration['fit']/'frozen'/item['path'])==item['sha256']
        assert sha(BASE/integration['fit']/'jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf')==integration['qsf_sha256']
        assert integration['gpu_qualification']['exact_selected_variant_macros']
        assert sha(OUT/'texture-offer-v1/qualification.json')==integration['gpu_qualification']['qualification_sha256']
        expected=integration['source_overrides']['cpu']['sha256']==triple['netlist_sha256']
        assert integration['cpu_evidence']['completed_fourteen']==expected
        if not expected:assert integration['cpu_evidence']['qualification_gap'] and integration['cpu_evidence']['focused_proofs_and_exact_composition']
        report['frozen_peripheral_integrations'][integration['fit']]=dict(audit=integration,audit_sha256=sha(path))
    paired_path=OUT/'pointer-packer-integration-v1/paired-integration.json'
    if paired_path.exists():
        pair=read(paired_path)
        for fit,digest in pair['audit_sha256'].items():assert sha(paired_path.parent/(fit+'.json'))==digest,fit
        assert pair['unchanged_inputs']==103 and pair['job_qsf_identical']
        report['current_pointer_packer_integration_pair']=dict(audit=pair,sha256=sha(paired_path))
    for path in sorted((OUT/'pointer-packer-integration-v2').glob('fit-*.json')):
        integration=read(path);assert sha(HERE/'audit_forwarding_integration.py')==integration['audit_script_sha256']
        for item in integration['source_overrides'].values():
            assert sha(BASE/integration['fit']/'frozen'/item['path'])==item['sha256']
        assert sha(BASE/integration['fit']/'jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf')==integration['qsf_sha256']
        assert sha(OUT/'texture-offer-v1/qualification.json')==integration['gpu_qualification']['qualification_sha256']
        if integration['cpu_evidence']['completed_fourteen']:
            p=report['profiles'][integration['cpu_evidence']['label']]
            assert p['passed_runs']==14 and p['netlist_sha256']==integration['source_overrides']['cpu']['sha256']
        else:assert integration['cpu_evidence']['qualification_gap']
        report['frozen_peripheral_integrations'][integration['fit']]=dict(audit=integration,audit_sha256=sha(path))
    paired_path=OUT/'pointer-packer-integration-v2/paired-integration.json'
    if paired_path.exists():
        pair=read(paired_path)
        for fit,digest in pair['audit_sha256'].items():assert sha(paired_path.parent/(fit+'.json'))==digest,fit
        assert pair['pair_whole_cpu_cases']==pair['triple_whole_cpu_cases']==14
        report['current_pointer_packer_integration_pair']=dict(audit=pair,sha256=sha(paired_path))
    best=OUT/'pointer-packer-integration-v2/pair5209-qualified-fit.json'
    if best.exists():
        record=read(best)
        for name,digest in record['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert record['configured_clock_mhz']==100 and record['timing_estimate_mhz']==87.65
        assert record['target_timing_mhz']==90 and record['target_strictly_greater']
        report['pair5209_qualified_fit']=dict(evidence=record,sha256=sha(best))
    coordinate_fit=BASE/'coordinate-fit-integration-audit-v1/manifest.json'
    if coordinate_fit.exists():
        record=read(coordinate_fit);assert record['passed']
        for name,digest in record['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert record['configured_main_mhz']==100 and record['main_mhz']==88.95
        assert not record['above_90mhz_goal_met'] and record['all_104_frozen_and_job_sources_verified']
        report['coordinate_effort2_qualified_fit']=dict(evidence=record,sha256=sha(coordinate_fit))
    coordinate_default_fit=BASE/'coordinate-default-fit-integration-audit-v1/manifest.json'
    if coordinate_default_fit.exists():
        record=read(coordinate_default_fit);assert record['passed']
        for name,digest in record['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert record['configured_main_mhz']==100 and record['main_mhz']==90.04
        assert record['above_90mhz_goal_met'] and not record['full_100mhz_timing_closure']
        assert record['all_104_frozen_and_job_sources_verified'] and record['component_qualifications_bound']
        assert record['cpu_retained_cases']==14 and record['gpu_cases']==114
        assert record['all_corner_checks']['hold']==0.097
        report['coordinate_default_qualified_fit']=dict(evidence=record,sha256=sha(coordinate_default_fit))
    seed_audit=OUT/'seed3-integration-v1/fit-rvc-skid-pointer-packer-texoffer-seed3-v1.json'
    if seed_audit.exists():
        integration=read(seed_audit);assert sha(HERE/'audit_seed3_integration.py')==integration['audit_script_sha256']
        assert integration['seed']==3 and integration['saved_seed']==2
        assert integration['cpu_evidence']['completed_fourteen']
        for item in integration['source_overrides'].values():
            assert sha(BASE/integration['fit']/'frozen'/item['path'])==item['sha256']
        job=BASE/integration['fit']/integration['job_relative_path']
        assert sha(job/'src/fpga/targets/pocket/ap_core.qsf')==integration['qsf_sha256']
        comparison_path=seed_audit.parent/'comparison.json';comparison=read(comparison_path)
        for name,digest in comparison['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert comparison['identical_frozen_input_count']==104 and comparison['all_frozen_source_hashes_identical']
        assert not comparison['full_seed_sweep'] and comparison['extra_cpu_runtime_runs']==0
        report['frozen_peripheral_integrations'][integration['fit']]=dict(audit=integration,audit_sha256=sha(seed_audit))
        report['seed3_placement_comparison']=dict(audit=comparison,sha256=sha(comparison_path))
    fanout_path=OUT/'stage2-fanout-integration-v1/manifest.json'
    if fanout_path.exists():
        fanout=read(fanout_path);assert fanout['passed']
        for name,digest in fanout['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert fanout['frozen_input_count']==104 and fanout['unchanged_frozen_inputs']==103
        assert fanout['max_fanout']==32 and fanout['existing_cpu_cases']==14 and fanout['additional_cpu_runs']==0
        assert fanout['cpu_sha256']==report['profiles']['rvc_skid_immediate_pingpong_integer_rs1_fp_rs2']['netlist_sha256']
        report['stage2_fanout_integration']=dict(audit=fanout,sha256=sha(fanout_path))
    allrs_path=OUT/'all-operand-integration-v1/manifest.json'
    if allrs_path.exists():
        allrs=read(allrs_path);assert allrs['passed']
        for name,digest in allrs['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert allrs['frozen_input_count']==104 and allrs['unchanged_nonCPU_inputs']==103
        assert allrs['completed_CPU_cases']==14 and allrs['monitored_cycles']==33363543 and allrs['monitor_errors']==0
        assert allrs['cpu_sha256']==report['profiles']['rvc_skid_immediate_pingpong_all_operand_groups']['netlist_sha256']
        report['all_operand_integration']=dict(audit=allrs,sha256=sha(allrs_path))
    seed3_result=OUT/'seed3-integration-v1/completed-result.json'
    if seed3_result.exists():
        record=read(seed3_result)
        for name,digest in record['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert record['timing_estimate_mhz']==82.73 and record['selection']=='Rejected: slower timing than qualified seed2 pair87.65MHz.'
        report['seed3_completed_result']=dict(evidence=record,sha256=sha(seed3_result))
    second_path=OUT/'second-usable-integration-v1/manifest.json'
    if second_path.exists():
        second=read(second_path);assert second['passed']
        for name,digest in second['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert second['pair_identical_nonQSF_inputs']==103 and second['same_RTL_SDC_PLL_and_other_settings']
        assert second['cpu_runtime']['completed_cases']==14 and not second['cpu_runtime']['qualification_pending']
        assert second['cpu_runtime']['monitored_cycles']==33363543 and second['cpu_runtime']['monitor_errors']==0
        assert second['cpu_sha256']==direct['netlist_sha256']
        report['second_usable_integration']=dict(audit=second,sha256=sha(second_path))
    speed_path=OUT/'trap-mmio-integration-v1/cpu-speed-mapped-review.json'
    if speed_path.exists():
        speed=read(speed_path)
        for name,digest in speed['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        report['cpu_speed_mapped_review']=dict(evidence=speed,review_sha256=sha(speed_path))
    speed_complete=OUT/'trap-mmio-integration-v1/cpu-speed-completed-comparison.json'
    if speed_complete.exists():
        data=read(speed_complete)
        for name,item in data['artifact_comparison'].items():
            assert sha(BASE/data['fits'][0]/name)==item['area_sha256']
            assert sha(BASE/data['fits'][1]/name)==item['speed_sha256']
        assert data['same_final_rbf']
        report['cpu_speed_completed_comparison']=dict(evidence=data,sha256=sha(speed_complete))
    mmio_mapped=OUT/'trap-mmio-integration-v1/mmio-mapped-boundaries.json'
    if mmio_mapped.exists():
        data=read(mmio_mapped);assert data['passed']
        for item in data['fits']:
            directory=BASE/item['audit'];assert sha(directory/'mmio_boundaries.tsv')==item['table_sha256']
            assert sha(directory/'manifest.json')==item['manifest_sha256']
        report['mmio_mapped_boundaries']=dict(evidence=data,sha256=sha(mmio_mapped))
    texture_path=OUT/'texture-offer-v1/qualification.json'
    if texture_path.exists():
        texture=read(texture_path);assert texture['passed']
        for name,digest in texture['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        source=BASE/'texture-offer-v1';manifest=read(source/'manifest.json')
        assert manifest['gpu_sha256']==texture['gpu_sha256'] and manifest['cache_sha256']==texture['cache_sha256']
        for name,digest in manifest['files_sha256'].items():assert sha(source/name)==digest,name
        paired=OUT/'texture-offer-v1/cache-paired-v2';pq=read(paired/'qualification.json')
        for name,digest in pq['files_sha256'].items():assert sha(paired/name)==digest,name
        assert sha(paired/'obj/Vgpu_tex_cache')==pq['simulator_sha256'] and pq['counters']['errors']==0
        rendering=OUT/'texture-offer-v1/rendering';rm=read(rendering/'manifest.json');rr=read(rendering/'results.json')
        for name,digest in rm['sources'].items():assert sha(rendering/name)==digest,name
        assert sha(rendering/'runner-snapshot.py')==rm['script_sha256']
        assert sha(rendering/'candidate-gpu_core.v')==texture['gpu_sha256']
        assert sha(rendering/'candidate-gpu_tex_cache.v')==texture['cache_sha256']
        assert rm['texture_queues_bram'] and 'INCLUDE_TEX_QUEUE_MLAB' not in rm['variant']['defs'].split()
        for variant in ['baseline','candidate']:
            job=rendering/variant;jm=read(job/'manifest.json');assert sha(job/'obj/Vtb_gpu')==jm['simulator_sha256']
            for item in texture['whole_gpu_logs']:
                log=job/(item['mode']+'.log');assert sha(log)==item['sha256']==rr['runs'][variant][item['mode']]['log_sha256']
        audit=read(BASE/'texture-offer-emitted-audit-v1/manifest.json')
        assert audit['gpu_sha256']==texture['gpu_sha256'] and audit['cache_sha256']==texture['cache_sha256']
        assert audit['actual_main_fsm_byte_identical'] and audit['original_default_parameter_semantics_preserved']
        assert texture['texture_monitor_totals']['errors']==0
        report['texture_offer_component']=dict(qualification=texture,qualification_sha256=sha(texture_path))
    packed_path=OUT/'fbwq-payload-pack-v1/qualification.json'
    if packed_path.exists():
        packed=read(packed_path);assert packed['passed']
        for name,digest in packed['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        rendering=OUT/'fbwq-payload-pack-v1/rendering';pm=read(rendering/'manifest.json');pr=read(rendering/'results.json')
        assert packed['candidate_sha256']==pm['candidate_sha256']==sha(rendering/'candidate-gpu_core.v')
        assert packed['reference_sha256']==pm['baseline_sha256']==sha(rendering/'baseline-gpu_core.v')
        assert packed['texture_cache_sha256']==sha(rendering/'candidate-gpu_tex_cache.v')==sha(rendering/'common/gpu_tex_cache.v')
        assert packed['render_cache_sha256']==sha(rendering/'candidate-gpu_color_depth_cache.sv')==sha(rendering/'common/gpu_color_depth_cache.sv')
        for name,digest in pm['sources'].items():assert sha(rendering/name)==digest,name
        for variant in ['baseline','candidate']:
            job=rendering/variant;jm=read(job/'manifest.json');assert sha(job/'obj/Vtb_gpu')==jm['simulator_sha256']
            for item in packed['whole_gpu_logs']:
                assert sha(job/(item['mode']+'.log'))==item['sha256']==pr['runs'][variant][item['mode']]['log_sha256']
        assert packed['focused_proof_transitions']==819200 and packed['gpu_workload_cycle_pairs']==114
        report['gpu_payload_pack_component']=dict(qualification=packed,qualification_sha256=sha(packed_path))
    for component,folder in [('gpu_coordinate_stage_component','gpu-coordinate-stage-v1'),
                             ('gpu_anchor_stage_component','gpu-anchor-stage-v1')]:
        coordinate_path=OUT/folder/'qualification.json'
        if not coordinate_path.exists():continue
        coordinates=read(coordinate_path);assert coordinates['passed']
        for name,digest in coordinates['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
        assert sha(BASE/folder/'qualification.json')==sha(coordinate_path)
        rendering=OUT/folder/'rendering';cm=read(rendering/'manifest.json');cr=read(rendering/'results.json')
        assert coordinates['candidate_sha256']==cm['candidate_sha256']==sha(rendering/'candidate-gpu_core.v')
        assert coordinates['reference_sha256']==cm['baseline_sha256']==sha(rendering/'baseline-gpu_core.v')
        assert coordinates['texture_cache_sha256']==sha(rendering/'candidate-gpu_tex_cache.v')==sha(rendering/'common/gpu_tex_cache.v')
        assert coordinates['render_cache_sha256']==sha(rendering/'candidate-gpu_color_depth_cache.sv')==sha(rendering/'common/gpu_color_depth_cache.sv')
        for name,digest in cm['sources'].items():assert sha(rendering/name)==digest,name
        for variant in ['baseline','candidate']:
            job=rendering/variant;jm=read(job/'manifest.json');assert sha(job/'obj/Vtb_gpu')==jm['simulator_sha256']
            for item in coordinates['whole_gpu_logs']:
                assert sha(job/(item['mode']+'.log'))==item['sha256']==cr['runs'][variant][item['mode']]['log_sha256']
        assert coordinates['supporting_extracted_arithmetic_cases']==3145728 and coordinates['gpu_workload_cycle_pairs']==114
        assert coordinates['universal_operand_substitution_and_phase_closure']
        report[component]=dict(qualification=coordinates,qualification_sha256=sha(coordinate_path))
    port_path=BASE/'port-idle-count-v1/qualification.json'
    if port_path.exists():
        port=read(port_path);assert port['passed'] and port['clean_replay_source_identical']
        for name,digest in port['evidence'].items():assert sha(ROOT/name)==digest,name
        directory=port_path.parent;pm=read(directory/'manifest.json')
        pa=read(BASE/'port-idle-count-emitted-audit-v1/manifest.json')
        assert sha(directory/'cpu_target_port_per.v')==pm['candidate_sha256']==pa['candidate_sha256']
        assert sha(directory/'reference.v')==sha(Path(pm['reference']))==pm['reference_sha256']
        assert sha(directory/'port_all_bursts.cpp')==pm['harness_sha256']
        assert sha(ROOT/'src/fpga/test/tb_cpu_target_port_main.cpp')==pm['source_regression_sha256']
        assert sha(BASE/'port-idle-count-replay-v1/cpu_target_port_per.v')==pm['candidate_sha256']
        for name,digest in pm['runtime_logs'].items():
            assert sha(Path(name))==digest and Path(name).read_text().count('PASS peripheral')==3072,name
        assert (directory/'reference-runtime.log').read_bytes()==(directory/'candidate-runtime.log').read_bytes()
        for key in ['passed','exact_only_idle_count_clear_relocation','sole_observer_used_only_in_aw_and_w',
                    'all_exits_from_idle_clear_both_counts_before_use','reset_increment_length_and_all_interface_updates_unchanged',
                    'full_runtime_logs_identical','counter_may_differ_only_while_unobserved_idle','no_extra_registers_or_cycles']:
            assert pa[key],key
        assert pa['relational_cases_including_reset']==530432 and port['retained_bus_cases_per_source']==3072
        report['port_idle_count_component']=dict(qualification=port,qualification_sha256=sha(port_path),
            manifest=pm,independent_audit=pa,additional_evidence={relative(p):sha(p) for p in
                [directory/'reference.v',directory/'port_all_bursts.cpp',directory/'reference-runtime.log',directory/'candidate-runtime.log']})
    for label in ['gpu-fbwq-single-read-v2','gpu-fbwq-payload-reset','render-cache-hot-v1/rendering',
                  'gpu-fbwq-combined-v1/rendering','gpu-ring-data-v1/rendering','gpu-chain-final-v1/rendering']:
        folder=OUT/label; manifest=read(folder/'manifest.json');results=read(folder/'results.json')
        assert sha(folder/'runner-snapshot.py')==manifest['script_sha256'],label
        assert sha(Path(manifest['candidate_original']))==manifest['candidate_sha256'],label
        for name,digest in manifest['sources'].items():assert sha(folder/name)==digest,(label,name)
        for variant in ['baseline','candidate']:
            assert sha(folder/(variant+'-gpu_core.v'))==manifest[variant+'_sha256'],label
            build=read(folder/variant/'manifest.json')
            assert sha(folder/variant/'obj/Vtb_gpu')==build['simulator_sha256'],label
            for mode,row in results['runs'][variant].items():
                assert sha(folder/variant/(mode+'.log'))==row['log_sha256'],(label,mode)
        report['gpu_derivatives'][label]=dict(candidate=relative(manifest['candidate_original']),
            candidate_sha256=manifest['candidate_sha256'],baseline_sha256=manifest['baseline_sha256'],
            manifest_sha256=sha(folder/'manifest.json'),results_sha256=sha(folder/'results.json'),
            source_and_simulator_identities_verified=True,runs=results['runs'])
        if 'cache_candidate_sha256' in manifest:
            assert sha(Path(manifest['cache_candidate_original']))==manifest['cache_candidate_sha256']
            assert sha(folder/'candidate-gpu_color_depth_cache.sv')==manifest['cache_candidate_sha256']
            assert sha(folder/'common/gpu_color_depth_cache.sv')==manifest['cache_baseline_sha256']
            report['gpu_derivatives'][label]['cache_candidate']=relative(manifest['cache_candidate_original'])
            report['gpu_derivatives'][label]['cache_candidate_sha256']=manifest['cache_candidate_sha256']
            report['gpu_derivatives'][label]['cache_baseline_sha256']=manifest['cache_baseline_sha256']
        if manifest.get('ring_monitor'):
            for mode in ['quiet','delayed','gapped']:
                first=results['runs']['baseline'][mode]['ring_monitor']
                assert first==results['runs']['candidate'][mode]['ring_monitor']
                assert int(first['cpu'])>0
            report['gpu_derivatives'][label]['actual_ring_write_oracle_passed']=True
    folder=OUT/'gpu-ring-data-v1';ring_audit=read(folder/'independent-audit.json')
    ring_manifest=read(BASE/'gpu-ring-data-v1/manifest.json')
    for path,digest in ring_audit['proof_evidence'].items():assert sha(ROOT/path)==digest,path
    assert ring_audit['candidate_sha256']==ring_manifest['candidate_sha256']
    assert ring_audit['candidate_sha256']==report['gpu_derivatives']['gpu-ring-data-v1/rendering']['candidate_sha256']
    report['ring_data_source_audit']=dict(independent_audit_sha256=sha(folder/'independent-audit.json'),
        source_and_proof_identities_verified=True,source_reverse_exact=ring_audit['source_reverse_exact'],
        added_cycles=ring_manifest['added_cycles'],added_state_bits=ring_manifest['added_state_bits'],
        exhaustive_actual_control_cases=ring_manifest['exhaustive_control_states']*ring_manifest['data_bit_pairs']*ring_manifest['output_positions'],
        dma_runtime_coverage=False,rendering_evidence='gpu-ring-data-v1/rendering')
    folder=OUT/'gpu-chain-final-v1';finality=read(folder/'qualification.json');fa=read(folder/'independent-audit.json')
    assert finality['candidate_sha256']==report['gpu_derivatives']['gpu-chain-final-v1/rendering']['candidate_sha256']
    assert finality['independent_audit_sha256']==sha(folder/'independent-audit.json')
    assert fa['exact_single_predicate_reversal'] and fa['actual_harness_wire_statements_verified_against_both_sources']
    for mode in ['quiet','delayed','gapped']:
        assert (folder/'rendering/baseline'/(mode+'.log')).read_bytes()==(folder/'rendering/candidate'/(mode+'.log')).read_bytes()
    fm=read(BASE/'gpu-chain-final-v1/manifest.json')
    for name,digest in fm['source_files'].items():assert sha(BASE/'gpu-chain-final-v1'/name)==digest,name
    report['gpu_finality_source_audit']=dict(qualification=finality,proof_manifest_sha256=sha(BASE/'gpu-chain-final-v1/manifest.json'))
    folder=OUT/'gpu-fbwq-combined-v1';composition=read(folder/'composition.json')
    assert sha(folder/'gpu_core.v')==composition['candidate_sha256']
    assert sha(folder/'combine-snapshot.py')==composition['script_sha256']
    for label,item in composition['inputs'].items():
        assert sha(BASE/item['path'])==item['sha256']
        assert sha(folder/(label+'-gpu_core.v'))==item['sha256']
    report['combined_gpu_composition']=dict(composition_sha256=sha(folder/'composition.json'),
        candidate_sha256=composition['candidate_sha256'],source_and_recipe_identities_verified=True,
        both_composition_orders_byte_identical=composition['both_composition_orders_byte_identical'],
        rendering_evidence='gpu-fbwq-combined-v1/rendering')
    folder=OUT/'render-cache-hot-v1/scoreboard';manifest=read(folder/'manifest.json');results=read(folder/'results.json')
    assert sha(folder/'runner-snapshot.py')==manifest['script_sha256']
    assert sha(folder/'monitor-import-snapshot.py')==manifest['monitor_import_sha256']
    assert sha(folder/'main.cpp')==manifest['harness_sha256']
    assert sha(folder/'original-main.cpp')==manifest['original_harness_sha256']
    for label in ['baseline','candidate']:
        build=read(folder/label/'manifest.json')
        assert sha(folder/label/'original.sv')==manifest['source_sha256'][label]
        assert sha(folder/label/'observed.sv')==build['observed_source_sha256']
        assert sha(folder/label/'obj/Vgpu_color_depth_cache')==build['simulator_sha256']
        for row in results['runs'][label]:assert sha(folder/label/f'seed{row["seed"]}.log')==row['log_sha256']
    report['cache_scoreboard']=dict(manifest_sha256=sha(folder/'manifest.json'),results_sha256=sha(folder/'results.json'),
        sources=manifest['source_sha256'],runs=results['runs'],source_and_simulator_identities_verified=True)
    report['identities_verified']=True
    (OUT/'validation-inventory.json').write_text(json.dumps(report,indent=2)+'\n')
    write_report(report)
    print('Verified',len(report['profiles']),'simulator builds and current RTL identities; no simulations run.')

def write_report(data):
    profiles=data['profiles'];lines=[]
    def add(text=''):lines.extend(text.split('\n'))
    add('os30 private dual-issue CPU validation report (2026-10-04 experiment)')
    add('')
    add('Corrected skid CPUs, including the FPU/tag derivatives, pass all fourteen')
    add('whole-CPU fixture runs.')
    add('The full-ALU version preserves more triangle/branch throughput; the smaller')
    add('immediate-only version has a measured pairing cost. This report evaluates')
    add('CPU correctness and equal-clock component cycles. It establishes neither')
    add('FPGA timing closure nor Mario/Quake II FPS. This validation did not edit')
    add('production sources.')
    add('')
    add('Coverage matrix: each column is quiet/scanout runs, normally two passes.')
    add('S=stress, F=I2F, K=kernels, I=instruction capacity, R=RVC/IRQ redirects,')
    add('T=precise illegal traps, L=FP legality. -- means not executed; P/F are')
    add('passing/failing runs. Historical profiles did not all receive later tests.')
    add(f'{"Profile":30} {"S":4} {"F":4} {"K":4} {"I":4} {"R":4} {"T":4} {"L":4} Total')
    for label,profile in profiles.items():
        cells=[]
        for program in PROGRAMS:
            statuses=profile['coverage'][program]
            cells.append('--' if not statuses else f'{statuses.count("pass")}P' if 'fail' not in statuses else f'{statuses.count("fail")}F')
        add(f'{label:30} '+''.join(f'{x:5}' for x in cells)+f'{profile["passed_runs"]}P/{profile["failed_runs"]}F')
    add('')
    add('Fixture scope and independent checks')
    add('  S: 4,096 integer/FPU/atomic/ecall sequences, cache checks, and >=200')
    add('     asynchronous timer interrupts per mode.')
    add('  F: 30,000 signed/unsigned integer-to-float conversions, valid rounding')
    add('     modes, flags and dependency hazards; expected hash 71a4bfd3.')
    add('  K: seven kernels, twice each: independent ADDI, shifts/branches, subword')
    add('     RAW hazards, SM64 mtxf_mul_vec3s, captured triangle setup, Cache16/96.')
    add('  I: dependency-heavy indirect calls across 8KiB and 24KiB code regions;')
    add('     192 functions touch both cache lines in each 128-byte slot.')
    add('  R: 2,048 mixed RVC/32-bit calls at offsets 0/2/4/6/30/62; line straddles,')
    add('     wrong-path store/ebreak sentinels, FDiv stalls and timer IRQs.')
    add('  T: 48 exact illegal traps per mode; raw compressed/full mtval, mepc,')
    add('     cause2 and instruction-cache boundary cases.')
    add('  L: 264 cases/164 traps per mode; disabled FS, reserved static/dynamic')
    add('     rounding, FP CSR forms, ordinary/compressed FP loads/stores, exact')
    add('     legal results and no destination/memory changes for rejected ops.')
    add('')
    add('The bench executes actual CPU, AXI fabric, Pocket SDRAM controller and')
    add('timer RTL. Scanout requests 128 words every 700 cycles, phase317. Read,')
    add('write-delivery, ID and burst-boundary scoreboards must remain clean.')
    add('There is no active GPU drawing, audio traffic, or injected AXI fetch fault.')
    add('Identical binaries are reused across profiles. Optional rdcycle is absent;')
    add('firmware uses the existing MMIO cycle counter. D64 capacity runs need the')
    add('40M-cycle limit; their initial 20M timeout was not a correctness failure.')
    add('')
    add('Kernel limitations')
    add('The SM64 function is real game code, but its vertices are synthetic. The')
    add('triangle workload is private C setup over 782 captured projected triangles,')
    add('checking 31,100 packet words; it is not current production CPU geometry.')
    add('Reported cycles use the second pass, with cache state affected by earlier')
    add('kernels. These are component measurements, not full-game rendering gains.')
    add('')
    add('Quiet second-pass cycles (same binaries; lower is better)')
    add(f'{"Profile":30} {"SM64 vec":>9} {"Triangles":>10} {"Shift":>8} {"Subword":>9} {"Cache96":>10}')
    for label,profile in profiles.items():
        if 'kernels' not in profile:continue
        x=profile['kernels']['quiet']
        add(f'{label:30} {x["sm64_vectors"]:9} {x["triangle_scene"]:10} {x["shift_branch"]:8} {x["memory_raw"]:9} {x["cache96"]:10}')
    add('')
    add('Current candidates: throughput change against shipping single, quiet/scanout')
    for label in ['rvc_skid_masked_control','rvc_skid_immediate_control']:
        add('  '+label)
        for name in ['sm64_vectors','triangle_scene','shift_branch','memory_raw']:
            changes=[100*(profiles['baseline128']['kernels'][mode][name]/profiles[label]['kernels'][mode][name]-1) for mode in ['quiet','scanout']]
            add(f'    {name:16} {changes[0]:+8.3f}% / {changes[1]:+8.3f}%')
    add('At equal clock, immediate-only loses 6.70%/6.88% triangle throughput and')
    add('7.96% shift/branch throughput versus the corrected full-ALU skid. Vectors')
    add('are effectively unchanged. The full-ALU skid differs little from B: vector')
    add('-0.002%/-0.016%; triangle +0.297%/+0.307%. FPGA frequency is a separate gate.')
    add('')
    add('Capacity and branch tradeoffs')
    add('D64 deliberately thrashes on Cache96: roughly 5.2x baseline cycles quiet,')
    add('7.8x with scanout for lean dual. Matched single-D64 isolates the cache cut.')
    add('I16/D128 restores data capacity but the 24KiB code workload takes396,392')
    add('quiet/607,259 scanout cycles on lean I16 versus187,706 on lean I32. The')
    add('matched single-I16 control confirms the capacity tradeoff. Neither cut')
    add('proves safe for whole games. Full I32/D128 avoids these particular cliffs.')
    add('Dual issue still regresses dependency-heavy indirect calls: the 24KiB')
    add('second pass takes180,968 cycles on shipping single,193,987 on corrected')
    add('full-ALU skid and194,582 on immediate skid. Predictor miss rates for a')
    add('complete Mario or Quake II session have not been measured here.')
    add('')
    add('Rejected v7 skid and verified correction')
    add('v7 failed stress and IRQ-enabled frontend; frontend hash1235025e replaced')
    add('expected12349dcc while the wrong-path sentinel remained intact. Disabling')
    add('only asynchronous IRQ made the separately archived diagnostic pass on B')
    add('and v7. Basic buffer assertions passed despite the architectural failure.')
    add('Direct taps then observed a flushed buffered lane mask1/3 captured into')
    add('an invalid decode1 frame. v8 masks both lane selects on cancellation.')
    add('The new hard assertion requires zero valid and zero lane mask after a')
    add('flush+ready transfer. Both corrected CPUs pass the original IRQ fixtures.')
    add('The rejected logs and failure-report.json remain under rvc_skid_control;')
    add('rvc_skid_debug contains the first eight observed invalid-frame captures;')
    add('skid-debug-noirq preserves the separate diagnostic source/binary/logs.')
    add('')
    add('Observed skid corner coverage; all protocol/payload/cleanup assertions pass')
    for label in ['rvc_skid_masked_control','rvc_skid_immediate_control']:
        add('  '+label)
        for key,value in profiles[label]['skid_coverage'].items():add(f'    {key:28} {value:12,}')
    add('Assertions cover all278 payload bits on capture, blocked holding and valid')
    add('output selection; occupancy/drain/drop; upstream ready=!full; aligner')
    add('forgetOne=>cancel; and canceled decode1 frame/lane cleanup. Waiting input')
    add('during full-buffer flush is not accepted. Taps do not modify CPU RTL.')
    add('')
    add('Log equivalence already established')
    add('A matches lean-smallpred byte-for-byte on twelve original logs. B-control')
    add('matches B on those twelve logs. A-control-rawFP matches lean-smallpred on')
    add('all fourteen logs (twelve also match A). New skid elasticity may change')
    add('cycles, so its architectural results are compared without requiring exact')
    add('cycle equivalence. The relevant *cycle-equivalence.json files are retained.')
    add('The smallest lane1 candidate (ADDI/LUI/AUIPC only) also passes all fourteen')
    add('fixtures. It reduces pairing: using the sum of both cold/warm executions,')
    add('its vectors lose 1.07% quiet/1.06% scanout versus the immediate-only lane;')
    add('triangles lose 0.60%/0.69%. Shift/branch cycles are unchanged. This separate')
    add('two-pass method differs from the second-pass table above; see its saved')
    add('paired-performance.json. No game FPS or fitted-frequency claim follows.')
    add('Its source manifests, patch and frozen timing-transform recipe are linked')
    add('in validation-inventory.json. The saved exact replay ends at the same')
    add('SHA256 as the fourteen-test simulator input; recipe inputs are re-audited.')
    for label in ['rvc_skid_fpu_match','rvc_skid_fpu_tag','rvc_skid_fpu_tag_rs2',
                  'rvc_skid_immediate_fpu_tag','rvc_skid_immediate_fpu_tag_rs2','rvc_skid_immediate_ready',
                  'rvc_skid_immediate_ready_fp_preselect','rvc_skid_immediate_preselect_src1',
                  'rvc_skid_immediate_preselect_all','rvc_skid_immediate_raw_normalize',
                  'rvc_skid_immediate_raw_hold_load']:
        if label not in profiles:continue
        profile=profiles[label]
        reference_label='rvc_skid_immediate_control' if 'immediate' in label else 'rvc_skid_masked_control'
        matched=[]
        for item in profile['runs']:
            reference=OUT/reference_label/f'{item["program"]}-{item["mode"]}.log'
            if sha(reference)==item['log_sha256']:matched.append(item['program']+'/'+item['mode'])
        add(f'{label}: {len(matched)} of {len(profile["runs"])} complete logs byte-identical')
        add('to '+reference_label+', including observed skid corner counts.')
    add('The final decode-ready rewrite separately exhausts 262,144 combinations')
    add('of the actual extracted 32-block cone. Its fourteen whole-CPU runs match')
    add('the immediate all-fixes base exactly, including 33,363,543 monitored skid')
    add('cycles and zero errors. It introduces no register or pipeline cycle.')
    add('A separate synthesis-only derivative removes KEEP/syn_keep from one')
    add('existing lane0 RS2 combinational declaration. Reversing exactly that')
    add('declaration reproduces the tested ready-only source byte for byte. It')
    add('shares that simulation evidence; it is NOT another fourteen simulated')
    add('runs and does not appear in the directly simulated coverage matrix.')
    for label,item in data['shared_simulation_derivatives'].items():
        add('  '+label)
        add('    '+item['netlist'])
        add('    SHA256 '+item['netlist_sha256'])
    add('Synthesis attributes and physical fanout assignments still require their')
    add('own fitted timing/area assessment; behavioral identity does not prove it.')
    add('The eight-endpoint FP preselection candidate (SHA a85cdf36...) also passes')
    add('fourteen directly simulated runs, all logs byte-identical to ready-only.')
    add('It moves equality checks before operand selection without adding registers')
    add('or cycles. Its extracted proofs cover 2,097,152 dispatch states and 786,432')
    add('match cases per endpoint, including zero/one-hot selection; live validity,')
    add('write gating and match-register enables remain unchanged. The other nineteen')
    add('FP matches remain unchanged. Full-core timing/area is assessed separately.')
    add('The SRC1 OR candidate (SHA 6077cfd9...) passes fourteen directly simulated')
    add('runs with byte-identical preselection-reference logs and skid coverage.')
    add('Its nine-source OR preserves the original bypass priority and KEEP. The')
    add('extracted actual cone passes 8,388,608 comparisons: 512 enable patterns,')
    add('512 data-bit patterns and all 32 output positions. Bitwise independence')
    add('extends these cases to arbitrary simultaneous data words. The independent')
    add('src1-balance-emitted-audit-v1 verifies the emitted cones match the proof,')
    add('exact reverse-source equality and no added state/cycles. Frozen patch replay')
    add('ends at the exact tested source SHA; all proof/input hashes are re-audited.')
    add('The all-match preselection CPU (SHA 0b4b291a6d8d13d9e4b9fe1ba3eb58a2bf2e69f73308b8d936bcfee25e05e846)')
    add('passes fourteen further whole-CPU runs, each byte-identical to SRC1. It')
    add('preselects the remaining nineteen FP and eight integer RS2 equality checks;')
    add('the earlier eight FP changes stay unchanged. All 35 actual match equations')
    add('are checked against the exhaustive harness: 1,572,864 cases per endpoint,')
    add('with 2,097,152 actual dispatch states. The independent emitted audit binds')
    add('each endpoint input and preserves prior RD-enable and all live valid/write')
    add('gates and register enables. It adds 50 combinational comparison wires,')
    add('zero registers and zero cycles. Exact frozen patch replay reaches the')
    add('tested SHA. The fourteen runs cover 33,363,543 monitored skid cycles and')
    add('151,853 occupied-buffer flushes with zero errors. No new game FPS gain')
    add('follows from these cycle-neutral timing rewrites; a full fit remains separate.')
    add('The RAW normalization CPU06c79789 and combined RAW/held-trap/load CPU')
    add('1bce3882 each pass fourteen directly simulated runs, byte-identical to their')
    add('reference profiles. RAW changes only upper-half normalization, preserving')
    add('low16, compressed mtval, zero-selection, storage and cancellation. Its')
    add('independent emitted audit binds 32,768 selector masks and 150,994,944 data')
    add('comparisons. The combined candidate adds two disjoint cycle-neutral edits:')
    add('held-frame aggregate trap factoring, leaving actual skid flush/masks/storage')
    add('unchanged; and byte rearrangement before the late load way/IO selection.')
    add('The latter preserves simultaneous-hit OR, no-hit zero and IO priority, with')
    add('all downstream sign/SC/AMO/FPU consumers untouched. Its independently')
    add('audited actual cones exhaust all 32 controls and 96 data basis vectors;')
    add('zero/OR preservation extends equality to arbitrary simultaneous data words.')
    add('Both patch orders reach the tested combined SHA and reverse to the exact')
    add('RAW base. The hold/combined independent emitted audit verifies the actual')
    add('aggregate boundary and unchanged ordinary decode and skid state, without')
    add('an unproved sequential invariant. The standalone hold-only and byte-only SHAs have')
    add('component proofs but no separate fourteen-case CPU run; those runs belong')
    add('to the combined source. Full-core timing and physical qualification remain')
    add('separate from these functional and equal-cycle results.')
    add('The two-bank CPU d471c5da passes fourteen directly simulated cases. Removing')
    add('only its extra PINGPONG COVERAGE line reproduces every complete parent1bce')
    add('log byte-for-byte, including displayed cycles and original skid coverage.')
    add('It rotates the existing two276-bit payload banks, preserving original H/S')
    add('and four lane-mask FFs while adding one pointer FF and zero pipeline cycles.')
    add('The actual emitted transition proof covers114,688 inductive cases, including')
    add('57,344 reset cases. The independent source/refinement audit verifies all30')
    add('field mappings, unchanged metadata and exact head data after every nonreset')
    add('edge. The consumer audit guards all invalid-tail observers and reset-era')
    add('head/decoder/dispatch validity boundaries; details are in its REVIEW.txt.')
    add('The runtime bank monitor checks33,363,543 cycles,12,515,700 rotations and')
    add('206,686 simultaneous flush/pop cycles with zero errors. It checks the old')
    add('StageLink recurrence on all276 head bits even when invalid. Runtime reset')
    add('count is zero because monitoring starts after boot reset; reset coverage')
    add('belongs to the inductive storage proof. Timing/area qualification is separate.')
    add('CPU4b01fdaa adds one aggregate LSU-hit FF captured under the original hit')
    add('vector enable. All14 complete logs are byte-identical to two-bank CPUd471,')
    add('including both monitor lines and33,363,543 checked cycles. The original')
    add('two-bit hit vector, five consumers, valid gates and reset semantics remain')
    add('unchanged. Actual capture proof covers32 cases, with1,024 four-state')
    add('induction cases; emitted source and frozen replay are independently audited.')
    add('The direct aligner-trap CPU ec69d8b6 changes only two combinational ctx_trap')
    add('classifications before priority selection. Its fourteen complete logs match')
    add('aggregate-hit CPU4b01 byte for byte, including both monitors and33,363,543')
    add('checked cycles. The actual69-block cone proof covers393,216 independent-lane')
    add('cases, arbitrary masks/required bits and both trap bits; exact patch replay')
    add('and an independent emitted audit bind that proof to the tested source.')
    add('No register or cycle is added. Fetch-AXI faults are not deliberately injected')
    add('by the runtime suite; their trap-bit combinations belong to the cone proof.')
    add('CPU 1aaeb857 applies the same two trap equations directly to d471, removing')
    add('the optional sampled-hit shadow from the ec69 comparison. Both composition')
    add('orders and exact reversals are verified; all original hit-vector consumers')
    add('and sequential source match d471. Its own fourteen complete logs match d471,')
    add('4b01 and ec69 exactly, including 33,363,543 monitored cycles and zero errors.')
    add('This is a controlled placement comparison, with no performance-cycle change.')
    add('Combined CPU 9faabaee adds the direct lastFromBuffer equations and grouped')
    add('FP RS1 forwarding. Its fourteen complete logs match ec69, 4b01 and d471.')
    add('Both component orders/reversals and independent source audits pass, with')
    add('1,048,576 FP-cone and 393,216 aligner-cone cases. No FF or cycle is added.')
    add('Triple CPU a083bdb0 adds equivalent pointer D, mode-qualified packer rounding')
    add('and grouped FP RS3 data. Its own fourteen full logs match9fa, ec69 and d471,')
    add('including 33,363,543 monitored cycles and zero errors. All six patch orders,')
    add('reversals, fresh full-script replay and independent emitted audits pass.')
    add('The separate pair5209 now has its own fourteen full logs, byte-identical to')
    add('9fa and a083, with33,363,543 monitored cycles and zero errors. Its default')
    add('fit estimates87.65MHz with+0.098ns hold,18,084ALMs/1,847LABs/308M10Ks.')
    add('Clock remains configured100MHz:87.65 is a setup-derived timing estimate,')
    add('not a changed operating clock; the latest target is strictly above90MHz.')
    if 'coordinate_effort2_qualified_fit' in data:
        add('The earlier effort-2 fit combines CPU 5209 with GPU 8e471228 and')
        add('placement effort 2: 88.95 MHz, +0.047 ns minimum hold, 18,142 ALMs,')
        add('1,848 LABs and 308 M10Ks. Its 104 frozen inputs and component evidence')
        add('are bound by coordinate-fit-integration-audit-v1. Configured clock stays')
        add('100 MHz; that placement falls short of the above-90 MHz target.')
    if 'coordinate_default_qualified_fit' in data:
        add('The current qualified best uses the same CPU 5209 and GPU 8e471228 with')
        add('default placement effort and saved seed 2: 90.04 MHz warm / 90.11 MHz')
        add('cold. It meets the strictly above 90 MHz estimate target by 0.04 MHz.')
        add('All 104 inputs, seven overrides and component tests are bound by')
        add('coordinate-default-fit-integration-audit-v1. Resources are 18,142 ALMs,')
        add('1,848 LABs and 308 M10Ks; minimum hold is +0.097 ns. Other clocks pass.')
        add('The configured clock remains 100 MHz with -1.106 ns setup slack; this')
        add('is not 100 MHz timing closure, hardware qualification or game FPS evidence.')
    add('Combinedfb18 adds integerRS1 2+7 and FP RS2 2+8 data groups, preserving')
    add('all original selectors/live gates/FFs. Its fourteen entire logs match5209,')
    add('9fa and a083 with33,363,543 monitored cycles and zero errors. Actual integer')
    add('and FP cone proofs cover262,144 and1,048,576 cases, with independent audits,')
    add('both source/patch orders and clean reproduction verified. Standaloneb182')
    add('and6c356 proofs are not counted as separate CPU executions.')
    add('All-operand CPUffed adds the previously proved RS3 4+6 data group tofb18.')
    add('Its fourteen complete logs matchfb18,5209 and9fa, including33,363,543')
    add('monitored cycles with zero errors. Independent component/composition audits,')
    add('all six patch/reverse orders and clean full-script reproduction are bound.')
    add('Direct-mask CPU856b replaces one second-usable vector assignment, cutting')
    add('the first-instruction usage-mask dependency. Actual old/new cones match in')
    add('all65,536 independent scanner-valid/length cases across31 observed bits;')
    add('sparse/empty cases, original ownership predicates and all state are exact.')
    add('Its own14 entire logs matchffed,fb18 and5209, including33,363,543 monitored')
    add('cycles and zero errors. Clean replay and independent emitted audit are bound.')
    add('Four further pair-based profiles each pass their own fourteen fixtures:')
    add('34a06d1e reuses two existing FP register matches for integer RS1; 27ed0176')
    add('adds one sampled lane1 producer match; a78ced6d specializes the restricted')
    add('lane1 destination register type to integer. CPU0111fd5a folds aligned')
    add('producer validity into an existing shared RS2 match. Every complete log matches')
    add('pair5209, including 33,363,543 monitored cycles and zero errors per profile.')
    add('Their exact sources, separate drivers, independent audits and replay/proof')
    add('evidence are bound individually. The RFID specialization proves all 32-bit')
    add('decoder inputs by symbolic cube intersections, then qualifies traps, stalls,')
    add('cancellation, reset and every current live consumer. Unobserved invalid')
    add('metadata may differ. It changes no supported operation or pipeline cycle;')
    add('the one-FF match derivative has a separate qualification and is not combined')
    add('with it. Mapping-only head-mask candidates have no fourteen-fixture suite.')
    add('These functional results do not establish a new fitted frequency or FPS.')
    if 'rvc_skid_immediate_pingpong_headvalid' in profiles:
        add('CPU c4e35150 changes only the existing first-buffer head-valid default')
        add('to constant one before its original ready capture. The actual ready cone')
        add('proves equality from reset-established binary valid state; arbitrary')
        add('unknown-head equivalence is explicitly excluded. All other state/masks')
        add('remain exact. Its own fourteen logs match 5209 byte-for-byte, including')
        add('33,363,543 monitored cycles and zero errors. No register or cycle is added.')
    if 'rvc_skid_immediate_second_decode' in profiles:
        add('Second decoded-buffer CPUf703a709 passes its own fourteen fixtures with')
        add('identical architectural results, but its changed elastic capacity changes')
        add('cycle counts. All three storage monitors report zero errors over33,373,292')
        add('cycles. New-buffer coverage includes173,037 full flushes,102,813 blocked')
        add('flushes,48,628 held-trap cycles,62,216 younger flushes while held and')
        add('11,034,741 partial-issue holds. The independent actual-source storage')
        add('proof checks1,340,480 transitions including146,892 off-clock resets.')
        add('At equal clock, warm shift/branch uses1.161% more cycles (1.148% lower')
        add('throughput); vectors are effectively unchanged. Warm triangle cycles')
        add('change+0.068% quiet/-0.005% scanout. These are component measurements,')
        add('not game FPS. Its initial full fit requires1,849LABs against1,848available;')
        add('functional qualification does not establish physical capacity or timing.')
    add('The standalone ef386 CPU was compiled but ran zero fixtures; it is not')
    add('counted as an additional directly simulated profile or qualification.')
    add('The FPU match proof compares original forwarding-register equality under')
    add('stalls, preserving live valid/RD gates. The tag mux proof exhausts control')
    add('states with representative distinct data sources; initializer address/fault')
    add('differences are qualified by loaded=0. See tag-select-review.txt for the')
    add('independent review of hit/fault/writeback masking and its precise limits.')
    add('')
    add('Separately qualified GPU timing candidate')
    add('gpu-count-nonzero/gpu_core.v (SHA16059ac4...) preserves the original count')
    add('RAM and SELECT cycle, adding a four-bit write-time nonzero shadow and one')
    add('captured predicate. Exhaustive extracted sequential checks cover18,039,361')
    add('cycles; 38 actual retained os30 GPU workloads across three latency modes')
    add('have identical pixel checks and cycles versus the frozen production GPU.')
    add('This is separate GPU component evidence, not a combined CPU/game run.')
    add('See gpu-count-nonzero/REPORT.txt and rendering/results.json; FPGA timing')
    add('remains independently measured by the full-core fit.')
    add('Two separate countNZ-based GPU derivatives also pass all 38 retained')
    add('workloads/79 pixel checks in each of three frozen latency modes:')
    add('  Single-read AW queue: SHA4ab7ecda7d6ab8c751a89c629d978e406ba0fbdda380e23dd90004f6d8c27544')
    add('  Reset-only payload: SHA2df16811df7bf380434977a45fa44a433e68f11c111a28ca072c31488ffce4b6')
    add('Each source executes 42,548,619 monitored GPU cycles, 387 hard resets and')
    add('six soft resets. All 114 per-workload cycle comparisons match countNZ;')
    add('valid request/snoop payload and cycle-qualified AXI address/data hashes')
    add('match too. Reset invalidation assertions pass. Reset-only intentionally')
    add('retains stale invalid payload; these unqualified counters are excluded')
    add('from equality. These initial two comparisons tested each edit separately.')
    add('See gpu-fbwq-single-read-v2/REPORT.txt and gpu-fbwq-payload-reset/REPORT.txt')
    add('for exact frozen inputs, reset coverage, retained logs and proof limits.')
    add('A third combined comparison keeps the exact single-read GPU and changes')
    add('only the render-cache hot lookup comparators (cache SHA03e4d625...). It')
    add('also passes all 38 workloads/79 checks in each of three latency modes,')
    add('with identical cycles, qualified payload/AXI hashes and hot-hit counters.')
    add('Both hot slots and simultaneous AW/AR requests are exercised. Separate')
    add('actual-cache scoreboards use the os30 geometry and hostile RAM collision')
    add('data: three seeds, 2,437,948 cycles, six hard resets and 360 soft resets;')
    add('complete baseline/candidate logs match. IDLE hot-expression equality and')
    add('hot-valid reset assertions pass. See render-cache-hot-v1/REPORT.txt; this')
    add('is functional and cycle evidence, while full-core timing is measured apart.')
    add('The subsequent combined GPU (SHA56988348...) preserves both validated')
    add('edits: single queue read plus request-payload reset removal. Guarded source')
    add('composition reproduces both parents and commutes byte for byte. Compared')
    add('with the single-read GPU, using hot cache03e4d625 in both builds, it passes')
    add('all 38 retained workloads in three modes: 114 identical workload cycles,')
    add('237 passing pixel checks, matching qualified payload/AXI hashes and hot-hit')
    add('counts. Per source: 42,548,619 cycles, 387 hard resets and six soft resets.')
    add('Stale invalid payload remains intentionally unconstrained. See combined')
    add('source/patch/proof and rendering identities in gpu-fbwq-combined-v1/.')
    add('Functional qualification did not make the combined reset-removal GPU the')
    add('selected physical candidate: its ready-only fit had -0.089 ns hold slack,')
    add('and its FP-preselection fit reached only an estimated 85.23 MHz setup')
    add('limit. Subsequent SRC1/all-match work uses the single-read GPU4ab7ecda or')
    add('its ring DATA derivative, with hot cache03e4d625. Root fit reports remain')
    add('the authority for current timing and placement outcomes.')
    add('The subsequent ring RAM DATA mux candidate (SHA c1d606620c3161ad6d6c3f3a649ea5dcdf84ceec8d47044381122d0c5c6d3a13)')
    add('changes one RHS from GPU4ab; enable, address, reset and all other bytes')
    add('stay identical. Actual DMA/CPU control equations prove writer exclusivity')
    add('and enabled data equality over 16,384 cases, without a sequential or mode')
    add('assumption. It adds no register or cycle. Independent source binding and')
    add('the 38-workload x 3-mode actual GPU regression pass with the shared hot')
    add('cache: all 114 workload cycle pairs, 237 acceptance checks, qualified payload,')
    add('AXI and cache activity match. Actual completed ring RAM writes also match')
    add('the original selected-data oracle; all three modes exercise CPU upload')
    add('and pointer wrap. Os30 excludes command DMA, so no runtime DMA coverage')
    add('is claimed; its control cases are covered by the separate exhaustive proof.')
    add('See gpu-ring-data-v1/REPORT.txt and independent-audit.json. This is GPU')
    add('functional/cycle evidence; timing improvement requires an independent fit.')
    add('The subsequent queue-finality GPU5e628c9a changes only the head-chain-final')
    add('predicate. All33,554,432 pointer/count/link combinations, including invalid')
    add('counts, agree with both the original cone and an independent prefix scan.')
    add('Source/harness identity and exact one-predicate reversal are independently')
    add('audited. The retained38x3 GPU comparison with hotcache03e4 passes; all three')
    add('complete logs match ringGPUc1d60662 byte-for-byte, including114 workload')
    add('cycles,237 acceptance checks,42,548,619 monitored cycles,387 hard resets')
    add('and6 soft resets per source. It adds no state/cycle. See gpu-chain-final-v1/.')
    if 'gpu_payload_pack_component' in data:
        add('The packed framebuffer payload GPU e7a1b8aa replaces separate 32-bit data')
        add('and 4-bit strobe RAMs with one 36-bit memory; all 576 storage bits, three')
        add('read branches, two write branches, reset gates and priority remain exact.')
        add('The 819,200-transition extracted proof and independent audit establish')
        add('old-data same-address collision semantics without a no_rw_check waiver.')
        add('Against qualified early-offer GPU 2d4870c5, all 38x3 complete logs match:')
        add('114 workload cycle pairs, 237 pixel checks, 42,548,619 monitored cycles,')
        add('387 hard resets and 6 soft resets. Both sides use texture cache 0d2d3669,')
        add('render cache 03e4d625 and actual os30 BRAM queue macros. Runtime exercises')
        add('FIFO wrap, simultaneous read/write, all three read branches and partial')
        add('strobes. Collision/swap/reset-nonempty counters are zero; the exact proof')
        add('covers those controls. See fbwq-payload-pack-v1/qualification.json.')
        add('No CPU or game FPS test is implied; physical RAM mapping remains separate.')
    if 'gpu_coordinate_stage_component' in data:
        add('GPU8e471228 stages six sorted coordinates in the existing 100-bit DDS')
        add('bank during PROD_L0, then reads/overwrites it at DELTA. No new RTL FF')
        add('or cycle is added. All raw vertices, order, sorting, attributes and state')
        add('transitions are unchanged. Exact copied-operand substitution plus the')
        add('unchanged signed17-bit arithmetic establishes equality for all96 input')
        add('bits under the independently audited capture-before-read phase relation.')
        add('Supporting actual extracted checks cover3,145,728 cases,208,896 hold')
        add('edges and12,288 reset events; they are not exhaustive96-bit enumeration.')
        add('All38x3 rendering logs match qualified GPU2d4870c5 exactly, including')
        add('114 workload cycles,237 pixel checks and42,548,619 monitored cycles.')
        add('The new observer checks1,011 captures/deltas,6,054 DDS consumers,5,469')
        add('scratch holding cycles and414 normalization cycles. Runtime reset while')
        add('scratch is live is zero; arbitrary abort/reset safety belongs to the')
        add('source/phase proof. Both sides use texture0d2d/render03e4 and os30 BRAM')
        add('queues. No game FPS or fitted timing gain follows from these results.')
    if 'gpu_anchor_stage_component' in data:
        add('GPU ecb667f2 is the narrower anchor-only alternative. It stages only')
        add('dd_x0/dd_y0 in PROD_L0; all four edge-register writers and raw slot1/2')
        add('coordinate reads remain unchanged. It uses 32 existing bits, no new FF')
        add('or cycle. Exact operand substitution, complete DDS phase/consumer')
        add('closure, independent audit and fresh replay all pass. The supporting')
        add('arithmetic checks cover 3,145,728 cases, 208,896 holds and 12,288 resets.')
        add('All 38 workloads x 3 modes produce entire logs identical to GPU 2d4870c5:')
        add('114 cycle pairs, 237 pixel checks, 42,548,619 monitored cycles, 387 hard')
        add('and 6 soft resets. Its observer covers 1,011 anchor captures/deltas,')
        add('6,054 DDS consumers, 5,469 scratch cycles and 414 normalization cycles.')
        add('Reset during scratch is not exercised in the workload; reset/abort')
        add('safety is established separately by the phase proof and focused checks.')
        add('Both sides retain texture 0d2d/render 03e4 and actual os30 BRAM queues.')
        add('These tests establish component equality; area/timing remain fit results.')
    add('')
    if 'port_idle_count_component' in data:
        add('Peripheral port 51105011 moves its write-burst counter clear to local')
        add('IDLE state. Count differences remain unobserved in IDLE; every exit')
        add('clears before use. Reset, burst lengths, increments and interface cycles')
        add('are preserved. The independent audit covers 530,432 relational/reset')
        add('cases. Actual old/new modules pass 3,072 burst/stall cases each with')
        add('identical entire logs, including all 1–256 beat lengths. Exact source')
        add('replay is bound. This is component qualification, not a game speed claim.')
        add('')
    add('Separately qualified CPU-system posted-write factoring')
    add('cpu_system.v SHA2eb4017111babe0b40651535025847092444930adbfa98e007eea97b2e35c214')
    add('moves LOCAL qualification after run encoding and compares WLAST lengths')
    add('before selecting the accepted/held/live result. No state, cycle, queue')
    add('accounting, reset, grant or handshake changes. Actual cones agree over')
    add('262,144 unrestricted control combinations; exact reverse/replay and the')
    add('independent emitted audit bind the fitted reference2960fe6b. Side-by-side')
    add('actual shim instances compare all17 outputs every edge. Existing15 stall')
    add('schedules/3,600 writes and40 directed boundary scenarios pass:53,131 edges,')
    add('270 reset edges,40 simultaneous push/pop events,960 wraps,160 reads and')
    add('zero mismatches. Length4 is covered by exhaustive proof; runtime exercises')
    add('lengths1/2/3. This is component evidence; it is not another14-case CPU run.')
    add('See ../posted-write-factor-v1/{REPORT.txt,qualification.json}.')
    add('The two frozen peripheral fits are audited separately: each has104 exact')
    add('inputs and five explicit overrides (CPUd471 or4b01, GPU5e62, cache03e4,')
    add('audio7dd7, CPU-system2eb4). Only intended AREA/combo/RAM settings, seed2,')
    add('os30 macros and compile parallelism change; PLL/phase/SDC exceptions stay')
    add('byte-identical to production. Clock remains100MHz, full ordinary os30 GPU')
    add('features and8KiB render-cache geometry remain. Removing TEX_QUEUE_MLAB')
    add('changes only two ramstyle attributes under the actual os30 parameters.')
    add('Audio7dd7 is bound to its16,777,216-case exact ramp proof, identical14,223')
    add('PCM samples/timestamps and36 throughput cases, with independent source audit.')
    add('These bind existing component evidence to fitted sources; no combined game')
    add('or extra full-system simulation is claimed. See peripheral-integration-v1/.')
    add('The subsequent trap/MMIO builds bind CPUec69 and a sixth source: MMIO0af14f6d.')
    add('Its only KEEP attribute reverses exactly to production03269e46, with zero')
    add('additional behavioral runs. The CPU-SPEED comparison preserves global AREA;')
    add('the stored SPEED assignment resolves to the unique mapped CPU hierarchy.')
    add('Its map report does not expose the effective per-instance goal, and all')
    add('entity resource counts match the AREA control. Configuration/target validity')
    add('is established; an optimization change is not. See trap-mmio-integration-v1/.')
    add('A third frozen integration uses CPU 1aaeb857 with the same MMIO/peripheral')
    add('sources and ordinary AREA options; its own fourteen-case evidence is bound.')
    add('Completed CPU-SPEED and AREA fits produced identical 2,198,400-byte RBFs,')
    add('SHA c0790fd0..., plus byte-identical timing summaries and equal resources.')
    add('Thus SPEED produced no implementation benefit for these exact sources/seed.')
    add('The retained SYSREG boundary is separately visible as 28 mapped combout')
    add('names in the sampled-hit fit and 34 in the no-hit fit; sharing varies.')
    add('Current fp-last-mmio and fp-last-texoffer fits bind CPU9fa and six/seven')
    add('exact overrides. All 104 inputs are audited; only GPU and texture-cache')
    add('source files differ between the pair. Saved seed2, 100MHz clock, phases,')
    add('timing exceptions, AREA/combo/BRAM options and full os30 features match.')
    add('Both GPU sides bind to the actual 38x3 test with selected BRAM queue macros;')
    add('CPU9fa binds its own fourteen runs and component/composition source proofs.')
    add('See fp-last-integration-v1/; no extra combined execution is claimed.')
    add('The pointer/packer pair5209 and triplea083 fits each bind104 inputs and')
    add('seven overrides. Only their CPU differs; all103 other inputs and jobQSF')
    add('match, including unchanged100MHz clocks, seed2, constraints and features.')
    add('Both pair5209 and triplea083 now bind their own fourteen-case runtime suites.')
    add('Updated bindings are in pointer-packer-integration-v2; v1 preserves the')
    add('earlier checkpoint when only triple runtime qualification was available.')
    add('A single explicit seed3 comparison uses exact qualified pair5209 with all')
    add('104 frozen input hashes identical to seed2. Only the generated job SEED')
    add('line changes2->3; inventory records saved_seed2 and placement_seed3.')
    add('RTL, clock, constraints and options remain identical. This is one placement')
    add('comparison, not the deferred full seed sweep; no CPU fixtures were repeated.')
    add('Seed3 completed at82.73MHz estimated timing with+0.030ns minimum hold and')
    add('was rejected as slower than seed2. No further seed probing is planned.')
    add('See seed3-integration-v1; source correctness remains separately qualified.')
    add('The stage2fan32 trial binds the same qualifiedfb18 source and seed2/default')
    add('options. Only two QSF assignments target decode_ctrls_1_down_ready:')
    add('IMPLEMENT_AS_OUTPUT_OF_LOGIC_CELL ON and MAX_FANOUT32. All103 non-QSF')
    add('inputs are exact. No CPU suite was repeated; actual mapped applicability,')
    add('duplication and timing remain post-fit checks. See stage2-fanout-integration-v1/.')
    add('The all-RS ffed fit keeps that exact fanout32 QSF and103 nonCPU inputs;')
    add('only the CPU changes. Its own14-case qualification binds the frozen input')
    add('and independent composition audits. See all-operand-integration-v1/.')
    add('Both direct-mask856b fits bind that CPU qualification and seven exact source')
    add('overrides. Fan32/global variants have103 identical non-QSF inputs; only')
    add('MAX_FANOUT32 is replaced by GLOBAL_SIGNAL "GLOBAL CLOCK" on the same exact')
    add('stage2-ready node, retaining IMPLEMENT_AS_OUTPUT_OF_LOGIC_CELL ON. Saved')
    add('seed2,100MHz,allSDC/PLLs and other options match. Actual global routing and')
    add('clock-network mapping remain post-fit checks. See second-usable-integration-v1/.')
    add('')
    add('Texture early-offer component')
    add('GPU2d4870c5/cache0d2d3669 removes hit-response feedback only from prime-A')
    add('address selection. Original handshakes/state/port B remain; no FF or cycle')
    add('is added. Actual FSM induction covers 131,072 transitions and producer')
    add('equations cover 65,536 cases across all three request modes. The default')
    add('legacy prime comparison covers 131,072 controls; independent source and')
    add('65,536-case boundary audits pass. Exact source reversal/replay is retained.')
    add('Original/default-disabled/enabled 16KiB caches match all nine outputs across')
    add('244,148 cycles, 12,404 fills and four hard-reset/flush epochs. Actual os30')
    add('GPU 38x3 rendering uses the selected BRAM queues: complete logs, 237 pixel')
    add('checks and 114 workload-cycle pairs match. Monitors cover 42,548,619 cycles,')
    add('387 hard/six soft resets, 1,866 fill-output windows and 40,717 outside-prefix')
    add('offers with qualified valid low, with zero errors. Component correctness and')
    add('cycles are established; physical timing and game FPS are separate outcomes.')
    add('')
    add('Focused-only pointer and RS3 candidates')
    add('Pointer d6f88a0c changes only the existing pointer FF clock-enable attribute')
    add('and equivalent unconditional XOR next-state expression. The independent')
    add('audit verifies all16 binary transitions, asynchronous reset between clocks,')
    add('actual emitted proof wrappers and exact source reversal/replay. Arbitrary')
    add('four-state X equivalence and actual D-versus-ENA mapping are not claimed.')
    add('RS3 186a99f3 retains all live enables/selectors/storage and uses two kept')
    add('combinational data groups. All1,048,576 enable/data-pattern cases pass the')
    add('actual-cone proof and independent lowest-enabled oracle for every32-bit')
    add('position, including no match. Neither component adds a register or cycle.')
    add('These exact standalone CPUs have no whole-CPU run; their selected triple')
    add('composition has its own fourteen-case qualification above. Physical timing')
    add('and actual D-versus-ENA mapping remain separate post-fit measurements.')
    add('RS2 alternatives on pair5209 are also focused-only:4+6 SHA2aea3501 and')
    add('selected2+8 SHA6c3560a5 each pass1,048,576 actual-cone/oracle cases. The')
    add('2+8 split crosses the measured producer0-to3 suppression boundary; all')
    add('enables/selectors/state stay exact. No standalone CPU suite was run here.')
    add('')
    add('Verified source identities and exact per-profile meanings')
    for label,profile in profiles.items():
        add('  '+label+': '+profile['description'])
        add('    '+profile['netlist'])
        add('    SHA256 '+profile['netlist_sha256'])
    add('Each netlist and simulator matches its saved build manifest. Generated')
    add('testbench Verilog and C++ snapshots also match. The monitor generator was')
    add('extended for rejected-v7 diagnosis and optional two-bank checking. Every')
    add('historical compiled snapshot is retained. Current candidate source hashes match.')
    add('')
    add('Other generated RTL without direct whole-CPU execution of that exact SHA')
    add('These may have generator/structural/combinational proofs elsewhere; no')
    add('whole-CPU qualification of the exact files is claimed by this report.')
    for item in data['unexecuted_netlists']:
        add('  '+item['path']);add('    SHA256 '+item['sha256'])
    add('')
    add('Evidence index (all paths relative to this validation directory)')
    add('  validation-inventory.json: audited netlist/simulator/log hashes and coverage.')
    add('  summary-all.json: initial eight profiles and capacity controls.')
    add('  summary-frontend.json, summary-immediate.json: A/B/C frontend comparisons.')
    add('  summary-control.json, summary-rawfp.json: factored-logic checkpoints.')
    add('  summary-skid.json: corrected full-ALU skid versus B and shipping single.')
    add('  summary-skid-immediate.json: immediate skid versus corrected full-ALU.')
    add('  summary-fpu-tag.json: cycle-neutral FPU/tag derivatives and shipping baseline.')
    add('  summary-final-cpus.json: all corrected timing derivatives, including integer RS2.')
    add('  PROFILE/build-manifest.json, *-results.json, *.log: exact individual runs.')
    add('  Corrected skid profiles/skid-coverage.json: all14 per-run corner counts.')
    add('  README-history.txt: preserved chronological notes; this report supersedes')
    add('  their historical "final" checkpoints. Source README gives reproduction.')
    text='\n'.join(lines)+'\n'
    for before,after in [('all278','all 278'),('all14','all 14'),('phase317','phase 317'),('takes396','takes 396'),('versus187','versus 187'),('takes180','takes 180'),(',193,987',', 193,987'),('and194','and 194'),('hash123','hash 123'),('expected123','expected 123'),('mask1/3','mask 1/3'),('SHA160','SHA 160'),('cover18','cover 18')]:text=text.replace(before,after)
    (OUT/'REPORT.txt').write_text(text)

if __name__=='__main__':main()
