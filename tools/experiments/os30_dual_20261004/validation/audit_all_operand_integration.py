#!/usr/bin/env python3
"""Bind all-operand ffed to its fanout32 baseline and its own fourteen CPU cases."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
OUT=BASE/'validation/all-operand-integration-v1'
REFERENCE='fit-rvc-skid-rs12-stage2fan32-v1'
CANDIDATE='fit-rvc-skid-allrs-stage2fan32-v1'
QSF='src/fpga/targets/pocket/ap_core.qsf'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
LABEL='rvc_skid_immediate_pingpong_all_operand_groups'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())

def main():
    reference=BASE/REFERENCE;candidate=BASE/CANDIDATE
    old=read(reference/'frozen-sources.json');new=read(candidate/'frozen-sources.json')
    assert len(old)==len(new)==104 and old.keys()==new.keys()
    assert [k for k in old if old[k]!=new[k]]==[CPU]
    assert old[CPU]=='fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb'
    assert new[CPU]=='ffed6f6e299b8e7d3275b781d55d7094ae4d6c086b9f57d6553f794cd545082c'
    old_job=reference/'jobs/pocket-os30-s2';new_job=candidate/'jobs/pocket-os30-s2'
    assert (old_job/QSF).read_bytes()==(new_job/QSF).read_bytes()
    assert (reference/'stage2-ready-fanout.qsf').read_bytes()==(candidate/'stage2-ready-fanout.qsf').read_bytes()
    for folder,sources,job in [(reference,old,old_job),(candidate,new,new_job)]:
        for path,digest in sources.items():
            assert sha(folder/'frozen'/path)==digest,path
            if path!=QSF:assert sha(job/path)==digest,path
    ea=read(reference/'experiment.json');eb=read(candidate/'experiment.json')
    assert [k for k in ea if ea[k]!=eb[k]]==['cpu_source','cpu_sha256'] and ea.keys()==eb.keys()
    ia=read(reference/'inventory.json');ib=read(candidate/'inventory.json')
    assert len(ia)==len(ib)==1 and ib[0]['seed']==ib[0]['saved_seed']==2
    assert {k:v for k,v in ib[0].items() if k not in ['cpu_sha256','private_cpu_source']}=={k:v for k,v in ia[0].items() if k not in ['cpu_sha256','private_cpu_source']}
    baseline_path=BASE/'validation/stage2-fanout-integration-v1/manifest.json';baseline=read(baseline_path)
    assert baseline['passed'] and baseline['candidate_fit']==REFERENCE
    for name,digest in baseline['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
    source=Path(eb['cpu_source']).parent;manifest=read(source/'manifest.json');recipe=read(source/'recipe.json')
    assert sha(source/'VexiiRiscv.v')==manifest['candidate_sha256']==recipe['candidate_sha256']==new[CPU]
    assert manifest['reference_sha256']==old[CPU] and manifest['new_register_bits']==manifest['new_cycles']==0
    assert sha(source/'compose_all_operand_groups.py')==recipe['script_sha256']
    for name,digest in recipe['patches'].items():assert sha(source/name)==digest,name
    for key in ['all_six_original_component_patch_orders_identical','all_six_reversals_restore_5209',
                'reversing_only_rs3_restores_fb18','rs3_and_parent_patch_commute',
                'all_three_fresh_actual_cones_identical_to_proved_components','all_three_proofs_rerun']:
        assert manifest[key],key
    replay=BASE/'all-operand-groups-replay-v1/immediate/VexiiRiscv.v';assert sha(replay)==new[CPU]
    audit_path=BASE/'all-operand-groups-composition-audit-v1/manifest.json';audit=read(audit_path)
    assert audit['source_sha256']['final']==new[CPU] and audit['source_sha256']['previous']==old[CPU]
    for key in ['exact_rs3_reuse_from_independently_proved_186a','only_rs3_data_block_added_to_fb18',
                'all_six_orders_and_reversals_exact','no_other_source_changes','all_sequential_blocks_byte_identical']:
        assert audit[key],key
    evidence=[baseline_path,source/'manifest.json',source/'recipe.json',source/'compose_all_operand_groups.py',replay,audit_path]
    for item in audit['independent_component_audits'].values():
        p=ROOT/item['path'];assert sha(p)==item['sha256'];evidence.append(p)
    runtime=BASE/'validation'/LABEL;build=read(runtime/'build-manifest.json');q=read(source/'qualification.json')
    assert q['passed'] and q['candidate_cpu_sha256']==new[CPU]
    assert sha(runtime/'build-manifest.json')==q['test_build_manifest_sha256']
    assert sha(runtime/'obj/Vtb_system')==build['simulator_sha256']
    assert build['sources'][build['netlist']]==new[CPU]
    assert sha(source/'recipe.json')==q['source_recipe_sha256'] and sha(audit_path)==q['independent_audit_sha256']
    for name,digest in q['component_audits'].items():assert sha(BASE/name/'manifest.json')==digest,name
    ce=read(runtime/'cycle-equivalence.json');assert len(ce['results'])==14 and ce['candidate_cpu_sha256']==new[CPU]
    assert sha(runtime/'cycle-equivalence.json')==q['cycle_equivalence_sha256']
    for item in ce['results']:
        assert sha(runtime/item['log'])==item['sha256'],item['log']
        for label,identical in item['byte_identical_to'].items():assert identical and sha(BASE/'validation'/label/item['log'])==item['sha256']
    for name in ['skid','pingpong']:
        p=runtime/(name+'-coverage.json');assert sha(p)==q[name+'_coverage_sha256']
        c=read(p);assert c['passed'] and c['totals']['cycles']==33363543 and c['totals']['errors']==0;evidence.append(p)
    evidence += [source/'qualification.json',runtime/'build-manifest.json',runtime/'cycle-equivalence.json',
        reference/'experiment.json',candidate/'experiment.json',reference/'inventory.json',candidate/'inventory.json',
        reference/'frozen-sources.json',candidate/'frozen-sources.json',old_job/QSF,new_job/QSF,candidate/'stage2-ready-fanout.qsf',Path(__file__)]
    result=dict(passed=True,reference_fit=REFERENCE,candidate_fit=CANDIDATE,frozen_input_count=104,
        unchanged_nonCPU_inputs=103,only_changed_frozen_input=CPU,job_QSF_identical=True,
        stage2_fanout_limit=32,saved_seed=2,placement_seed=2,clock_mhz=100,
        all_other_RTL_options_timing_constraints_PLL_identical=True,cpu_sha256=new[CPU],
        completed_CPU_cases=14,entire_logs_byte_identical_to=['fb18','5209','9fa'],monitored_cycles=33363543,monitor_errors=0,
        additional_runtime_runs_by_auditor=0,
        scope='Source-bound component integration and existing exact CPU tests; mapped fanout/area/timing and game behavior remain separate.',
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence})
    OUT.mkdir(exist_ok=True);(OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS all-RS integration:104 inputs,103 nonCPU identical,same fanout32/seed2;ffed own14 logs bound')

if __name__=='__main__':main()
