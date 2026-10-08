#!/usr/bin/env python3
"""Independent combined CPU source and proof review; no native rerun or fit."""
import importlib.util,json,re,shutil
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
P=CAMP/'phase10';C=P/'cpu-aligner-ready-v8';B=P/'cpu-rvc-plane-v7';O=P/'cpu-aligner-ready-review-v8';H=Path(__file__).parent
def mod(name):
 spec=importlib.util.spec_from_file_location(name,H/(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def main():
 prep=json.loads((C/'prepared.json').read_text());proof=json.loads((C/'manifest.json').read_text())
 for n,h in proof['files'].items():assert sha(C/n)==h,n
 assert prep['candidate_sha256']==sha(C/'VexiiRiscv.v')=='3e8cbcee4df8fae9a0a5b5e6d02a46b00a3430dd0bfad63b86cca850949ee514'
 assert prep['parent_manifest_sha256']==sha(B/'manifest.json')
 old=(B/'VexiiRiscv.v').read_text();new=(C/'VexiiRiscv.v').read_text()
 enable=mod('phase10_cpu_aligner_enable');ready=mod('phase10_cpu_aligner_ready_v8')
 a=enable.rep(enable.rep(old,enable.ANCHOR,enable.ANCHOR+enable.ADDITION),enable.OLD,enable.NEW)
 assert ready.rep(a,ready.RES_OLD,ready.RES_NEW)==new
 b=ready.rep(old,ready.RES_OLD,ready.RES_NEW)
 assert enable.rep(enable.rep(b,enable.ANCHOR,enable.ANCHOR+enable.ADDITION),enable.OLD,enable.NEW)==new
 assert new.replace(ready.RES_NEW,ready.RES_OLD).replace(enable.NEW,enable.OLD).replace(enable.ANCHOR+enable.ADDITION,enable.ANCHOR)==old
 for pattern in (r'^\s*reg\s+[^;]+;',r'^\s*(?:input|output|parameter|localparam)\s+[^;]+;'):
  assert re.findall(pattern,old,re.M)==re.findall(pattern,new,re.M)
 guards=["  assign fetch_logic_ctrls_3_down_isCancel = 1'b0;","  assign decode_ctrls_0_up_isCancel = 1'b0;",
 '  assign AlignerPlugin_logic_buffer_downFire = (decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel);',
 '  assign decode_ctrls_0_up_ready = decode_ctrls_0_down_isReady;',
 '  assign decode_ctrls_0_down_isReady = decode_ctrls_0_down_ready;',
 '  assign decode_ctrls_1_up_ready = decode_ctrls_1_down_isReady;',
 '  assign decode_ctrls_1_down_isReady = decode_ctrls_1_down_ready;',
 '  assign decode_ctrls_2_up_ready = decode_ctrls_2_down_isReady;',
 '  assign decode_ctrls_2_down_isReady = decode_ctrls_2_down_ready;',
 '  assign when_StageLink_l71_2 = (! decode_ctrls_1_up_isValid);',
 '  assign when_StageLink_l71_3 = (! decode_ctrls_2_up_isValid);',
 '''  always @(*) begin
    decode_ctrls_0_down_ready = decode_ctrls_1_up_ready;
    if(when_StageLink_l71_2) begin
      decode_ctrls_0_down_ready = 1'b1;
    end
  end''',
 '''  always @(*) begin
    decode_ctrls_1_down_ready = decode_ctrls_2_up_ready;
    if(when_StageLink_l71_3) begin
      decode_ctrls_1_down_ready = 1'b1;
    end
  end''']
 for g in guards:assert old.count(g)==new.count(g)==1,g
 # Ensure reset and later flush override remain after the rewritten block;
 # exact complete reconstruction establishes all other writes unchanged.
 flush='''      if(AlignerPlugin_logic_buffer_flushIt) begin
        AlignerPlugin_logic_buffer_mask <= 2'b00;
        AlignerPlugin_logic_buffer_last <= 2'b00;
      end'''
 assert old.count(flush)==new.count(flush)==1 and new.index(flush)>new.index(enable.NEW)
 for n in ('proof/result.json','enable-proof/result.json','reservation-proof/result.json','native/result.json','native/extra/result.json','result.json'):assert json.loads((C/n).read_text())['passed'],n
 result=json.loads((C/'result.json').read_text());assert result['all10nativeUART_and_cycles_exact']
 for n in ('READY','FP_RS2','FP_RS3','INTWB','RVC','ALIGNER_UPDATE'):assert result['live_checks'][n]>0
 O.mkdir(exist_ok=False)
 text='''Independent combined CPU v8 review: PASS

The exact final source is3e8cbcee, composed over qualified v7 CPU8fca2871.
Both disjoint substitutions commute and reversing them reproduces every parent
byte. Register, parameter and port declarations are unchanged. No extra flip-
flop, pipeline stage, cycle, cache capacity, clock, ISA or FPU setting is added.

The first substitution groups only aligner mask/last writes under an equivalent
local enable. In this exact generated topology, decode0 and fetch cancel are
constant0. Following the two existing elastic ready links gives downFire =
!decode1Valid || !decode2Valid || decode2Ready. Thus the old fetch occupancy term
reduces to emptyMask || downFire. Fetch capture with a nonempty old mask already
implies downFire; when old mask is empty, allow equals !halt. Therefore
downFire || capture equals downFire || (fetchValid && !halt && emptyMask).
The original capture-priority data equations and later flush override remain;
trap captures under its original condition and reset is unchanged. This is
specific to the checked generated topology, not a general multi-lane identity.

The second substitution removes reservation hazard R from kept early term E
and includes it in the final ready-block OR. G && (!(E && !R) || H) equals
G && (!E || R || H) for every binary input. The actual reservation network,
register hazard, issue/fire, cancellation and trap conditions are untouched.
Only equivalent ready qualification is refactored; no resource hazard is lost.

Existing proofs cover262,144 arbitrary aligner control/data/reset/flush states,
32,768 scheduler controls, and negative controls. Ten actual CPU program/traffic
pairs preserve exact UART output and cycle counts. Live original ready, RVC,
RS2/RS3, integer writeback and aligner reference-state checks all run together.
This review inspected/bound those proofs without rerunning them. It does not
claim four-state X equivalence or whole-native-game rendering.

No map/fitter, generator configuration, production or sibling/SD edit was
performed here. The generator proposal, actual Scala reproduction and normal-
build source closure remain separately required before the composed fit gate.
'''
 (O/'REPORT.txt').write_text(text);shutil.copy2(Path(__file__),O/'recipe.py')
 write(O/'review.json',dict(passed=True,source_sha256=sha(C/'VexiiRiscv.v'),parent_sha256=sha(B/'VexiiRiscv.v'),complete_reverse_source_exact=True,hunks_commute=True,register_port_parameter_declarations_unchanged=True,selected_generated_ready_topology_checked=True,reset_flush_capture_priorities_preserved=True,no_native_rerun=True,no_fitter=True))
 evidence=[C/'manifest.json',C/'prepared.json',C/'VexiiRiscv.v',C/'combined.patch',B/'manifest.json',H/'phase10_cpu_aligner_enable.py',H/'phase10_cpu_aligner_ready_v8.py',Path(__file__)]+list(O.iterdir())
 write(O/'manifest.json',dict(passed=True,candidate_sha256=sha(C/'VexiiRiscv.v'),evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in evidence}))
 print(sha(O/'manifest.json'))
if __name__=='__main__':main()
