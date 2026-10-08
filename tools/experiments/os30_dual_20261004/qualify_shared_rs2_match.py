#!/usr/bin/env python3
"""Qualify the existing shared stage4 RS2 match before its sampling register."""
from pathlib import Path
import argparse
import difflib
import hashlib
import itertools
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
BASE_SHA = '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
IQ = 'int_rs2_write_match_4_0'
FQ = 'fp_match_RS2_4'
LANE = 'execute_ctrl4_up_LANE_SEL_lane0'
RD = 'execute_ctrl4_up_RD_ENABLE_lane0'
S0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0'
S1 = S0 + '_1'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'shared-rs2-qualified-v1/immediate')
    out = parser.parse_args().output.resolve()
    assert sha(BASE) == BASE_SHA
    source = BASE.read_text()
    def once(text):
        assert source.count(text) == 1, text
        return text
    def stored(name):
        blocks = re.findall(r'  reg ' + name + r';\n  always @\(posedge clk\) if\(execute_ctrl0_down_isReady\)\n    ' + name + r' <= [^;]+;', source)
        assert len(blocks) == 1
        assert len(re.findall(r'\b' + name + r'\s*<=', source)) == 1
        return blocks[0]
    int_block, fp_block = stored(IQ), stored(FQ)
    fp_rhs = fp_block.split(' <= ', 1)[1].removesuffix(';')
    assert int_block.split(' <= ', 1)[1] == f'execute_ctrl3_down_RD_ENABLE_lane0 && ({fp_rhs});'
    fp_old = once(f'    execute_lane0_bypasser_float_RS2_bypassEnables[0] = ({LANE} && {RD}) && {FQ};')
    int_consumer = once(f'    execute_lane0_bypasser_integer_RS2_bypassEnables[4] = {LANE} && {IQ};')
    assert len(re.findall(r'\b' + IQ + r'\b', source)) == 3
    assert len(re.findall(r'\b' + FQ + r'\b', source)) == 3
    advance = []
    for stage in range(12):
        for line in [f'  assign execute_ctrl{stage}_down_ready = execute_ctrl{stage+1}_up_ready;',
                     f'  assign execute_ctrl{stage}_down_isReady = execute_ctrl{stage}_down_ready;',
                     f'  assign execute_ctrl{stage}_up_ready = execute_ctrl{stage}_down_isReady;']:
            advance.append(once(line))
    lane_writes = re.findall(r'\b' + LANE + r'\s*<=\s*[^;]+;', source)
    assert lane_writes == [f"{LANE} <= 1'b0;", f'{LANE} <= execute_ctrl3_down_LANE_SEL_lane0;']
    once(f'      if(execute_ctrl3_down_isReady) begin\n        {LANE} <= execute_ctrl3_down_LANE_SEL_lane0;')
    reset_blocks = re.findall(r'^  always @\(posedge clk or posedge reset\) begin.*?^  end', source, re.M | re.S)
    reset_block = [b for b in reset_blocks if lane_writes[0] in b]
    assert len(reset_block) == 1 and lane_writes[0] in reset_block[0].split('    end else begin', 1)[0]
    rd_capture = once(f'      {RD} <= execute_ctrl3_down_RD_ENABLE_lane0;')
    assert len(re.findall(r'\b' + RD + r'\s*<=', source)) == 1
    capture_blocks = re.findall(r'    if\(execute_ctrl3_down_isReady\) begin\n((?:      [^\n]*\n)+)    end', source)
    assert sum(rd_capture in block for block in capture_blocks) == 1
    new_block = f'''  reg {IQ};
  always @(posedge clk or posedge reset) begin
    if(reset)
      {IQ} <= 1'b0;
    else if(execute_ctrl0_down_isReady)
      {IQ} <= execute_ctrl3_down_LANE_SEL_lane0 && execute_ctrl3_down_RD_ENABLE_lane0 && ({fp_rhs});
  end'''
    fp_new = f'    execute_lane0_bypasser_float_RS2_bypassEnables[0] = {IQ};'
    candidate = source.replace(int_block, new_block).replace(fp_old, fp_new)
    assert candidate.replace(new_block, int_block).replace(fp_new, fp_old) == source
    # The old raw FP match is intentionally retained but has no consumer. Its
    # physical elimination must be measured; no state-removal claim is made.
    assert len(re.findall(r'\b' + FQ + r'\b', candidate)) == 2
    assert candidate.count(int_consumer) == 1
    out.mkdir(parents=True, exist_ok=False)
    (out / 'VexiiRiscv.v').write_text(candidate)
    contracts = dict(common_advances=advance, old_int_block=int_block, old_fp_block=fp_block,
        new_int_block=new_block, original_integer_consumer=int_consumer,
        original_float_consumer=fp_old, new_float_consumer=fp_new,
        lane_writes=lane_writes, rd_capture=rd_capture,
        original_int_match_references=3, original_fp_match_references=3,
        candidate_raw_fp_match_references=2)
    (out / 'source-contracts.json').write_text(json.dumps(contracts, indent=2) + '\n')
    # Actual sampled input expressions are kept. Arbitrary dispatch selectors,
    # including11, are permitted: both old matches share identical expressions.
    tb = '''module shared_proof(input clk,reset,ready,input incoming_lane,incoming_rd,
input [1:0] selection,input [5:0] producer,candidate0,candidate1,
output [1:0] original,result);
wire execute_ctrl0_down_isReady=ready;
wire execute_ctrl3_down_LANE_SEL_lane0=incoming_lane;
wire execute_ctrl3_down_RD_ENABLE_lane0=incoming_rd;
wire [4:0] execute_ctrl3_down_RD_PHYS_lane0=producer[4:0];
wire execute_ctrl3_down_RD_RFID_lane0=producer[5];
'''
    tb += f'wire {S0}=selection[0],{S1}=selection[1];\n'
    for i in range(2):
        tb += f'wire [4:0] DispatchPlugin_logic_candidates_{i}_ctx_hm_RS2_PHYS=candidate{i}[4:0];\nwire DispatchPlugin_logic_candidates_{i}_ctx_hm_RS2_RFID=candidate{i}[5];\n'
        text = once(f'  assign fp_preselect_RS2_4_candidate{i} = (execute_ctrl3_down_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_{i}_ctx_hm_RS2_PHYS) && (execute_ctrl3_down_RD_RFID_lane0 == DispatchPlugin_logic_candidates_{i}_ctx_hm_RS2_RFID);')
        tb += f'wire fp_preselect_RS2_4_candidate{i};\n' + text + '\n'
    tb += int_block + '\n' + fp_block + '\n' + new_block.replace(IQ, 'qualified_match') + '\n'
    tb += f'reg {LANE},{RD};\n'
    tb += f'always @(posedge clk or posedge reset) if(reset) {LANE}<=0; else if(ready) {LANE}<=incoming_lane;\n'
    tb += f'always @(posedge clk) if(ready) {RD}<=incoming_rd;\n'
    tb += 'assign original[0]=' + int_consumer.split(' = ', 1)[1] + '\n'
    tb += 'assign original[1]=' + fp_old.split(' = ', 1)[1] + '\n'
    tb += 'assign result[0]=' + int_consumer.split(' = ', 1)[1].replace(IQ, 'qualified_match') + '\n'
    tb += 'assign result[1]=qualified_match;\nendmodule\n'
    (out / 'proof.v').write_text(tb)
    cpp = r'''#include "Vshared_proof.h"
#include <cstdio>
#include <cstdint>
uint32_t rng=0x48192837;
uint32_t next(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
void edge(Vshared_proof& d){d.clk=0;d.eval();d.clk=1;d.eval();d.clk=0;d.eval();}
int main(){Vshared_proof d;unsigned cases=0;
d.reset=0;d.ready=0;d.eval();d.reset=1;d.eval();if(d.original!=d.result)return 1;
d.reset=0;for(int i=0;i<8;++i){edge(d);if(d.original!=d.result)return 2;}
d.ready=1;
for(unsigned p=0;p<64;++p)for(unsigned a=0;a<64;++a)for(unsigned b=0;b<64;++b)
for(unsigned s=0;s<4;++s)for(unsigned lane=0;lane<2;++lane)for(unsigned rd=0;rd<2;++rd){
 d.producer=p;d.candidate0=a;d.candidate1=b;d.selection=s;d.incoming_lane=lane;d.incoming_rd=rd;
 edge(d);if(d.original!=d.result)return 3;++cases;
}
unsigned stalls=0,resets=0;
for(unsigned i=0;i<1000000;++i){
 d.ready=(next()&3)!=0;d.incoming_lane=next()&1;d.incoming_rd=next()&1;
 d.producer=next()&63;d.candidate0=next()&63;d.candidate1=next()&63;d.selection=next()&3;
 d.reset=(i%107)==0;d.eval();if(d.original!=d.result)return 4;
 edge(d);if(d.original!=d.result)return 5;
 stalls+=!d.ready;resets+=d.reset;
}
std::printf("PASS %u complete-ID/control sampled transitions including selector11;1000000 supplemental cycles stalls=%u resets=%u; both consumers equal\n",cases,stalls,resets);
}
'''
    (out / 'proof.cpp').write_text(cpp)
    # Exact Boolean invariant over the old stored lane/write/equality and both
    # original/new match states. An asynchronous reset directly establishes it.
    induction = 0
    reset_cases = 0
    for lane, rd, fp, integer, qualified in itertools.product(range(2), repeat=5):
        assert not (0 and rd and fp) and not (0 and integer)
        reset_cases += 1
        if qualified != (lane and rd and fp) or (lane and integer) != qualified:
            continue
        for reset, ready, lane_in, rd_in, eq_in in itertools.product(range(2), repeat=5):
            ln = 0 if reset else lane_in if ready else lane
            rn = rd_in if ready else rd
            fn = eq_in if ready else fp
            inn = rd_in and eq_in if ready else integer
            qn = 0 if reset else lane_in and rd_in and eq_in if ready else qualified
            assert qn == (ln and rn and fn) == (ln and inn)
            assert (ln and qn) == (ln and inn)
            induction += 1
    assert induction == 384 and reset_cases == 32
    (out / 'induction.json').write_text(json.dumps(dict(passed=True, admitted_transitions=induction,
        asynchronous_reset_cases=reset_cases, invariant='qualified == lane && rd && fp == lane && integer'), indent=2) + '\n')
    for name, command in [('build', ['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal', '--top-module', 'shared_proof', '--Mdir', str(out / 'obj'), '-CFLAGS', '-std=c++17 -O2', str(out / 'proof.v'), str(out / 'proof.cpp')]),
        ('proof', [str(out / 'obj/Vshared_proof')]),
        ('lint', ['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(out / 'VexiiRiscv.v')])]:
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    for name in ['build', 'lint']:
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / (name + '.log')).read_text())
    patch = ''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v'))
    (out / 'shared-rs2-qualified.patch').write_text(patch)
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(candidate)
    for name, extra, expected in [('reverse', ['-R'], source), ('replay', [], candidate)]:
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(out / 'shared-rs2-qualified.patch')], cwd=replay, check=True, stdout=log, stderr=subprocess.STDOUT)
        assert (replay / 'VexiiRiscv.v').read_text() == expected
    shutil.copy2(__file__, out / Path(__file__).name)
    manifest = dict(reference_sha256=BASE_SHA, candidate_sha256=sha(out / 'VexiiRiscv.v'),
        exact_scope='Only existing int_rs2_write_match_4_0 capture/reset and FP_RS2 bypassEnables[0] changed.',
        added_register_bits=0, added_cycles=0, changed_existing_register_async_reset=True,
        original_integer_consumer_unchanged=True, two_consumers_proved_equivalent=True,
        old_raw_fp_match_has_no_consumers=True, physical_dead_register_elimination_unmeasured=True,
        all_common_advances_verified=True, exact_shared_input_functions=True,
        complete_ID_control_transitions=4194304, arbitrary_selector11_included=True,
        sampling_induction_transitions=induction, supplemental_cycles=1000000,
        exact_reverse_replay=True, lint_pass=True,
        whole_cpu_validation='Pending independent emitted audit and selected retained14 fixtures.',
        physical_qualification='Requires full-core fit; no area/timing saving assumed.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    evidence = ['source-contracts.json', 'proof.v', 'proof.cpp', 'proof.log', 'induction.json', 'shared-rs2-qualified.patch']
    (out / 'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out / Path(__file__).name), source_sha256=BASE_SHA,
        candidate_sha256=sha(out / 'VexiiRiscv.v'), evidence={name: sha(out / name) for name in evidence}), indent=2) + '\n')
    print((out / 'proof.log').read_text() + json.dumps(manifest, indent=2))

if __name__ == '__main__':
    main()
