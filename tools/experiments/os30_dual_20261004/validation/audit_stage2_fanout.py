#!/usr/bin/env python3
"""Bind the stage2-ready fanout trial to qualified fb18 and its exact baseline."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
OUT=BASE/'validation/stage2-fanout-integration-v1'
REFERENCE='fit-rvc-skid-pointer-packer-rs12-texoffer-v1'
CANDIDATE='fit-rvc-skid-rs12-stage2fan32-v1'
QSF='src/fpga/targets/pocket/ap_core.qsf'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())

def main():
    reference=BASE/REFERENCE;candidate=BASE/CANDIDATE
    old=read(reference/'frozen-sources.json');new=read(candidate/'frozen-sources.json')
    assert len(old)==len(new)==104 and old.keys()==new.keys()
    assert [k for k in old if old[k]!=new[k]]==[QSF]
    target='ic|cpu|cpu|decode_ctrls_1_down_ready'
    flags=('set_instance_assignment -name IMPLEMENT_AS_OUTPUT_OF_LOGIC_CELL ON -to '+target+'\n'
           'set_instance_assignment -name MAX_FANOUT 32 -to '+target+'\n')
    flag_file=candidate/'stage2-ready-fanout.qsf';assert flag_file.read_text()==flags
    old_job=reference/'jobs/pocket-os30-s2';new_job=candidate/'jobs/pocket-os30-s2'
    old_qsf=(reference/'frozen'/QSF).read_text();new_qsf=(candidate/'frozen'/QSF).read_text()
    assert new_qsf==old_qsf+'\n'+flags
    assert (new_job/QSF).read_text().count('\n'+flags)==1
    assert (new_job/QSF).read_text().replace('\n'+flags,'',1)==(old_job/QSF).read_text()
    for folder,sources,job in [(reference,old,old_job),(candidate,new,new_job)]:
        for path,digest in sources.items():
            assert sha(folder/'frozen'/path)==digest,path
            if path!=QSF:assert sha(job/path)==digest,path
    ea=read(reference/'experiment.json');eb=read(candidate/'experiment.json')
    assert eb['stage2_ready_fanout_limit']==32 and eb['stage2_ready_fanout_sha256']==sha(flag_file)
    assert eb['saved_seed']==eb['placement_seed']==2 and not eb['explicit_placement_comparison']
    normalized=dict(eb)
    for key,value in [('stage2_ready_fanout_limit',32),('stage2_ready_fanout_sha256',sha(flag_file)),
                      ('saved_seed',2),('placement_seed',2),('explicit_placement_comparison',False)]:
        assert normalized[key]==value
        if key not in ea:normalized.pop(key)
    assert normalized==ea
    ia=read(reference/'inventory.json');ib=read(candidate/'inventory.json')
    assert len(ia)==len(ib)==1 and ib[0]['seed']==ib[0]['saved_seed']==2
    assert {k:v for k,v in ib[0].items() if k!='saved_seed'}==ia[0]
    baseline_path=BASE/'validation/pointer-packer-integration-v2'/(REFERENCE+'.json')
    baseline=read(baseline_path)
    assert sha(Path(__file__).parent/'audit_forwarding_integration.py')==baseline['audit_script_sha256']
    assert baseline['all_frozen_and_job_hashes_verified'] and baseline['frozen_source_count']==104
    assert baseline['cpu_evidence']['completed_fourteen'] and baseline['cpu_evidence']['all_fourteen_complete_logs_verified']
    assert baseline['source_overrides']['cpu']['sha256']==new[CPU]==eb['cpu_sha256']
    assert new[CPU]=='fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb'
    assert sha(reference/'experiment.json')==baseline['experiment_sha256']
    assert sha(reference/'frozen-sources.json')==baseline['source_manifest_sha256']
    assert sha(old_job/QSF)==baseline['qsf_sha256']
    for item in baseline['source_overrides'].values():assert new[item['path']]==item['sha256']
    label=baseline['cpu_evidence']['label'];runtime=BASE/'validation'/label
    ce=read(runtime/'cycle-equivalence.json');assert len(ce['results'])==14
    assert sha(runtime/'cycle-equivalence.json')==baseline['cpu_evidence']['cycle_equivalence_sha256']
    for item in ce['results']:assert sha(runtime/item['log'])==item['sha256'],item['log']
    sources=[baseline_path,flag_file,reference/'experiment.json',candidate/'experiment.json',
             reference/'inventory.json',candidate/'inventory.json',reference/'frozen-sources.json',candidate/'frozen-sources.json',
             old_job/QSF,new_job/QSF,runtime/'cycle-equivalence.json',Path(__file__)]
    result=dict(passed=True,reference_fit=REFERENCE,candidate_fit=CANDIDATE,
        frozen_input_count=104,unchanged_frozen_inputs=103,only_changed_frozen_input=QSF,
        exact_added_assignments=flags.splitlines(),assignment_target=target,max_fanout=32,
        saved_seed=2,placement_seed=2,clock_mhz=100,
        all_RTL_timing_constraints_PLL_and_other_options_identical=True,
        cpu_sha256=new[CPU],existing_cpu_cases=14,additional_cpu_runs=0,
        baseline_integration_sha256=sha(baseline_path),
        scope='Source/QSF integration only. Requested assignment presence is not proof of mapped duplication, fanout or improved timing; post-fit audit remains separate.',
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources})
    OUT.mkdir(exist_ok=True);(OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print('PASS stage2fan32:104 frozen inputs,103 unchanged,exact2 QSF assignments,same fb18 CPU14 evidence,no new runtime')

if __name__=='__main__':main()
