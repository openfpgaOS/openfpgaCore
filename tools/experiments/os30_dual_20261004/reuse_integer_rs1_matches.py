#!/usr/bin/env python3
"""Reuse two existing sampled RS1 equalities without adding any state."""
from pathlib import Path
import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'pointer-packer-combined-v1/immediate/VexiiRiscv.v'
BASE_SHA='5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
S0='_zz_execute_ctrl0_up_LANE_AGE_lane0'
S1='_zz_execute_ctrl0_up_LANE_AGE_lane0_1'

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=ART/'integer-rs1-reuse-v1/immediate')
    out=ap.parse_args().output.resolve()
    assert sha(BASE)==BASE_SHA
    source=BASE.read_text()
    def once(text):
        assert source.count(text)==1,(text,source.count(text))
        return text
    contracts=[]
    # Every execute stage has one common advance, so both source ID and each
    # producer ID are sampled together with the already-existing equality FF.
    for stage in range(12):
        for text in [f'  assign execute_ctrl{stage}_down_ready = execute_ctrl{stage+1}_up_ready;',
                     f'  assign execute_ctrl{stage}_down_isReady = execute_ctrl{stage}_down_ready;',
                     f'  assign execute_ctrl{stage}_up_ready = execute_ctrl{stage}_down_isReady;']:
            contracts.append(once(text))
    aliases=[]
    for i,s in enumerate([S0,S1]):
        aliases.append(once(f'  assign {s} = DispatchPlugin_logic_inserter_0_oh[{i}];'))
    for field,zero in [('PHYS',"5'h0"),('RFID',"1'b0")]:
        aliases.append(once(f'  assign execute_ctrl0_up_RS1_{field}_lane0 = (({S0} ? DispatchPlugin_logic_candidates_0_ctx_hm_RS1_{field} : {zero}) | ({S1} ? DispatchPlugin_logic_candidates_1_ctx_hm_RS1_{field} : {zero}));'))
        aliases.append(once(f'  assign execute_ctrl0_down_RS1_{field}_lane0 = execute_ctrl0_up_RS1_{field}_lane0;'))
        aliases.append(once(f'  assign execute_ctrl1_down_RS1_{field}_lane0 = execute_ctrl1_up_RS1_{field}_lane0;'))
    captures=[]
    for stage,field in [(1,'RS1_PHYS'),(1,'RS1_RFID'),(4,'RD_PHYS'),(4,'RD_RFID'),(5,'RD_PHYS'),(5,'RD_RFID')]:
        q=f'execute_ctrl{stage}_up_{field}_lane0';d=f'execute_ctrl{stage-1}_down_{field}_lane0'
        stmt=once(f'      {q} <= {d};')
        assert len(re.findall(r'\b'+q+r'\s*<=',source))==1
        pattern=r'    if\(execute_ctrl'+str(stage-1)+r'_down_isReady\) begin\n((?:      [^\n]*\n)+)    end'
        assert sum(stmt in x for x in re.findall(pattern,source))==1
        captures.append(stmt.strip())
        if stage!=1:
            aliases.append(once(f'  assign execute_ctrl{stage}_down_{field}_lane0 = {q};'))
    # Lane gates reset to zero and can become true only on that same advance.
    lane_contracts=[]
    for stage in [4,5]:
        name=f'execute_ctrl{stage}_up_LANE_SEL_lane0'
        writes=re.findall(r'\b'+name+r'\s*<=\s*[^;]+;',source)
        assert writes==[f"{name} <= 1'b0;",f'{name} <= execute_ctrl{stage-1}_down_LANE_SEL_lane0;']
        lane_contracts.extend(writes)
        lane_block=re.search(r'    if\(execute_ctrl'+str(stage-1)+r'_down_isReady\) begin\n((?:      [^\n]*\n)+)    end',source)
        # The lane controls are inside the reset-aware block at a different
        # indentation from the unreset payload capture.
        assert f'      if(execute_ctrl{stage-1}_down_isReady) begin\n        {name} <= execute_ctrl{stage-1}_down_LANE_SEL_lane0;' in source
    reset_blocks=re.findall(r'^  always @\(posedge clk or posedge reset\) begin.*?^  end',source,re.M|re.S)
    reset_block=[b for b in reset_blocks if lane_contracts[0] in b]
    assert len(reset_block)==1
    reset_block=reset_block[0]
    assert reset_block.startswith('  always @(posedge clk or posedge reset) begin\n    if(reset) begin')
    for line in lane_contracts[::2]:assert line in reset_block.split('    end else begin',1)[0]

    eq_wires=[];match_blocks=[];changes=[]
    for stage,index in [(4,4),(5,6)]:
        for c in range(2):
            name=f'fp_preselect_RS1_{stage}_candidate{c}'
            eq_wires.append(once(f'  (* keep , syn_keep *) wire {name} /* synthesis syn_keep = 1 */;'))
            eq_wires.append(once(f'  assign {name} = (execute_ctrl{stage-1}_down_RD_PHYS_lane0 == DispatchPlugin_logic_candidates_{c}_ctx_hm_RS1_PHYS) && (execute_ctrl{stage-1}_down_RD_RFID_lane0 == DispatchPlugin_logic_candidates_{c}_ctx_hm_RS1_RFID);'))
        block=re.findall(r'  reg fp_match_RS1_'+str(stage)+r';\n  always @\(posedge clk\) if\(execute_ctrl0_down_isReady\)\n    fp_match_RS1_'+str(stage)+r' <= [^;]+;',source)
        assert len(block)==1
        assert len(re.findall(r'\bfp_match_RS1_'+str(stage)+r'\s*<=',source))==1
        match_blocks.append(block[0])
        old=f'    execute_lane0_bypasser_integer_RS1_bypassEnables[{index}] = (((execute_ctrl{stage}_up_LANE_SEL_lane0 && execute_ctrl{stage}_up_RD_ENABLE_lane0) && (execute_ctrl{stage}_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl{stage}_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));'
        new=f'    execute_lane0_bypasser_integer_RS1_bypassEnables[{index}] = (execute_ctrl{stage}_up_LANE_SEL_lane0 && execute_ctrl{stage}_up_RD_ENABLE_lane0) && fp_match_RS1_{stage};'
        changes.append((once(old),new))
    candidate=source
    for a,b in changes:candidate=candidate.replace(a,b,1)
    restored=candidate
    for a,b in changes:restored=restored.replace(b,a,1)
    assert restored==source
    # No FF declarations, capture/input functions, valid/write-enable gates,
    # priority, data mux, cycle count or reset behavior are changed.
    out.mkdir(parents=True,exist_ok=False)
    (out/'VexiiRiscv.v').write_text(candidate)
    (out/'source-contracts.json').write_text(json.dumps(dict(advance_aliases=contracts,id_aliases=aliases,
        payload_captures=captures,lane_reset_capture=lane_contracts,equality_wires=eq_wires,
        existing_match_blocks=match_blocks,changes=changes),indent=2)+'\n')
    checker=ROOT/'tools/check_vexii_os20_timing.py'
    spec=importlib.util.spec_from_file_location('cones',checker);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    plain=source.replace('  (* maxfan = 16 *) wire execute_freeze_valid;','  wire execute_freeze_valid;',1)
    dispatch=mod.dispatch_tb(plain,plain)
    prior=ART/'match-preselect-all-v1/immediate/dispatch'
    assert dispatch==(prior/'tb.sv').read_text()
    (out/'actual-dispatch-tb.sv').write_text(dispatch)
    with (out/'dispatch-rerun.log').open('w') as log:
        subprocess.run([str(prior/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)

    # Exact current capture functions and old/new enable expressions, with
    # producer/candidate IDs unrestricted and the proved zero/one-hot selector.
    tb='''module reuse_proof(input clk,reset,ready,input [1:0] selection,
input [5:0] candidate0,candidate1,producer3,producer4,
input [1:0] incoming_lane,current_rd_enable,output [1:0] original,result,
output [3:0] capture_matches);
wire execute_ctrl0_down_isReady=ready;
wire [1:0] DispatchPlugin_logic_inserter_0_oh=selection;
wire '''+S0+','+S1+';\n'
    for c in range(2):
        tb+=f'wire [4:0] DispatchPlugin_logic_candidates_{c}_ctx_hm_RS1_PHYS=candidate{c}[4:0];\nwire DispatchPlugin_logic_candidates_{c}_ctx_hm_RS1_RFID=candidate{c}[5];\n'
    for stage in [3,4]:
        tb+=f'wire [4:0] execute_ctrl{stage}_down_RD_PHYS_lane0=producer{stage}[4:0];\nwire execute_ctrl{stage}_down_RD_RFID_lane0=producer{stage}[5];\n'
    # Current producer down aliases for stages4/5 would collide with previous
    # input names; rename only captured/current aliases in observation terms.
    capture_names={}
    for stage,field in [(1,'RS1_PHYS'),(1,'RS1_RFID'),(4,'RD_PHYS'),(4,'RD_RFID'),(5,'RD_PHYS'),(5,'RD_RFID')]:
        q=f'execute_ctrl{stage}_up_{field}_lane0';w='[4:0]' if field.endswith('PHYS') else ''
        tb+=f'reg {w} {q};\n'
        capture_names[f'execute_ctrl{stage}_down_{field}_lane0']=q
    for field,width in [('PHYS','[4:0]'),('RFID','')]:
        for side in ['up','down']:tb+=f'wire {width} execute_ctrl0_{side}_RS1_{field}_lane0;\n'
    tb+='\n'.join(a for a in aliases if 'execute_ctrl1_down_' not in a and not re.search(r'execute_ctrl[45]_down_RD',a))+'\n'
    tb+='\n'.join(eq_wires+match_blocks)+'\n'
    tb+='always @(posedge clk) if(ready) begin\n'+'\n'.join(captures)+'\nend\n'
    for offset,stage in enumerate([4,5]):
        name=f'execute_ctrl{stage}_up_LANE_SEL_lane0'
        tb+=f'reg {name};\nwire execute_ctrl{stage}_up_RD_ENABLE_lane0=current_rd_enable[{offset}];\n'
        tb+=f'always @(posedge clk or posedge reset) if(reset) {name}<=0; else if(ready) {name}<=incoming_lane[{offset}];\n'
        a,b=changes[offset]
        expr=a.split(' = ',1)[1].rstrip(';')
        expr=re.sub(r'\b\w+\b',lambda m:capture_names.get(m[0],m[0]),expr)
        tb+=f'assign original[{offset}]={expr};\nassign result[{offset}]={b.split(" = ",1)[1]}\n'
        fp_rhs=match_blocks[offset].split(' <= ',1)[1].rstrip(';')
        expected=f'(execute_ctrl{stage-1}_down_RD_PHYS_lane0 == execute_ctrl0_down_RS1_PHYS_lane0) && (execute_ctrl{stage-1}_down_RD_RFID_lane0 == execute_ctrl0_down_RS1_RFID_lane0)'
        tb+=f'assign capture_matches[{offset*2}]={fp_rhs};\nassign capture_matches[{offset*2+1}]={expected};\n'
    tb+='endmodule\n'
    (out/'proof.v').write_text(tb)
    cpp=r'''#include "Vreuse_proof.h"
#include <cstdio>
#include <cstdint>
uint32_t rng=0x56492571;
uint32_t next(){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
void edge(Vreuse_proof& d){d.clk=0;d.eval();d.clk=1;d.eval();d.clk=0;d.eval();}
bool equal(Vreuse_proof& d){return d.original==d.result;}
int main(){Vreuse_proof d;unsigned cases=0;
 for(unsigned rd=0;rd<64;++rd)for(unsigned a=0;a<64;++a)for(unsigned b=0;b<64;++b)for(unsigned s=0;s<3;++s){
  d.candidate0=a;d.candidate1=b;d.producer3=rd;d.producer4=rd;d.selection=s;d.eval();
  if(((d.capture_matches>>0)&1)!=((d.capture_matches>>1)&1)||((d.capture_matches>>2)&1)!=((d.capture_matches>>3)&1)){std::puts("FAIL capture input");return 1;}++cases;
 }
 // Force asynchronous reset before any advance: arbitrary unmatched startup
 // payload is unobservable while both actual producer lane gates are zero.
 d.ready=0;d.reset=0;d.eval();d.reset=1;d.eval();if(!equal(d))return 2;
 d.reset=0;d.eval();for(int i=0;i<8;++i){edge(d);if(!equal(d))return 3;}
 unsigned stalls=0,resets=0;
 for(unsigned i=0;i<1000000;++i){
  d.reset=(i%107)==0;d.ready=(next()&3)!=0;d.selection=next()%3;
  d.candidate0=next()&63;d.candidate1=next()&63;d.producer3=next()&63;d.producer4=next()&63;
  d.incoming_lane=next()&3;d.current_rd_enable=next()&3;edge(d);
  if(!equal(d)){std::printf("FAIL sampled cycle %u\n",i);return 4;}
  // Current write-enable/cancellation visibility can change during a stall;
  // the exact same live gate remains outside the reused equality in both.
  d.current_rd_enable=next()&3;d.eval();if(!equal(d))return 5;
  stalls+=!d.ready;resets+=d.reset;
 }
 std::printf("PASS %u exhaustive input-equivalence cases per sampled match; 1000000 exact-source sampling cycles; stalls=%u resets=%u; live qualification and pre-first-advance reset covered\n",cases,stalls,resets);
}
'''
    (out/'proof.cpp').write_text(cpp)
    for label,cmd in [('build',['verilator','--cc','--exe','--build','-j','2','-Wno-fatal','--top-module','reuse_proof','--Mdir',str(out/'obj'),'-CFLAGS','-std=c++17 -O2',str(out/'proof.v'),str(out/'proof.cpp')]),('proof',[str(out/'obj/Vreuse_proof')])]:
        with (out/(label+'.log')).open('w') as log:subprocess.run(cmd,check=True,stdout=log,stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'build.log').read_text())
    patch=''.join(difflib.unified_diff(source.splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v'))
    (out/'integer-rs1-reuse.patch').write_text(patch)
    replay=out/'patch-replay';replay.mkdir();(replay/'VexiiRiscv.v').write_text(candidate)
    for name,extra,expected in [('reverse',['-R'],source),('replay',[],candidate)]:
        with (out/(name+'.log')).open('w') as log:subprocess.run(['patch','--batch','--fuzz=0',*extra,'-p1','-i',str(out/'integer-rs1-reuse.patch')],cwd=replay,check=True,stdout=log,stderr=subprocess.STDOUT)
        assert (replay/'VexiiRiscv.v').read_text()==expected
    with (out/'lint.log').open('w') as log:subprocess.run(['verilator','--lint-only','--top-module','VexiiRiscv','-Wno-fatal',str(out/'VexiiRiscv.v')],check=True,stdout=log,stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH|MULTIDRIVEN)',(out/'lint.log').read_text())
    shutil.copy2(__file__,out/Path(__file__).name)
    manifest=dict(reference_sha256=BASE_SHA,candidate_sha256=sha(out/'VexiiRiscv.v'),exact_scope='Only two integer RS1 bypass equality RHS terms reuse existing fp_match_RS1_4/5.',
        added_register_bits=0,added_cycles=0,all_sequential_bytes_unchanged=True,all_current_LANE_SEL_and_RD_ENABLE_gates_preserved=True,
        original_priority_and_data_mux_unchanged=True,all_capture_enables_proved_identical_by_aliases=True,
        all_capture_input_functions_exhausted_cases=786432,actual_dispatch_selector_cases=2097152,sampling_cycles=1000000,
        reset_and_before_first_advance_guarded=True,stalls_and_live_qualification_exercised=True,
        exact_reverse_replay=True,lint_pass=True,whole_cpu_validation='Pending independent emitted audit and retained14 fixtures.',physical_qualification='Requires complete-core fit; no area or timing gain assumed.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (out/'recipe.json').write_text(json.dumps({'script_sha256':sha(out/Path(__file__).name),'source_sha256':BASE_SHA,'candidate_sha256':sha(out/'VexiiRiscv.v'),'evidence':{n:sha(out/n) for n in ['source-contracts.json','proof.v','proof.cpp','proof.log','actual-dispatch-tb.sv','dispatch-rerun.log','integer-rs1-reuse.patch']}},indent=2)+'\n')
    print((out/'proof.log').read_text()+json.dumps(manifest,indent=2))

if __name__=='__main__':main()
