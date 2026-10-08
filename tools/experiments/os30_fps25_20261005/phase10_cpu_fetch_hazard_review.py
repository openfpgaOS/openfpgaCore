#!/usr/bin/env python3
"""Independent final fetch-ready source/algebra and sealed native-proof review."""
import ast
import hashlib
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cpu-writeback-plane-v10'; C=P/'cpu-fetch-hazard-v11'
O=P/'cpu-fetch-hazard-review-v11'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    parent=(B/'VexiiRiscv.v').read_text(); candidate=(C/'VexiiRiscv.v').read_text()
    assert sha(C/'VexiiRiscv.v')=='6c02b722fe4a66f91ad88408e035dbadec92686e18000559cf13a93b5f541b54'
    recipe=Path(__file__).with_name('phase10_cpu_fetch_hazard.py')
    constants={}
    for node in ast.parse(recipe.read_text()).body:
        if isinstance(node,ast.Assign):
            for name in node.targets:
                if isinstance(name,ast.Name) and name.id in ('OLD','NEW'):
                    constants[name.id]=ast.literal_eval(node.value)
    assert parent.count(constants['OLD'])==1 and candidate==parent.replace(constants['OLD'],constants['NEW'])
    assert constants['NEW'].count('(* keep *) wire')==2
    assert 'always' not in constants['NEW'] and ' reg ' not in constants['NEW']
    # Verify the literal dependency used by the algebra, independently of
    # reachability: decode2 ready is the complement of G&&(!E||H).
    decode='''  always @(*) begin
    decode_ctrls_2_down_ready = 1'b1;
    if(when_DispatchPlugin_l373) begin
      decode_ctrls_2_down_ready = 1'b0;
    end
  end'''
    stall='''  assign when_DispatchPlugin_l373 = (decode_ctrls_2_up_LANE_SEL_0 &&
      !DispatchPlugin_logic_feeds_0_sent) &&
      (!dispatch_ready_early_can || DispatchPlugin_logic_candidates_0_rsHazards[0]);'''
    assert decode in parent and stall in parent and decode in candidate and stall in candidate
    for token in ('!fetch_logic_ctrls_3_down_valid','fetch_ready_allow && (fetch_ready_empty ||',
                  '!decode_ctrls_2_up_LANE_SEL_0 || DispatchPlugin_logic_feeds_0_sent',
                  'fetch_ready_allow && dispatch_ready_early_can',
                  'fetch_ready_check_rs && !DispatchPlugin_logic_candidates_0_rsHazards[0]'):
        assert token in constants['NEW']
    component=read(C/'manifest.json')
    assert component['candidate_sha256']==sha(C/'VexiiRiscv.v') and component['parent_manifest_sha256']==sha(B/'manifest.json')
    for name,digest in component['files'].items(): assert sha(C/name)==digest,name
    proof=read(C/'proof/result.json'); native=read(C/'result.json')
    assert proof['passed'] and proof['all32768_independent_controls']
    assert proof['negative_missing_RS_hazard_mismatches']>0
    assert native['passed'] and native['all10_direct482e_UART_cycles_exact'] and len(native['runs'])==10
    checks=stalls=rs3=0
    evidence=[B/'VexiiRiscv.v',B/'manifest.json',C/'VexiiRiscv.v',C/'manifest.json',C/'prepared.json',
              C/'result.json',C/'proof/result.json',C/'proof/probe.sv',C/'proof/run.log',recipe,Path(__file__)]
    for row in native['runs']:
        a=C/row['candidate_log']; b=ROOT/row['reference_log']
        assert b.is_relative_to(B) and sha(a)==row['candidate_sha256'] and sha(b)==row['reference_sha256']
        at=a.read_text();bt=b.read_text()
        uart=lambda t:t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
        cycles=lambda t:int(re.search(r'cycles=(\d+) PC=',t)[1])
        assert uart(at)==uart(bt) and cycles(at)==cycles(bt)==row['cycles']
        m=re.search(r'FETCH_HAZARD_COVER checks=(\d+) stalled=(\d+) rs3=(\d+)',at)
        checks+=int(m[1]);stalls+=int(m[2]);rs3+=int(m[3]);evidence += [a,b]
    assert checks==native['live_checks']['FETCH_HAZARD']==14519017
    assert stalls==native['live_fetch_stall_cycles']==4933884
    assert rs3==native['live_RS3_hazard_cycles']==16532
    prep=read(C/'native/prepared.json'); assert '--assert' in prep['command']
    for name,digest in prep['sources_sha256'].items(): assert sha(C/'native'/name)==digest,name
    cpu=C/'native/frozen/src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    sim=cpu.read_text();assert constants['NEW'] in sim
    assert 'assert(fetch_logic_ctrls_3_down_ready==fetch_hazard_reference)' in sim
    assert 'assert(decode_ctrls_2_down_ready==(!(decode_ctrls_2_up_LANE_SEL_0&&!DispatchPlugin_logic_feeds_0_sent)' in sim
    evidence += [cpu,C/'native/prepared.json']
    O.mkdir(exist_ok=False)
    result=dict(passed=True,candidate_sha256=sha(C/'VexiiRiscv.v'),only_final_fetch_ready_block_changed=True,
      original_decode_ready_dispatch_fire_issue_hazards_and_sequential_logic_unchanged=True,
      all_binary_control_cases_algebra_exact=True,no_reachability_assumption=True,
      literal_cases=32768,direct_native_uart_cycle_pairs=10,live_fetch_checks=checks,
      live_stalls=stalls,live_rs3_hazard_cycles=rs3,added_registers=0,added_cycles=0,
      no_new_simulation=True,deployment_ready=False)
    (O/'review.json').write_text(json.dumps(result,indent=2)+'\n')
    (O/'REPORT.txt').write_text('''Independent final fetch-ready review: PASS

Only the final fetch-ready block changes from CPU482e. The two added kept
wires are combinational. Every register, original decode/dispatch readiness,
fire, issue, reservation/hazard equation, trap/reset/flush and aligner capture
statement is byte-identical. No memory/cache/ISA/clock option changes.

Let G=lane&&!sent, E=early_can, H=RS hazards, V=fetch valid,
A=fetch_ready_allow and B=fetch_ready_empty. The actual old decode-ready
procedural block gives D=!(G&&(!E||H))=!G||(E&&!H).
Thus F=!V||A&&(B||D) equals
(!V||A&&(B||!G))||(A&&E&&!H), exactly the candidate.
This is Boolean distribution/absorption for arbitrary binary controls; it
needs no lane, occupancy, one-hot, cancel or reachable-state assumption.
H remains the complete original reduction: no hazard is omitted. Keeping
the early qualifier cannot promise routing or frequency; that remains a fit.

Rehashed literal proof covers32768 combinations and detects an omitted-H
negative control. Independently compared all ten exact parent482e UART and
cycle logs, plus bound assertion-enabled simulated CPU source.14519017 live
old-fetch/decode checks include4933884 stalled-fetch and16532 RS3-hazard
cycles. Inherited monitors are retained. This is bounded native qualification,
not a universal four-state proof or new whole-game CPU measurement.

No blocking source/proof finding. No simulation, mapper or fitter was run
by this review; actual CPU regeneration and build/fit gates remain separate.
''')
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    (O/'manifest.json').write_text(json.dumps(dict(files={p.name:sha(p) for p in O.iterdir() if p.is_file()},
      evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in evidence}),indent=2)+'\n')
    print(sha(O/'manifest.json'))

if __name__=='__main__':main()
