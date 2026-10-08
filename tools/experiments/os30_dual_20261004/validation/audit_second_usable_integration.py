#!/usr/bin/env python3
"""Audit exact direct-mask CPU inputs with fanout32 versus global ready routing."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
OUT=BASE/'validation/second-usable-integration-v1'
REFERENCE='fit-rvc-skid-allrs-stage2fan32-v1'
FITS=['fit-rvc-skid-secondusable-stage2fan32-v1','fit-rvc-skid-secondusable-stage2global-v1']
QSF='src/fpga/targets/pocket/ap_core.qsf'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
LABEL='rvc_skid_immediate_pingpong_second_usable'
SHA='856b139adcaf3147f0a791b2c1dfdd4b6b7b258a64a1b97c1e6a4151c652565a'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())

def main():
    reference=BASE/REFERENCE;old=read(reference/'frozen-sources.json')
    oldexp=read(reference/'experiment.json');oldinv=read(reference/'inventory.json')[0]
    oldjob=reference/'jobs/pocket-os30-s2';target='ic|cpu|cpu|decode_ctrls_1_down_ready'
    fan='set_instance_assignment -name MAX_FANOUT 32 -to '+target+'\n'
    glob='set_instance_assignment -name GLOBAL_SIGNAL "GLOBAL CLOCK" -to '+target+'\n'
    common='set_instance_assignment -name IMPLEMENT_AS_OUTPUT_OF_LOGIC_CELL ON -to '+target+'\n'
    baseline=BASE/'validation/all-operand-integration-v1/manifest.json';ba=read(baseline)
    assert ba['passed'] and ba['candidate_fit']==REFERENCE
    for name,digest in ba['evidence_sha256'].items():assert sha(ROOT/name)==digest,name
    evidence=[baseline,reference/'frozen-sources.json',reference/'experiment.json',reference/'inventory.json',oldjob/QSF,Path(__file__)]
    fit_results=[];frozen_maps=[]
    for index,fit in enumerate(FITS):
        folder=BASE/fit;frozen=folder/'frozen';job=folder/'jobs/pocket-os30-s2'
        sources=read(folder/'frozen-sources.json');exp=read(folder/'experiment.json');inv=read(folder/'inventory.json')
        assert len(sources)==104 and sources.keys()==old.keys();frozen_maps.append(sources)
        changed={k for k in old if old[k]!=sources[k]}
        assert changed==({CPU,QSF} if index else {CPU})
        assert sources[CPU]==exp['cpu_sha256']==SHA
        for path,digest in sources.items():
            assert sha(frozen/path)==digest,path
            if path!=QSF:assert sha(job/path)==digest,path
        expected=(reference/'frozen'/QSF).read_text();job_expected=(oldjob/QSF).read_text()
        assert expected.count(fan)==job_expected.count(fan)==1
        option=folder/('stage2-ready-global.qsf' if index else 'stage2-ready-fanout.qsf')
        assert option.read_text()==common+(glob if index else fan)
        if index:
            expected=expected.replace(fan,glob,1);job_expected=job_expected.replace(fan,glob,1)
            assert exp['stage2_ready_global'] and exp['stage2_ready_global_sha256']==sha(option)
            assert exp['stage2_ready_fanout_limit'] is None and exp['stage2_ready_fanout_sha256'] is None
        else:
            assert not exp.get('stage2_ready_global',False)
            assert exp['stage2_ready_fanout_limit']==32 and exp['stage2_ready_fanout_sha256']==sha(option)
        assert (frozen/QSF).read_text()==expected and (job/QSF).read_text()==job_expected
        normalized=dict(exp)
        if index:
            normalized.pop('stage2_ready_global');normalized.pop('stage2_ready_global_sha256')
            normalized['stage2_ready_fanout_limit']=32
            normalized['stage2_ready_fanout_sha256']=oldexp['stage2_ready_fanout_sha256']
        assert normalized.keys()==oldexp.keys()
        assert [k for k in oldexp if oldexp[k]!=normalized[k]]==['cpu_source','cpu_sha256']
        assert len(inv)==1 and inv[0]['seed']==inv[0]['saved_seed']==2
        skip=['cpu_sha256','private_cpu_source']
        assert {k:v for k,v in inv[0].items() if k not in skip}=={k:v for k,v in oldinv.items() if k not in skip}
        assert exp['clock_mhz']==100 and not exp['seed_sweep'] and not exp['production_modified']
        evidence += [folder/'frozen-sources.json',folder/'experiment.json',folder/'inventory.json',option,job/QSF]
        fit_results.append(dict(fit=fit,frozen_inputs=104,only_changes_vs_ffed_fan32=sorted(changed),
            stage2_ready_fanout_limit=None if index else 32,stage2_ready_global=bool(index),
            assignment_target=target,job_QSF_sha256=sha(job/QSF),option_sha256=sha(option)))
    assert [k for k in frozen_maps[0] if frozen_maps[0][k]!=frozen_maps[1][k]]==[QSF]
    source=BASE/'second-usable-direct-v1/immediate';m=read(source/'manifest.json');recipe=read(source/'recipe.json')
    assert sha(source/'VexiiRiscv.v')==m['candidate_sha256']==recipe['candidate_sha256']==SHA
    assert m['exhaustive_cases']==65536 and m['observed_bits']==31 and m['extra_registers']==m['extra_cycles']==0
    assert m['all_sequential_source_unchanged'] and m['current_lastFromBuffer_and_trap_predicates_unchanged']
    assert sha(source/'factor_second_usable.py')==recipe['script_sha256']
    for name,digest in recipe['evidence'].items():assert sha(source/name)==digest,name
    audit=BASE/'second-usable-emitted-audit-v1/manifest.json';a=read(audit)
    assert a['candidate_sha256']==SHA and a['independent_mask_math_cases']==65536
    assert a['fresh_cones_and_wrapper_match'] and a['actual_cone_binary_rerun'] and a['unchanged_state_and_other_consumers']
    for name,digest in a['inputs'].items():assert sha(ROOT/name)==digest,name
    evidence += [source/'manifest.json',source/'recipe.json',source/'factor_second_usable.py',audit]
    runtime=dict(label=LABEL,completed_cases=0,qualification_pending=True,additional_runs_by_auditor=0)
    if (source/'qualification.json').exists():
        q=read(source/'qualification.json');folder=BASE/'validation'/LABEL;build=read(folder/'build-manifest.json')
        assert q['passed'] and q['candidate_cpu_sha256']==SHA
        assert sha(folder/'build-manifest.json')==q['test_build_manifest_sha256']
        assert sha(folder/'obj/Vtb_system')==build['simulator_sha256'] and build['sources'][build['netlist']]==SHA
        assert sha(source/'recipe.json')==q['source_recipe_sha256'] and sha(audit)==q['independent_audit_sha256']
        ce=read(folder/'cycle-equivalence.json');assert ce['candidate_cpu_sha256']==SHA and len(ce['results'])==14
        assert sha(folder/'cycle-equivalence.json')==q['cycle_equivalence_sha256']
        for item in ce['results']:
            assert sha(folder/item['log'])==item['sha256'],item['log']
            for label,equal in item['byte_identical_to'].items():assert equal and sha(BASE/'validation'/label/item['log'])==item['sha256']
        for name in ['skid','pingpong']:
            p=folder/(name+'-coverage.json');c=read(p)
            assert sha(p)==q[name+'_coverage_sha256'] and c['passed'] and c['totals']['cycles']==33363543 and c['totals']['errors']==0
            evidence.append(p)
        evidence += [source/'qualification.json',folder/'build-manifest.json',folder/'cycle-equivalence.json']
        runtime.update(completed_cases=14,qualification_pending=False,monitored_cycles=33363543,monitor_errors=0,entire_logs_byte_identical=True)
    result=dict(passed=True,reference_fit=REFERENCE,fits=fit_results,cpu_sha256=SHA,
        pair_only_differing_frozen_input=QSF,pair_identical_nonQSF_inputs=103,
        same_RTL_SDC_PLL_and_other_settings=True,configured_clock_mhz=100,saved_seed=2,placement_seed=2,
        focused_actual_cone_cases=65536,independent_emitted_audit=True,cpu_runtime=runtime,
        actual_global_routing_or_clock_mapping_verified=False,
        scope='Exact source/QSF and retained component/CPU evidence. Global routing applicability, timing, clock-network mapping and game execution are separate post-fit/hardware evidence.',
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence})
    OUT.mkdir(exist_ok=True);(OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'PASS direct-mask pair:104 inputs each;only fanout/global QSF differs,CPU14={runtime["completed_cases"]==14}')

if __name__=='__main__':main()
