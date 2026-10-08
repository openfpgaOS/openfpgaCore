#!/usr/bin/env python3
"""Prove the second-start survivor formula independently; do not emit RTL."""
from pathlib import Path
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parents[4]
BASE=ROOT/'docs/measurements/os30-dual-20261004'
SOURCE=BASE/'all-operand-groups-v1/immediate/VexiiRiscv.v'
OUT=BASE/'frontend-second-usable-analysis-v1'
SHA='ffed6f6e299b8e7d3275b781d55d7094ae4d6c086b9f57d6553f794cd545082c'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    assert sha(SOURCE)==SHA
    text=SOURCE.read_text()
    assert text.count("  assign AlignerPlugin_logic_usedMask_0 = 8'h0;")==1
    usage=[]
    for i in range(8):
        p=f'AlignerPlugin_logic_scanners_{i}'
        assert text.count(f"  assign {p}_checker_0_required = 1'b1;")==1
        assert text.count(f"  assign {p}_checker_1_required = (AlignerPlugin_logic_slices_data_{i}[1 : 0] == 2'b11);")==1
        expected=f"  always @(*) begin\n    {p}_usageMask = 8'h0;\n    {p}_usageMask[{i}] = {p}_checker_0_required;\n"
        if i<7:expected+=f'    {p}_usageMask[{i+1}] = {p}_checker_1_required;\n'
        expected+='  end'
        assert text.count(expected)==1
        usage.append(expected)
    cases=0;empty_second=0;sparse_masks=0
    for available in range(256):
        for required in range(256):
            first=next((i for i in range(8) if available>>i&1),None)
            consumed=0 if first is None else ((1<<first)|(((required>>first)&1)<<(first+1)))&255
            reference=(available&~consumed)>>1
            direct=0
            for j in range(1,8):
                earlier=bool(available&((1<<(j-1))-1))
                prior_short=bool(available>>(j-1)&1) and not bool(required>>(j-1)&1)
                survives=bool(available>>j&1) and (earlier or prior_short)
                direct|=int(survives)<<(j-1)
            assert direct==reference,(available,required,direct,reference)
            second=next((j for j in range(1,8) if reference>>(j-1)&1),None)
            last_oracle=second is None or second<3 or (second==3 and not bool(required&8))
            low=bool(direct&3);boundary=bool(direct&4);high=bool(direct&120)
            last=not((not low) and ((boundary and bool(required&8)) or ((not boundary) and high)))
            assert last==last_oracle,(available,required,second,last,last_oracle)
            empty_second+=second is None
            bits=[i for i in range(8) if available>>i&1]
            sparse_masks+=bool(bits) and bits[-1]-bits[0]+1!=len(bits)
            cases+=1
    OUT.mkdir(exist_ok=False)
    log=f'PASS {cases} arbitrary V8/R8 cases: all7 survivor bits and unchanged ownership classification; empty-second cases={empty_second}; sparse-mask cases={sparse_masks}. Independent mathematical proof, not an actual emitted-cone test.\n'
    (OUT/'proof.log').write_text(log)
    (OUT/'usage-contracts.json').write_text(json.dumps(usage,indent=2)+'\n')
    (OUT/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    timing=BASE/'timing-audit-rvc-skid-allrs-stage2fan32-v1/slow_85_cpu_all.rpt'
    result=dict(reference_cpu_sha256=SHA,cases=cases,available_patterns=256,required_patterns=256,
        second_survivor_bits=7,empty_second_cases=empty_second,sparse_mask_cases=sparse_masks,
        formula='W[j] = V[j] && ((|V[j-2:0]) || (V[j-1] && !R[j-1])) for j=1..7; empty reduction at j1 is0.',
        output_mapping='extractor1 usableMask[6:0] = {W7,W6,W5,W4,W3,W2,W1}',
        actual_usedMask0_constant_zero_verified=True,actual_first_usage_start_and_optional_successor_rule_verified=True,
        no_contiguity_or_valid_control_assumption=True,empty_second_lastFromBuffer_true_preserved=True,
        selected_scope='Generator emits only usableMask1 RHS replacement, retaining current lastFromBuffer predicates and all other source.',
        scope_boundary='Independent binary mathematical oracle with narrow source contracts; generator must additionally prove freshly extracted actual RTL cone and freeze exact patch/replay.',
        rtl_files_written=0,runtime_suites_run=0,
        evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in [SOURCE,timing,Path(__file__),OUT/'usage-contracts.json',OUT/'proof.log']})
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    print(log,end='')

if __name__=='__main__':main()
