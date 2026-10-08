#!/usr/bin/env python3
"""Add one sampled, preselected equality for the measured integer RS1 index5 path."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import itertools
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'integer-rs1-reuse-v1/immediate/VexiiRiscv.v'
BASE_SHA = '34a06d1e2583bfeed966dde6f8ee683deffc6cec8e9fec3f2f70707cf097aba0'
S0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0'
S1 = S0 + '_1'
Q = 'int_rs1_match_4_lane1'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ART / 'integer-rs1-sample-lane1-v2/immediate')
    out = parser.parse_args().output.resolve()
    assert sha(BASE) == BASE_SHA
    source = BASE.read_text()
    def once(text):
        assert source.count(text) == 1, text
        return text
    advance = []
    for stage in range(12):
        for text in [f'  assign execute_ctrl{stage}_down_ready = execute_ctrl{stage+1}_up_ready;',
                     f'  assign execute_ctrl{stage}_down_isReady = execute_ctrl{stage}_down_ready;',
                     f'  assign execute_ctrl{stage}_up_ready = execute_ctrl{stage}_down_isReady;']:
            advance.append(once(text))
    aliases = [once(f'  assign {s} = DispatchPlugin_logic_inserter_0_oh[{i}];') for i, s in enumerate([S0, S1])]
    captures = []
    for field, zero in [('PHYS', "5'h0"), ('RFID', "1'b0")]:
        aliases.append(once(f'  assign execute_ctrl0_up_RS1_{field}_lane0 = (({S0} ? DispatchPlugin_logic_candidates_0_ctx_hm_RS1_{field} : {zero}) | ({S1} ? DispatchPlugin_logic_candidates_1_ctx_hm_RS1_{field} : {zero}));'))
        aliases.append(once(f'  assign execute_ctrl0_down_RS1_{field}_lane0 = execute_ctrl0_up_RS1_{field}_lane0;'))
        aliases.append(once(f'  assign execute_ctrl1_down_RS1_{field}_lane0 = execute_ctrl1_up_RS1_{field}_lane0;'))
        aliases.append(once(f'  assign execute_ctrl4_down_RD_{field}_lane1 = execute_ctrl4_up_RD_{field}_lane1;'))
        for stage, kind, lane in [(1, 'RS1', 0), (4, 'RD', 1)]:
            q = f'execute_ctrl{stage}_up_{kind}_{field}_lane{lane}'
            stmt = once(f'      {q} <= execute_ctrl{stage-1}_down_{kind}_{field}_lane{lane};')
            assert len(re.findall(r'\b' + q + r'\s*<=', source)) == 1
            blocks = re.findall(r'    if\(execute_ctrl' + str(stage-1) + r'_down_isReady\) begin\n((?:      [^\n]*\n)+)    end', source)
            assert sum(stmt in b for b in blocks) == 1
            captures.append(stmt.strip())
    lane = 'execute_ctrl4_up_LANE_SEL_lane1'
    lane_writes = re.findall(r'\b' + lane + r'\s*<=\s*[^;]+;', source)
    assert lane_writes == [f"{lane} <= 1'b0;", f'{lane} <= execute_ctrl3_down_LANE_SEL_lane1;']
    once('      if(execute_ctrl3_down_isReady) begin\n        execute_ctrl4_up_LANE_SEL_lane0 <= execute_ctrl3_down_LANE_SEL_lane0;\n        execute_ctrl4_up_LANE_SEL_lane1 <= execute_ctrl3_down_LANE_SEL_lane1;')
    blocks = re.findall(r'^  always @\(posedge clk or posedge reset\) begin.*?^  end', source, re.M | re.S)
    reset_block = [b for b in blocks if lane_writes[0] in b]
    assert len(reset_block) == 1 and lane_writes[0] in reset_block[0].split('    end else begin', 1)[0]
    old = once('    execute_lane0_bypasser_integer_RS1_bypassEnables[5] = (((execute_ctrl4_up_LANE_SEL_lane1 && execute_ctrl4_up_RD_ENABLE_lane1) && (execute_ctrl4_down_RD_PHYS_lane1 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl4_down_RD_RFID_lane1 == execute_ctrl1_down_RS1_RFID_lane0));')
    new = f'    execute_lane0_bypasser_integer_RS1_bypassEnables[5] = (execute_ctrl4_up_LANE_SEL_lane1 && execute_ctrl4_up_RD_ENABLE_lane1) && {Q};'
    added = ''
    for i in range(2):
        name = f'int_rs1_preselect_4_lane1_candidate{i}'
        added += f'  (* keep , syn_keep *) wire {name} /* synthesis syn_keep = 1 */;\n'
        added += f'  assign {name} = (execute_ctrl3_down_RD_PHYS_lane1 == DispatchPlugin_logic_candidates_{i}_ctx_hm_RS1_PHYS) && (execute_ctrl3_down_RD_RFID_lane1 == DispatchPlugin_logic_candidates_{i}_ctx_hm_RS1_RFID);\n'
    rhs = f'({S0} && int_rs1_preselect_4_lane1_candidate0) || ({S1} && int_rs1_preselect_4_lane1_candidate1) || ((!{S0} && !{S1}) && (execute_ctrl3_down_RD_PHYS_lane1 == 5\'h0) && (execute_ctrl3_down_RD_RFID_lane1 == 1\'b0))'
    added += f'  reg {Q};\n  always @(posedge clk) if(execute_ctrl0_down_isReady)\n    {Q} <= {rhs};\n'
    anchor = once('  reg fp_match_RS1_4;')
    candidate = source.replace(old, new).replace(anchor, added + anchor)
    assert candidate.replace(added, '', 1).replace(new, old, 1) == source
    out.mkdir(parents=True, exist_ok=False)
    (out / 'VexiiRiscv.v').write_text(candidate)
    contracts = dict(common_advances=advance, aliases=aliases, captures=captures,
                     lane_writes=lane_writes, old_enable=old, new_enable=new, new_block=added)
    (out / 'source-contracts.json').write_text(json.dumps(contracts, indent=2) + '\n')
    spec = importlib.util.spec_from_file_location('cones', ROOT / 'tools/check_vexii_os20_timing.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plain = source.replace('  (* maxfan = 16 *) wire execute_freeze_valid;', '  wire execute_freeze_valid;', 1)
    dispatch = mod.dispatch_tb(plain, plain)
    prior = ART / 'match-preselect-all-v1/immediate/dispatch'
    assert dispatch == (prior / 'tb.sv').read_text()
    (out / 'actual-dispatch-tb.sv').write_text(dispatch)
    with (out / 'dispatch-rerun.log').open('w') as log:
        subprocess.run([str(prior / 'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    tb = '''module match_proof(input clk,reset,ready,input [1:0] selection,
input [5:0] candidate0,candidate1,producer3,input incoming_lane,current_rd_enable,
output original,result,output [1:0] capture_matches);
wire execute_ctrl0_down_isReady=ready;
wire [1:0] DispatchPlugin_logic_inserter_0_oh=selection;
'''
    tb += f'wire {S0},{S1};\n'
    for i in range(2):
        tb += f'wire [4:0] DispatchPlugin_logic_candidates_{i}_ctx_hm_RS1_PHYS=candidate{i}[4:0];\nwire DispatchPlugin_logic_candidates_{i}_ctx_hm_RS1_RFID=candidate{i}[5];\n'
    tb += 'wire [4:0] execute_ctrl3_down_RD_PHYS_lane1=producer3[4:0];\nwire execute_ctrl3_down_RD_RFID_lane1=producer3[5];\n'
    for field, width in [('PHYS', '[4:0]'), ('RFID', '')]:
        for side in ['up', 'down']:
            tb += f'wire {width} execute_ctrl0_{side}_RS1_{field}_lane0;\n'
        tb += f'reg {width} execute_ctrl1_up_RS1_{field}_lane0;\nreg {width} execute_ctrl4_up_RD_{field}_lane1;\n'
        tb += f'wire {width} execute_ctrl1_down_RS1_{field}_lane0;\nwire {width} execute_ctrl4_down_RD_{field}_lane1;\n'
    tb += '\n'.join(aliases) + '\n' + added
    tb += 'always @(posedge clk) if(ready) begin\n' + '\n'.join(captures) + '\nend\n'
    tb += 'reg execute_ctrl4_up_LANE_SEL_lane1;\nwire execute_ctrl4_up_RD_ENABLE_lane1=current_rd_enable;\n'
    tb += 'always @(posedge clk or posedge reset) if(reset) execute_ctrl4_up_LANE_SEL_lane1<=0; else if(ready) execute_ctrl4_up_LANE_SEL_lane1<=incoming_lane;\n'
    tb += 'assign original=' + old.split(' = ', 1)[1] + '\nassign result=' + new.split(' = ', 1)[1] + '\n'
    tb += f'assign capture_matches[0]={rhs};\nassign capture_matches[1]=(execute_ctrl3_down_RD_PHYS_lane1 == execute_ctrl0_down_RS1_PHYS_lane0) && (execute_ctrl3_down_RD_RFID_lane1 == execute_ctrl0_down_RS1_RFID_lane0);\nendmodule\n'
    (out / 'proof.v').write_text(tb)
    cpp = r'''#include "Vmatch_proof.h"
#include <cstdio>
#include <cstdint>
uint32_t rng=0x82659171;
uint32_t next(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
void edge(Vmatch_proof& d){d.clk=0;d.eval();d.clk=1;d.eval();d.clk=0;d.eval();}
int main(){Vmatch_proof d;unsigned cases=0;
for(unsigned p=0;p<64;++p)for(unsigned a=0;a<64;++a)for(unsigned b=0;b<64;++b)for(unsigned s=0;s<3;++s){
 d.candidate0=a;d.candidate1=b;d.producer3=p;d.selection=s;d.eval();
 if((d.capture_matches&1)!=((d.capture_matches>>1)&1))return 1;++cases;
}
d.ready=0;d.reset=0;d.eval();d.reset=1;d.eval();if(d.original!=d.result)return 2;
d.reset=0;d.eval();for(int i=0;i<8;++i){edge(d);if(d.original!=d.result)return 3;}
unsigned stalls=0,resets=0,first=0;
for(unsigned i=0;i<1000000;++i){
 d.reset=(i%107)==0;d.ready=(next()&3)!=0;d.selection=next()%3;
 d.candidate0=next()&63;d.candidate1=next()&63;d.producer3=next()&63;
 d.incoming_lane=next()&1;d.current_rd_enable=next()&1;edge(d);
 if(d.original!=d.result)return 4;d.current_rd_enable=next()&1;d.eval();if(d.original!=d.result)return 5;
 stalls+=!d.ready;resets+=d.reset;first+=d.ready&&d.incoming_lane;
}
std::printf("PASS %u exhaustive complete-ID preselection cases; 1000000 sampled cycles stalls=%u resets=%u live_captures=%u\n",cases,stalls,resets,first);
}
'''
    (out / 'proof.cpp').write_text(cpp)
    # Equality is an unconstrained Boolean cut after the exhaustive ID proof.
    # Relation: before any advance lane=0; afterwards match=current equality.
    induction = 0
    for known, lane_bit, equality, match, ready, reset, incoming, nxt_eq, rd in itertools.product(range(2), repeat=9):
        if known and match != equality:
            continue
        if not known and lane_bit:
            continue
        assert (lane_bit and rd and equality) == (lane_bit and rd and match)
        known_n = known or ready
        lane_n = 0 if reset else incoming if ready else lane_bit
        equality_n = nxt_eq if ready else equality
        match_n = nxt_eq if ready else match
        assert known_n and match_n == equality_n or not known_n and lane_n == 0
        assert (lane_n and rd and equality_n) == (lane_n and rd and match_n)
        induction += 1
    assert induction == 256
    (out / 'induction.json').write_text(json.dumps(dict(passed=True, admitted_states=induction,
        relation='Known => match==current equality; !Known => current producer lane is zero. Known becomes true at any common advance and remains true across reset.'), indent=2) + '\n')
    commands = [('build', ['verilator', '--cc', '--exe', '--build', '-j', '2', '-Wno-fatal', '--top-module', 'match_proof', '--Mdir', str(out / 'obj'), '-CFLAGS', '-std=c++17 -O2', str(out / 'proof.v'), str(out / 'proof.cpp')]),
                ('proof', [str(out / 'obj/Vmatch_proof')]),
                ('lint', ['verilator', '--lint-only', '--top-module', 'VexiiRiscv', '-Wno-fatal', str(out / 'VexiiRiscv.v')])]
    for name, command in commands:
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    for name in ['build', 'lint']:
        assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)', (out / (name + '.log')).read_text())
    patch = ''.join(difflib.unified_diff(source.splitlines(True), candidate.splitlines(True), fromfile='a/VexiiRiscv.v', tofile='b/VexiiRiscv.v'))
    (out / 'integer-rs1-sample-lane1.patch').write_text(patch)
    replay = out / 'patch-replay'
    replay.mkdir()
    (replay / 'VexiiRiscv.v').write_text(candidate)
    for name, extra, expected in [('reverse', ['-R'], source), ('replay', [], candidate)]:
        with (out / (name + '.log')).open('w') as log:
            subprocess.run(['patch', '--batch', '--fuzz=0', *extra, '-p1', '-i', str(out / 'integer-rs1-sample-lane1.patch')], cwd=replay, check=True, stdout=log, stderr=subprocess.STDOUT)
        assert (replay / 'VexiiRiscv.v').read_text() == expected
    shutil.copy2(__file__, out / Path(__file__).name)
    manifest = dict(reference_sha256=BASE_SHA, candidate_sha256=sha(out / 'VexiiRiscv.v'),
        exact_scope='One integer RS1 index5 equality sampled from two upstream preselected candidate equalities.',
        added_register_bits=1, added_cycles=0, original_sequential_bytes_unchanged=True,
        all_current_LANE_SEL_and_RD_ENABLE_gates_preserved=True, original_priority_and_data_mux_unchanged=True,
        all_common_advances_verified=True, no_selection_ID_zero_preserved=True,
        complete_ID_preselection_cases=786432, actual_dispatch_selector_cases=2097152,
        sampled_cycles=1000000, inductive_sampling_states=induction, exact_reverse_replay=True,
        lint_pass=True, whole_cpu_validation='Pending selection and retained14 fixtures.',
        physical_qualification='Requires full-core fit; no area or timing gain assumed.')
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    evidence = ['source-contracts.json', 'proof.v', 'proof.cpp', 'proof.log', 'induction.json', 'actual-dispatch-tb.sv', 'dispatch-rerun.log', 'integer-rs1-sample-lane1.patch']
    (out / 'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out / Path(__file__).name),
        source_sha256=BASE_SHA, candidate_sha256=sha(out / 'VexiiRiscv.v'), evidence={name: sha(out / name) for name in evidence}), indent=2) + '\n')
    print((out / 'proof.log').read_text() + json.dumps(manifest, indent=2))

if __name__ == '__main__':
    main()
