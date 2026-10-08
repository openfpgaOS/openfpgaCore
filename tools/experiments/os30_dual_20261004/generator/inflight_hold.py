#!/usr/bin/env python3
"""Factor only the aggregate inflight trap contribution of the held skid."""
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
ART = ROOT/'docs/measurements/os30-dual-20261004'
REF = ART/'raw-normalize-v1/immediate/VexiiRiscv.v'
SHA = '06c797895939e09a2a63a0eb921f2ae73beb8e8e55ef4ac98521c8c75f65d048'
CHECK = ROOT/'tools/check_vexii_os20_timing.py'
CHECK_SHA = '80a0c0e9cee4d3f7532c44d4aca198becc46c2400ebc8bfba811bd61e10f6188'
F = 'LsuPlugin_logic_flushPort_valid'
E = 'execute_lane0_logic_trapPending'
D = 'decode_logic_trapPending'
H = 'TrapPlugin_logic_harts_0_trap_fsm_inflightTrap'
Q = 'decode_hold_buffered_trap'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def prove(out, name, tb):
    directory=out/name; directory.mkdir()
    (directory/'tb.sv').write_text(tb)
    with (directory/'build.log').open('w') as log:
        subprocess.run(['verilator','--binary','--timing','--top-module','tb','-Wno-fatal',
                        '--Mdir',str(directory/'obj'),'-j','2',str(directory/'tb.sv')],
                       check=True,stdout=log,stderr=subprocess.STDOUT)
    assert not re.search(r'%Warning-(IMPLICIT|UNDRIVEN|LATCH)',(directory/'build.log').read_text())
    with (directory/'proof.log').open('w') as log:
        subprocess.run([str(directory/'obj/Vtb')],check=True,stdout=log,stderr=subprocess.STDOUT)
    print((directory/'proof.log').read_text(),end='',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ART/'inflight-hold-v1/immediate')
    args=parser.parse_args()
    src=REF.read_text(); assert sha(src.encode())==SHA
    assert sha(CHECK.read_bytes())==CHECK_SHA

    def once(text):
        assert src.count(text)==1,(text,src.count(text))

    # The existing decode aggregate remains present and unchanged. Its sole
    # consuming aggregate is the only assignment replaced by this transform.
    old=f'  assign {H} = (|{{execute_lane1_logic_trapPending[0],{{{E}[0],{{DispatchPlugin_logic_trapPendings[0],{D}[0]}}}}}});'
    once(old)
    assert len(re.findall(r'\b'+D+r'\b',src))==3
    decl=re.findall(r'^  assign '+D+r'\[0\] = \((.+) \|\| decode_logic_skid_trapPending\);$',src,re.M)
    assert len(decl)==1
    ordinary_decode=decl[0]
    non_lsu=['DecoderPlugin_logic_laneLogic_1_flushPort_valid','DecoderPlugin_logic_laneLogic_0_flushPort_valid',
             'early0_EnvPlugin_logic_flushPort_valid','CsrAccessPlugin_logic_flushPort_valid',
             'early0_BranchPlugin_logic_flushPort_valid']
    new_aggregate=old.replace(D+'[0]',ordinary_decode)[:-1]+' || '+Q+';'
    new=f'''  wire {Q};
  assign {Q} = decode_skid_rValid && !(|{{{','.join(non_lsu)}}}) &&
      ((decode_ctrls_0_down_LANE_SEL_0_s2mBuffer && decode_ctrls_0_down_TRAP_0_s2mBuffer) ||
       (decode_ctrls_0_down_LANE_SEL_1_s2mBuffer && decode_ctrls_0_down_TRAP_1_s2mBuffer));
{new_aggregate}'''
    assert not re.search(r'\b'+Q+r'\b',src)
    candidate=src.replace(old,new,1)
    assert candidate.replace(new,old,1)==src
    # In particular no actual skid occupancy/validity/throw/cancel/lane-mask
    # assignment changes. Only the aggregate's held-payload accounting changes.

    spec=importlib.util.spec_from_file_location('cone_checker',CHECK)
    checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)

    def scalar_assignment(text,name):
        original='  assign '+name+'[0] = '
        assert text.count(original)==1
        return text.replace(original,'  assign '+name+' = ',1)

    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    # First independent proof: exact raw LSU flush implies exact execute pending
    # for all inputs, without assuming state reachability or a pipeline invariant.
    ep=['execute_ctrl4_up_LANE_SEL_lane0','execute_ctrl4_down_AguPlugin_SEL_lane0',
        'LsuPlugin_logic_onCtrl_lsuTrap','execute_ctrl4_up_TRAP_lane0']
    ep += [f'execute_ctrl{stage}_{side}_{field}_lane0' for stage in range(1,4)
           for side,field in [('up','LANE_SEL'),('down','TRAP')]]
    actual,width=checker.cone(scalar_assignment(src,E),'execute_pending',ep,[F,E]);assert width==2
    tb=actual+'\nmodule tb;\n'+'\n'.join('reg '+n+';' for n in ep)+'\nwire [1:0] observed;\n'
    tb+='execute_pending a('+','.join(ep+['observed'])+');\ninitial begin for(integer i=0;i<1024;i++)begin\n'
    tb+='{'+','.join(ep)+"}=10'(i);#1;if(observed[1]&&!observed[0])$fatal(1,\"LSU flush lacks execute pending: %x\",i);\n"
    tb+='end $display("PASS: actual LSU flush implies execute pending for all1024 source inputs");$finish;end endmodule\n'
    prove(out,'lsu-implication',tb)

    # Establish the common OR decomposition from the entire actual decode
    # aggregate. This validates using its stage0 contribution to absorb the
    # empty-skid bypass term while all other contributions are left identical.
    dp=[f'decode_ctrls_{stage}_{side}_{field}_{lane}' for stage in range(4) for lane in range(2)
        for side,field in [('up','LANE_SEL'),('down','TRAP')]]
    dp.append('decode_logic_skid_trapPending')
    actual,width=checker.cone(scalar_assignment(src,D),'decode_pending',dp,[D]);assert width==1
    tb=actual+'\nmodule tb;\n'+'\n'.join('reg '+n+';' for n in dp)+'\nwire observed,expected;\n'
    terms=[f'(decode_ctrls_{stage}_up_LANE_SEL_{lane} && decode_ctrls_{stage}_down_TRAP_{lane})'
           for stage in range(4) for lane in range(2)]
    tb+='assign expected='+ ' || '.join(terms+['decode_logic_skid_trapPending'])+';\n'
    tb+='decode_pending a('+','.join(dp+['observed'])+');\ninitial begin for(integer i=0;i<(1<<17);i++)begin\n'
    tb+='{'+','.join(dp)+"}=17'(i);#1;if(observed!==expected)$fatal(1,\"Decode decomposition mismatch: %x\",i);\n"
    tb+='end $display("PASS: actual decode aggregate equals all eight lane/trap pairs OR skid, all131072 inputs");$finish;end endmodule\n'
    prove(out,'decode-decomposition',tb)

    # Complete held/bypass contribution, including original live-lane flush
    # masks, valid suppression, stored payload selection and stale payloads.
    inputs=['AlignerPlugin_logic_feeder_lanes_0_valid','AlignerPlugin_logic_feeder_lanes_1_valid',
            'decode_ctrls_0_down_TRAP_0','decode_ctrls_0_down_TRAP_1',
            'decode_ctrls_0_down_LANE_SEL_0_s2mBuffer','decode_ctrls_0_down_LANE_SEL_1_s2mBuffer',
            'decode_ctrls_0_down_TRAP_0_s2mBuffer','decode_ctrls_0_down_TRAP_1_s2mBuffer',
            'decode_skid_rValid']+non_lsu+[F]
    assert len(inputs)==15
    original,width=checker.cone(src,'reference',inputs,['decode_logic_skid_trapPending','_zz_decode_logic_trapPending_4'])
    changed,new_width=checker.cone(candidate,'candidate',inputs,[Q,'_zz_decode_logic_trapPending_4'])
    assert width==new_width==3
    tb=original+changed+'\nmodule tb;\n'+'\n'.join('reg '+n+';' for n in inputs)+'\nreg execute_pending; wire [2:0] original,result;\n'
    tb+='reference a('+','.join(inputs+['original'])+');\ncandidate b('+','.join(inputs+['result'])+');\n'
    tb+='integer checked=0; initial begin for(integer i=0;i<65536;i++)begin\n'
    tb+='{execute_pending,'+','.join(inputs)+"}=16'(i);#1;\n"
    tb+=f'''if(!{F} || execute_pending) begin
  if((execute_pending || (|original)) !== (execute_pending || (|result)))
    $fatal(1,"Held/bypass aggregate mismatch inputs=%x old=%x new=%x",i,original,result);
  if(original[1:0] !== result[1:0]) $fatal(1,"Ordinary decode0 contribution changed");
  checked++;
end
end
if(checked!=49152)$fatal(1,"Unexpected proof domain");
$display("PASS: exact held/bypass aggregate for all49152 source states satisfying separately proved LSU-flush=>execute-pending");
$finish;end endmodule
'''
    prove(out,'held-bypass',tb)
    (out/'VexiiRiscv.v').write_text(candidate)
    shutil.copy2(__file__,out/'inflight_hold.py');shutil.copy2(CHECK,out/'check_vexii_os20_timing.py')
    (out/'inflight-hold.patch').write_text(''.join(difflib.unified_diff(src.splitlines(True),candidate.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
    manifest=dict(reference=str(REF.relative_to(ROOT)),reference_sha256=SHA,candidate_sha256=sha(candidate.encode()),
                  script_sha256=sha(Path(__file__).read_bytes()),checker_sha256=CHECK_SHA,
                  changed_output=H,new_register_bits=0,pipeline_cycles_added=0,
                  actual_skid_flush_valid_lane_masks_storage_unchanged=True,ordinary_decode_contribution_unchanged=True,
                  held_pending_gated_by_occupancy=True,lsu_flush_absorbed_only_in_aggregate=True,
                  implication_input_states=1024,decode_decomposition_states=131072,aggregate_admissible_states=49152,
                  proof_assumption='LSU flush implies execute pending; separately proved for all source inputs, no sequential invariant',
                  all_other_rtl_exactly_unchanged=True,whole_cpu_validation='pending')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
