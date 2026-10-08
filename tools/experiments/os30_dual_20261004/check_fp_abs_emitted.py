#!/usr/bin/env python3
"""Audit the proof-only floating-point special-class comparison factorization."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
CAND=ART/'fp-cmp-abs-factor-v1/immediate'
OUT=ART/'fp-cmp-abs-emitted-audit-v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    base=ART/'fp-last-combined-v1/immediate/VexiiRiscv.v'
    target=CAND/'VexiiRiscv.v'
    assert sha(base)=='9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377'
    assert sha(target)=='67ad5bafec4d5fd654469a820c00ac5c3599878b8d9703141c10ff62e1a59bb6'
    before,after=base.read_text(),target.read_text()
    dest='FpuCmpPlugin_logic_onCmp_rs1AbsSmaller'
    pattern=r'  always @\(\*\) begin\n    '+dest+r' = .*?\n  end'
    old=re.findall(pattern,before,re.S);new=re.findall(pattern,after,re.S)
    assert len(old)==len(new)==1
    assert before.replace(old[0],new[0],1)==after
    flags=['when_FpuCmpPlugin_l'+str(i) for i in range(110,114)]
    inputs=['execute_ctrl2_down_FpuCmpPlugin_logic_onCmp_'+n+'_lane0' for n in ['rs1ExpSmaller','expEqual','rs1MantissaSmaller']]+flags
    # Final zero overrides dominate either one override, regardless of whether
    # floating-point class predicates can actually coincide.
    for bits in range(128):
        values=[bool(bits&(1<<i)) for i in range(7)]
        exp_less,exp_eq,mant_less,a,b,c,d=values
        old_value=exp_less or exp_eq and mant_less
        if a:old_value=True
        if b:old_value=True
        if c:old_value=False
        if d:old_value=False
        new_value=not(c or d) and (a or b or exp_less or exp_eq and mant_less)
        assert old_value==new_value
    checker=CAND/'check_vexii_os20_timing.py'
    assert sha(checker)=='80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
    spec=importlib.util.spec_from_file_location('abs_check',checker)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for name,source in [('reference',before),('candidate',after)]:
        cone,width=module.cone(source,name,inputs,[dest])
        assert width==1
        assert cone==(CAND/(name+'.v')).read_text()
        assert (CAND/'tb.sv').read_text().count(cone)==1
    OUT.mkdir(exist_ok=False)
    with (OUT/'actual-proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert 'PASS all32 independent class/base conditions and all128' in (OUT/'actual-proof-rerun.log').read_text()
    result=dict(reference_sha256=sha(base),candidate_sha256=sha(target),exact_one_combinational_block_only=True,
       all_class_numeric_sign_nan_flag_result_and_sequential_source_unchanged=True,
       no_class_correlation_assumption=True,independent_boolean_cases=128,
       actual_fresh_cones_match_artifacts=True,actual_proof_rerun_checks=160,
       new_registers=0,new_cycles=0,new_keep_boundaries=0,
       qualification='Proof-only fallback; full CPU tests and full fit intentionally not run.')
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
