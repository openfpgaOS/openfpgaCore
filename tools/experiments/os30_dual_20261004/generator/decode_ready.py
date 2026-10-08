#!/usr/bin/env python3
"""Prove and emit a combinational late-input decode1-ready rewrite."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
REF=ART/'integer-rs2-v3/immediate-skid-fpu-tag/VexiiRiscv.v'
SHA='6bb303967bb5d669ffb87e267e88d981a443c38815e6f12c35dca0b52a177d1c'
P='DispatchPlugin_logic_'

def blocks(src):
    for start in re.finditer(r'^  always @\(\*\) begin\n',src,re.M):
        level=0
        for token in re.finditer(r'\bbegin\b|\bend\b',src[start.start():]):
            level+=1 if token[0]=='begin' else -1
            if level==0:
                yield src[start.start():start.start()+token.end()]+'\n'
                break

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ART/'decode-ready-v1/immediate')
    args=parser.parse_args()
    src=REF.read_text();assert hashlib.sha256(src.encode()).hexdigest()==SHA
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    def once(s):assert src.count(s)==1,(s,src.count(s))
    old='''  always @(*) begin
    decode_skid_gate_down_ready = decode_ctrls_1_up_ready;
    if(when_StageLink_l71_2) begin
      decode_skid_gate_down_ready = 1'b1;
    end
  end
'''
    once(old)
    # Pin the sole abstraction boundary used by the cone proof. The original
    # first arbiter's layer hits are exactly dispatch_available0, since both
    # execution units initially are free; no reachable-state assumption needed.
    old_avail=P+'scheduler_arbiters_0_layersHits'
    once('  assign '+P+'scheduler_eusFree_0 = 2\'b11;')
    once('  assign '+old_avail+' = ((('+P+'candidates_0_ctx_laneLayerHits & (~ '+P+'candidates_0_rsHazards)) & (~ '+P+'candidates_0_reservationHazards)) & {'+P+'scheduler_eusFree_0[0],'+P+'scheduler_eusFree_0[1]});')
    once('  assign dispatch_available0 = '+P+'candidates_0_ctx_laneLayerHits & ~'+P+'candidates_0_rsHazards & ~'+P+'candidates_0_reservationHazards;')
    new='''  wire decode_load_pending0;
  wire decode_load_pending1;
  wire decode_load_issue_ok;
  wire decode_load_allow1;
  assign decode_load_pending0 = decode_ctrls_3_up_LANE_SEL_0 && !DispatchPlugin_logic_feeds_0_sent;
  assign decode_load_pending1 = decode_ctrls_3_up_LANE_SEL_1 && !DispatchPlugin_logic_feeds_1_sent;
  assign decode_load_issue_ok = !execute_freeze_valid && !DispatchPlugin_api_haltDispatch;
  assign decode_load_allow1 = !DispatchPlugin_logic_candidates_1_flushHazards && !DispatchPlugin_logic_candidates_1_fenceOlderHazards && !DispatchPlugin_logic_scheduler_arbiters_1_candHazard;
  (* keep , syn_keep *) wire decode_load_without_lane0 /* synthesis syn_keep = 1 */;
  (* keep , syn_keep *) wire decode_load_needs_lane0 /* synthesis syn_keep = 1 */;
  assign decode_load_without_lane0 = !decode_ctrls_1_up_valid || !decode_ctrls_2_up_valid || !decode_ctrls_3_up_valid ||
      (!decode_load_pending0 && !decode_load_pending1) ||
      (decode_load_issue_ok &&
       ((decode_load_pending0 && dispatch_first_allowed && dispatch_available0[0] &&
         (!decode_load_pending1 || (decode_load_allow1 && dispatch_available1[1]))) ||
        (!decode_load_pending0 && decode_load_pending1 && decode_load_allow1 && (|dispatch_available1))));
  assign decode_load_needs_lane0 = decode_load_issue_ok && decode_load_pending0 && dispatch_first_allowed &&
      (!decode_load_pending1 || (decode_load_allow1 && !dispatch_available0[0] && dispatch_available1[0]));
  always @(*) begin
    decode_skid_gate_down_ready = decode_load_without_lane0 || (dispatch_available0[1] && decode_load_needs_lane0);
  end
'''
    candidate=src.replace(old,new,1);assert candidate.replace(new,old,1)==src
    # Extract the entire original ready cone, stopping only at registered
    # frame/pending flags, existing hazard flags and the unchanged availability
    # outputs. Exhaust all18 input bits, including invalid-frame/stale-lane cases.
    decl={m[3]:(m[1],(m[2] or '').strip()) for m in re.finditer(r'^  (?:\(\*[^\n]*\*\)\s+)?(wire|reg)\s+(\[\d+:0\]\s+)?(\w+);$',src,re.M)}
    inputs=['decode_ctrls_1_up_valid','decode_ctrls_2_up_valid','decode_ctrls_3_up_valid',
            'decode_ctrls_3_up_LANE_SEL_0','decode_ctrls_3_up_LANE_SEL_1',
            P+'feeds_0_sent',P+'feeds_1_sent',P+'candidates_0_flushHazards',P+'candidates_1_flushHazards',
            P+'candidates_0_fenceOlderHazards',P+'candidates_1_fenceOlderHazards',
            P+'scheduler_arbiters_1_candHazard','execute_freeze_valid','DispatchPlugin_api_haltDispatch',
            'dispatch_available0','dispatch_available1']
    stops=set(inputs+[old_avail]);all_blocks=list(blocks(src));drivers={};names=set(inputs+[old_avail]);pending=['decode_skid_gate_down_ready','dispatch_first_allowed']
    while pending:
        name=pending.pop()
        if name in stops or name in drivers:continue
        matches=re.findall(r'^  assign '+re.escape(name)+r' = [^;]+;\n',src,re.M)
        matches += [b for b in all_blocks if re.search(r'\b'+re.escape(name)+r'(?:\[[^\n]+?\])?\s*=',b)]
        assert len(matches)==1,(name,len(matches))
        block=matches[0]
        written=set(re.findall(r'^\s*(?:assign )?(\w+)(?:\[[^\n]+?\])?\s*=',block,re.M))
        assert not written&stops
        for output in written:drivers[output]=block
        deps=set(re.findall(r'\b\w+\b',block))&set(decl)
        names|=deps|written;pending+=list(deps-written)
    ports=['input wire '+decl[n][1]+' '+n for n in inputs]+['output wire old_ready','output wire new_ready']
    design='module cone(\n'+',\n'.join(ports)+'\n);\n'
    for n in sorted(names-set(inputs)):
        kind,width=decl[n];design+=f'{kind} {width} {n};\n'
    design+='assign '+old_avail+'=dispatch_available0;\n'
    original_blocks=sorted(set(drivers.values()))
    design+='\n'.join(original_blocks)
    design+='assign old_ready=decode_skid_gate_down_ready;\n'
    new_logic=new.replace('decode_skid_gate_down_ready','new_load_ready')
    design+='reg new_load_ready;\n'+new_logic+'assign new_ready=new_load_ready;\nendmodule\n'
    tb='module tb;\n'
    for n in inputs:tb+='logic '+decl[n][1]+' '+n+';\n'
    tb+='wire a,b; cone dut('+','.join('.'+n+'('+n+')' for n in inputs)+',.old_ready(a),.new_ready(b));\n'
    tb+='initial begin for(integer i=0;i<(1<<18);i++)begin\n'
    bit=0
    for n in inputs:
        width=2 if n.startswith('dispatch_available') else 1
        tb+=f'{n}=i[{bit+width-1}:{bit}];\n';bit+=width
    assert bit==18
    tb+='#1;if(a!==b)$fatal(1,"Ready mismatch inputs=%x old=%b new=%b",i,a,b);\nend\n$display("PASS: actual original ready cone equals direct late-input expression for all262144 input combinations");$finish;end endmodule\n'
    (out/'tb.sv').write_text(design+tb)
    with (out/'build.log').open('w') as f:
        subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal','--Mdir',str(out/'obj'),'-j','2',str(out/'tb.sv')],check=True,stdout=f,stderr=subprocess.STDOUT)
    with (out/'proof.log').open('w') as f:subprocess.run([str(out/'obj/Vtb')],check=True,stdout=f,stderr=subprocess.STDOUT)
    (out/'VexiiRiscv.v').write_text(candidate);shutil.copy2(__file__,out/'decode_ready.py')
    manifest=dict(reference=str(REF.relative_to(ROOT)),reference_sha256=SHA,candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                  only_changed_behavioral_cone='decode_skid_gate_down_ready, cycle-equivalent',new_register_bits=0,pipeline_cycles_added=0,
                  preserved_early_boolean_terms=2,exhaustive_input_states=1<<18,original_cone_blocks=len(original_blocks),
                  all_other_rtl_exactly_unchanged=True,whole_cpu_validation='pending',
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print((out/'proof.log').read_text());print(json.dumps(manifest,indent=2))

if __name__=='__main__':main()
