#!/usr/bin/env python3
"""Factor lane-zero integer RS1 forwarding into two local priority groups."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
REF = ART / 'pointer-packer-rs3-combined-v1/immediate/VexiiRiscv.v'
REF_SHA = 'a083bdb084b9dfd7b8944287ef3411b4dc739bf366f7dfa3f42599d5edd0528b'
CHECK = ROOT / 'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
EN = 'execute_lane0_bypasser_integer_RS1_bypassEnables'
SEL = 'execute_lane0_bypasser_integer_RS1_sel'
DEST = '_zz_execute_ctrl1_down_integer_RS1_lane0_1'
EARLY = 'integer_rs1_priority_early_data'
LATE = 'integer_rs1_priority_late_data'
KEEP = '  (* keep , syn_keep *) reg        [31:0]   '+DEST+' /* synthesis syn_keep = 1 */ ;'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def balanced(terms):
    if len(terms) == 1: return terms[0]
    split = len(terms)//2
    return '('+balanced(terms[:split])+' | '+balanced(terms[split:])+')'


def run(command, log, **kwargs):
    with log.open('w') as handle:
        subprocess.run(command, check=True, stdout=handle, stderr=subprocess.STDOUT, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ART/'integer-rs1-priority-groups-v1/immediate')
    args = ap.parse_args()
    assert sha(REF)==REF_SHA and sha(CHECK)==CHECK_SHA
    source=REF.read_text()
    producers=[(stage,lane) for stage in range(2,6) for lane in range(2)]
    data=[f'execute_ctrl{s}_down_lane{l}_integer_WriteBackPlugin_logic_DATA_lane{l}' for s,l in producers]
    data.append('execute_lane0_bypasser_integer_RS1_port_data')
    terms=[f"({SEL}[{i}] ? {word} : 32'h0)" for i,word in enumerate(data)]
    old=f'  always @(*) begin\n    {DEST} = {balanced(terms)};\n  end\n'
    assert source.count(old)==source.count(KEEP)==1
    enable_statements=[]
    for i,(stage,lane) in enumerate(producers):
        p=f'execute_ctrl{stage}_up_'; d=f'execute_ctrl{stage}_down_'
        line=f'    {EN}[{i}] = ((({p}LANE_SEL_lane{lane} && {p}RD_ENABLE_lane{lane}) && ({d}RD_PHYS_lane{lane} == execute_ctrl1_down_RS1_PHYS_lane0)) && ({d}RD_RFID_lane{lane} == execute_ctrl1_down_RS1_RFID_lane0));'
        assert source.count(line)==1
        enable_statements.append(line)
    fallback=f"    {EN}[8] = 1'b1;"
    assert source.count(fallback)==1
    enable_statements.append(fallback)
    early_terms=[f"({SEL}[{i}] ? {data[i]} : 32'h0)" for i in range(2)]
    late_terms=[]
    for i in range(2,9):
        enable=f'{EN}[{i}]' if i==2 else f'({EN}[{i}] && !(|{EN}[{i-1}:2]))'
        late_terms.append(f"({enable} ? {data[i]} : 32'h0)")
    decls=[f'  (* keep = "true" *) wire [31:0] {name};' for name in (EARLY,LATE)]
    new='\n'.join(decls)+'\n'+f'  assign {EARLY} = {balanced(early_terms)};\n'+f'  assign {LATE} = {balanced(late_terms)};\n'
    new+=f"  always @(*) begin\n    {DEST} = ({EARLY} | ((!(|{EN}[1:0])) ? {LATE} : 32'h0));\n  end\n"
    assert EARLY not in source and LATE not in source
    candidate=source.replace(old,new,1)
    assert candidate.replace(new,old,1)==source
    assert candidate.count(KEEP)==1
    seq=lambda s: re.findall(r'^  always @\(posedge clk.*?^  end',s,re.M|re.S)
    assert seq(candidate)==seq(source)
    # The sole consumer of the kept output remains the original operand alias.
    # Other selector consumers (including the original stage2 bypass indication)
    # retain their actual equations; only whole data-word masking is changed.
    consumer=f'  assign execute_ctrl1_down_integer_RS1_lane0 = {DEST};'
    assert source.count(consumer)==candidate.count(consumer)==1
    uses=[line for line in source.splitlines() if re.search(r'\b'+DEST+r'\b',line)]
    assert len(uses)==3 and KEEP in uses and consumer in uses
    assert source.count(f'  assign when_ExecuteLanePlugin_l196 = {SEL}[0];')==1
    for line in enable_statements: assert candidate.count(line)==1
    spec=importlib.util.spec_from_file_location('actual_cones',CHECK)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)
    def normalized(text):
        text=text.replace(KEEP,'  reg [31:0] '+DEST+';',1)
        for name,decl in zip((EARLY,LATE),decls):
            text=text.replace(decl,'  wire [31:0] '+name+';',1)
        return text
    inputs=[EN,*data]
    outputs=[SEL,DEST]
    reference,width=checker.cone(normalized(source),'reference',inputs,outputs)
    changed,new_width=checker.cone(normalized(candidate),'candidate',inputs,outputs)
    assert width==new_width==41
    # Assert the entire actual priority/selector cone is byte-identical,
    # independently of the data proof and all producer validity correlations.
    selector_ref,_=checker.cone(normalized(source),'selector',[EN],[SEL])
    selector_new,_=checker.cone(normalized(candidate),'selector',[EN],[SEL])
    assert selector_ref==selector_new
    wrapper='module integer_rs1_proof(input [8:0] enables,input [287:0] data,output [40:0] original,result);\n'
    for module,outname in [('reference','original'),('candidate','result')]:
        wrapper+=module+' '+module+'_dut(enables,'+','.join(f'data[{i*32+31}:{i*32}]' for i in range(9))+','+outname+');\n'
    wrapper+='endmodule\n'
    cpp=r'''#include "Vinteger_rs1_proof.h"
#include <cstdint>
#include <cstdio>
int main() {
 Vinteger_rs1_proof dut;
 uint32_t words[512][9]{};
 // Bit b sees all512 independent nine-source patterns over p. Only whole-word
 // conditional masks and OR exist in the guarded expressions: there is no
 // arithmetic or cross-bit operation, so this covers arbitrary32-bit words.
 for(unsigned p=0;p<512;++p) for(unsigned i=0;i<9;++i)
  for(unsigned b=0;b<32;++b) words[p][i]|=((p>>((i+b)%9))&1u)<<b;
 unsigned cases=0;
 for(unsigned e=0;e<512;++e) {
  unsigned selected=9;
  for(unsigned i=0;i<9;++i) if(e&(1u<<i)){selected=i;break;}
  for(unsigned p=0;p<512;++p) {
   dut.enables=e;
   for(unsigned i=0;i<9;++i)dut.data[i]=words[p][i];
   dut.eval();
   uint64_t expected=selected==9?0:(uint64_t(1u<<selected)<<32)|words[p][selected];
   if(dut.original!=dut.result || dut.result!=expected) {
    std::printf("FAIL e=%x p=%x old=%llx new=%llx expected=%llx\n",e,p,
     (unsigned long long)dut.original,(unsigned long long)dut.result,(unsigned long long)expected);
    return 1;
   }
   ++cases;
  }
 }
 std::printf("PASS all%u actual-cone enable/data-pattern cases; every32-bit output position independently exhausted, zero-selection and lowest-enabled oracle included\n",cases);
}
'''
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    for name,text in [('reference.v',reference),('candidate.v',changed),('selector.v',selector_ref),('proof.v',reference+changed+wrapper),('proof.cpp',cpp)]:
        (out/name).write_text(text)
    (out/'source-contracts.json').write_text(json.dumps(dict(producer_order=producers,
        actual_enable_statements=enable_statements,original_output_keep=KEEP,sole_output_consumer=consumer,
        original_selector_cone_unchanged=True,all_other_source_exact=True,
        groups=[[0,1],[2,3,4,5,6,7,8]],word_operations='Only complete32-bit word masks and OR; no data arithmetic, indexing or cross-bit operation.'),indent=2)+'\n')
    run(['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','integer_rs1_proof','--Mdir',str(out/'obj'),'-CFLAGS','-std=c++17 -O2',str(out/'proof.v'),str(out/'proof.cpp')],out/'proof-build.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)',(out/'proof-build.log').read_text())
    run([str(out/'obj/Vinteger_rs1_proof')],out/'proof.log')
    target=out/'VexiiRiscv.v';target.write_text(candidate)
    patch=out/'integer-rs1-priority-groups.patch'
    patch.write_text(''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
    replay=out/'patch-replay';replay.mkdir();(replay/target.name).write_text(candidate)
    for label,extra,expected in [('reverse',['-R'],source),('replay',[],candidate)]:
        run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(patch)],out/(label+'.log'),cwd=replay)
        assert (replay/target.name).read_text()==expected
    run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(target)],out/'lint.log')
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copyfile(__file__,out/Path(__file__).name);shutil.copyfile(CHECK,out/CHECK.name)
    manifest=dict(reference=str(REF.relative_to(ROOT)),reference_sha256=REF_SHA,candidate_sha256=sha(target),
        exact_scope='Only lane0 integerRS1 kept data block plus two combinational32-bit group boundaries.',
        groups=[[0,1],[2,3,4,5,6,7,8]],all_original_selectors_and_live_enable_gates_unchanged=True,
        original_output_keep_and_only_consumer_unchanged=True,all_sequential_source_unchanged=True,
        no_other_operand_or_source_changes=True,new_register_bits=0,new_cycles=0,new_kept_combinational_words=2,
        independent_enable_patterns=512,independent_source_bit_patterns=512,actual_cone_cases=262144,
        each32_output_bits_exhausted=True,bit_comparisons=8388608,arbitrary_zero_selection_preserved=True,
        independent_lowest_enabled_oracle=True,source_guard_reverse_replay=True,full_netlist_lint=True,
        whole_cpu_qualification='HELD pending focused proof and independent review; final14-case suite runs only when selected.',
        physical_limitation='Targets measured stage4 lane1 enable/index5 global-priority crossing; extra kept words may change placement/packing. No timing claim before fit.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps(dict(script_sha256=sha(out/Path(__file__).name),reference_sha256=REF_SHA,
        candidate_sha256=sha(target),checker_sha256=CHECK_SHA,patch_sha256=sha(patch),
        actual_cone_sha256={name:sha(out/name) for name in ('reference.v','candidate.v','selector.v')},
        proof_sha256=sha(out/'proof.v'),harness_sha256=sha(out/'proof.cpp')),indent=2)+'\n')
    (out/'README.txt').write_text('''Integer lane-zero RS1 local priority groups
==========================================

Base a083 retains qualified pointer/packer/RS3 changes. Only its kept integer
RS1 data block changes. The earliest two producers (stage2, lanes0/1) resolve
with the original selectors. Stages3-5 both lanes and RF resolve local priority
independently; their resulting word is masked once by absence of both early
enables. The original output KEEP and every live LANE_SEL/RD_ENABLE/PHYS/RFID
comparison, original selector and FF stay unchanged. Two kept combinational
word boundaries are added, with no register or cycle.

The actual extracted nine-way selector/data cones pass512 enable patterns ×
512 independent source-bit patterns, plus an independent lowest-enabled oracle.
Each output bit sees every pattern: guarded word-level masks/OR prove this
bitwise lift for arbitrary32-bit inputs. Zero-selection is preserved, even
though the actual RF fallback is normally enabled. The kept data output's only
consumer remains the original operand alias; all other source is exact.

This targets the measured stage4 lane1 RD_PHYS -> enable5 -> selector5 -> kept
SRC1 word path, six LUTs/83% routing. Physical benefit is unproven until fitting;
two extra kept words may affect scarce packing capacity. The fourteen CPU
cases are held until independent focused review, then recorded separately.

Reproduce with generator/factor_integer_rs1.py --output <new-directory>.
''')
    print((out/'proof.log').read_text(),end='');print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
