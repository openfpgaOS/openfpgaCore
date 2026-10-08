#!/usr/bin/env python3
"""Private exact-source RS2 match/write-enable retiming, with extracted proof."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[4]
ART=ROOT/'docs/measurements/os30-dual-20261004'
REF=ART/'tag-select-v1/full-skid-fpu/VexiiRiscv.v'
SHA='6e2ab04eb9859aa36c5af5087f010e0a1621b46d61d7391475cc5a6d21dd7093'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',choices=['full-alu','immediate','address'],default='full-alu')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    reference,reference_sha=REF,SHA
    default_output=ART/'integer-rs2-v2/full-skid-fpu-tag'
    if args.profile=='immediate':
        reference=ART/'tag-select-v2/immediate-skid-fpu/VexiiRiscv.v'
        reference_sha='44abc261550d4b1da3986bf9889911f2d942d1a9f64f98cfcc3ad57cf1de6d10'
        default_output=ART/'integer-rs2-v3/immediate-skid-fpu-tag'
    if args.profile=='address':
        reference=ART/'tag-select-v3/address-skid-fpu/VexiiRiscv.v'
        reference_sha='9c1cbad163f1376d2ec247a5d4a9ffb42323760676bb4369b33b5c37e4ab81f8'
        default_output=ART/'integer-rs2-v4/address-skid-fpu-tag'
    src=reference.read_text()
    assert hashlib.sha256(src.encode()).hexdigest()==reference_sha
    def once(s):
        assert src.count(s)==1, ('source contract',s,src.count(s))
    for stage in range(12):
        once(f'  assign execute_ctrl{stage}_down_ready = execute_ctrl{stage+1}_up_ready;')
        once(f'  assign execute_ctrl{stage}_down_isReady = execute_ctrl{stage}_down_ready;')
        once(f'  assign execute_ctrl{stage}_up_ready = execute_ctrl{stage}_down_isReady;')
    fields=[]
    for stage in range(2,6):
        for lane in range(2):
            for field in ['RD_PHYS','RD_RFID','RD_ENABLE']:
                fields.append((stage,lane,field))
    fields += [(1,0,'RS2_PHYS'),(1,0,'RS2_RFID')]
    registers=[]
    for stage,lane,field in fields:
        q=f'execute_ctrl{stage}_up_{field}_lane{lane}'
        d=f'execute_ctrl{stage-1}_down_{field}_lane{lane}'
        stmt=f'      {q} <= {d};'
        once(stmt)
        assert len(re.findall(r'\b'+q+r'\s*<=',src))==1
        blocks=re.findall(r'    if\(execute_ctrl'+str(stage-1)+r'_down_isReady\) begin\n((?:      [^\n]*\n)+)    end',src)
        matches=[b for b in blocks if stmt in b]
        assert len(matches)==1
        assert all(re.fullmatch(r'      \w+ <= [^;]+;',s) for s in matches[0].splitlines())
        # RD_ENABLE has a live cancellation bypass on down. The ORIGINAL match
        # consumes UP_RD_ENABLE; capture its exact previous DOWN source instead.
        if field!='RD_ENABLE':
            once(f'  assign execute_ctrl{stage}_down_{field}_lane{lane} = {q};')
        decl=re.search(r'^  reg\s+(\[\d+:\d+\]\s+)?'+q+r';$',src,re.M)
        assert decl
        registers.append((q,d,(decl[1] or '').strip()))
    capture=[]
    changes=[]
    for stage in range(2,6):
        for lane in range(2):
            index=(stage-2)*2+lane
            name=f'int_rs2_write_match_{stage}_{lane}'
            capture += [f'  reg {name};',
                        '  always @(posedge clk) if(execute_ctrl0_down_isReady)',
                        f'    {name} <= execute_ctrl{stage-1}_down_RD_ENABLE_lane{lane} && (execute_ctrl{stage-1}_down_RD_PHYS_lane{lane} == execute_ctrl0_down_RS2_PHYS_lane0) && (execute_ctrl{stage-1}_down_RD_RFID_lane{lane} == execute_ctrl0_down_RS2_RFID_lane0);']
            lhs=f'execute_lane0_bypasser_integer_RS2_bypassEnables[{index}]'
            old=f'    {lhs} = (((execute_ctrl{stage}_up_LANE_SEL_lane{lane} && execute_ctrl{stage}_up_RD_ENABLE_lane{lane}) && (execute_ctrl{stage}_down_RD_PHYS_lane{lane} == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl{stage}_down_RD_RFID_lane{lane} == execute_ctrl1_down_RS2_RFID_lane0));'
            new=f'    {lhs} = execute_ctrl{stage}_up_LANE_SEL_lane{lane} && {name};'
            once(old)
            changes.append((old,new))
    block='\n'.join(capture)+'\n'
    anchor='  assign execute_lane0_bypasser_integer_RS2_port_valid ='
    once(anchor)
    candidate=src.replace(anchor,block+anchor,1)
    for old,new in changes:
        candidate=candidate.replace(old,new,1)
    restored=candidate.replace(block,'',1)
    for old,new in changes:
        restored=restored.replace(new,old,1)
    assert restored==src
    out=(args.output or default_output).resolve()
    out.mkdir(parents=True,exist_ok=False)
    (out/'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__,out/'integer_rs2.py')

    # Each old ID/enable flop is copied from the actual generated source. Its
    # previous-down capture value is unconstrained, covering cancellation before
    # the edge. Current lane select remains unconstrained and live, including
    # changes during a freeze. Renaming separates these arbitrary capture inputs
    # from the original pipeline's additional correlations; this is stronger
    # than exercising only reachable instruction streams for these equations.
    tb='module tb; reg clk=0; reg ready; wire execute_ctrl0_down_isReady=ready;\n'
    old_names={}
    input_names={}
    randoms=[]
    updates=[]
    for q,d,width in registers:
        input_names[d]='in_'+d
        old_names[q]='old_'+q
        if '_RD_ENABLE_' not in q:
            old_names[q.replace('_up_','_down_')]='old_'+q
        tb+=f'reg {width} old_{q}; reg {width} in_{d};\n'
        updates.append(f'old_{q} <= in_{d};')
        randoms.append(f'in_{d}=$random;')
    def renamed(s,names):
        return re.sub(r'\b\w+\b',lambda m:names.get(m[0],m[0]),s)
    tb+='always @(posedge clk) if(ready) begin\n'+'\n'.join(updates)+'\nend\n'
    tb+=renamed(block,input_names)
    checks=[]
    lane_random=[]
    for old,new in changes:
        gate=re.search(r'execute_ctrl\d+_up_LANE_SEL_lane\d',old)[0]
        tb+='reg '+gate+';\n'
        lane_random.append(gate+'=$random;')
        old_expr=renamed(old.split(' = ',1)[1].rstrip(';'),old_names)
        new_expr=new.split(' = ',1)[1].rstrip(';')
        checks.append(f'if(({old_expr}) !== ({new_expr})) $fatal(1,"Mismatch {gate}");')
    tb+='initial begin\nfor(integer i=0;i<1000000;i++) begin\nready=(i==0 || ($random & 3)!=0);\n'
    tb+='\n'.join(randoms+lane_random)+'\n#1; clk=1; #1;\n'+'\n'.join(checks)
    tb+='\nclk=0;\n'+'\n'.join(lane_random)+'\n#1;\n'+'\n'.join(checks)
    tb+='\nend\n$display("PASS: 8 actual RS2 match/write-enable cones, 1000000 cycles with freezes, arbitrary previous-down suppression and live lane cancellation");\n$finish; end endmodule\n'
    (out/'tb.sv').write_text(tb)
    with (out/'build.log').open('w') as log:
        subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal','--Mdir',str(out/'obj'),'-j','2',str(out/'tb.sv')],check=True,stdout=log,stderr=subprocess.STDOUT)
    with (out/'run.log').open('w') as log:
        subprocess.run([str(out/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
    manifest=dict(profile=args.profile,reference=str(reference.relative_to(ROOT)),reference_sha256=reference_sha,
                  candidate_sha256=hashlib.sha256(candidate.encode()).hexdigest(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  added_register_bits=8,extra_pipeline_cycles=0,
                  guarded_original_capture_registers=len(registers),
                  all_other_rtl_exactly_unchanged=True,shared_execute_advance_guarded=True,
                  previous_down_write_enable_captured=True,current_lane_valid_unchanged=True,
                  original_priority_data_mux_and_keep_unchanged=True,
                  extracted_equivalence_passed=True,whole_cpu_validation='pending',
                  reset_scope='No new reset is needed: current original lane validity gates uninitialized payload matches, as it gates the original uninitialized IDs.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print((out/'run.log').read_text())
    print(json.dumps(manifest,indent=2))

if __name__=='__main__':
    main()
