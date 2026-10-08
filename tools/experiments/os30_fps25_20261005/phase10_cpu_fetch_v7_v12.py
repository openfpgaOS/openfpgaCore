#!/usr/bin/env python3
"""Apply only the proven final-fetch factor directly to exact v7 CPU8fca."""
import difflib, hashlib, importlib.util, json, re, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-rvc-plane-v7'; O=P/'cpu-fetch-v7-v12'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD='''  assign fetch_logic_ctrls_3_down_ready = !fetch_logic_ctrls_3_down_valid ||
      (fetch_ready_allow && (fetch_ready_empty || decode_ctrls_2_down_ready));'''
NEW='''  // Keep occupancy and dispatch qualification ahead of the late RS-hazard
  // reduction. decode2 ready is !G || (early_can && !rsHazards), where
  // G=lane_sel&&!sent. The no-G case is already absorbed by the first term.
  // Original dispatch ready/fire/hazard equations remain unchanged.
  (* keep *) wire fetch_ready_without_rs = !fetch_logic_ctrls_3_down_valid ||
      (fetch_ready_allow && (fetch_ready_empty ||
       !decode_ctrls_2_up_LANE_SEL_0 || DispatchPlugin_logic_feeds_0_sent));
  (* keep *) wire fetch_ready_check_rs = fetch_ready_allow && dispatch_ready_early_can;
  assign fetch_logic_ctrls_3_down_ready = fetch_ready_without_rs ||
      (fetch_ready_check_rs && !DispatchPlugin_logic_candidates_0_rsHazards[0]);'''

def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def module(name):
 spec=importlib.util.spec_from_file_location(name,Path(__file__).parent/('phase10_'+name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def prepare():
 assert sha(BASE/'VexiiRiscv.v')=='8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
 s=(BASE/'VexiiRiscv.v').read_text();assert s.count(OLD)==1
 later=(P/'cpu-writeback-plane-v10/VexiiRiscv.v').read_text()
 for begin,end in [('  (* keep *) wire fetch_ready_allow =','  assign when_AlignerPlugin_l256 ='),('  (* keep *) wire dispatch_ready_early_can =','  assign DispatchPlugin_logic_scheduler_eusFree_0 ='),("  always @(*) begin\n    decode_ctrls_2_down_ready =",'\n\n')]:
  def block(t):
   i=t.index(begin);return t[i:t.index(end,i+len(begin))]
  assert block(s)==block(later)
 assert 'aligner_mask_last_update' not in s and 'os30_wb_copy_byte' not in s
 assert s.count("decode_ctrls_2_down_ready = 1'b1;")==s.count("decode_ctrls_2_down_ready = 1'b0;")==1
 t=s.replace(OLD,NEW);O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(t);shutil.copy2(__file__,O/'recipe.py')
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='cpu8fca',tofile='fetch-v7-v12')))
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),
  timing_manifest_sha256=sha(P/'compact-timing-audit-v7/manifest.json'),added_state=0,added_cycles=0,
  inherited_fetch_component_manifest_sha256=sha(P/'cpu-fetch-hazard-v11/manifest.json'),
  aligner_enable_reservation_rewrite_and_WB_data_plane_excluded=True,
  only_final_fetch_ready_equation_changed=True,original_dispatch_ready_fire_issue_hazards_reservation_and_registers_unchanged=True))
 print(sha(O/'VexiiRiscv.v'))
def prove():
 d=O/'proof';d.mkdir(exist_ok=False);s=(BASE/'VexiiRiscv.v').read_text()
 a=s.index('  (* keep *) wire dispatch_ready_early_can =');b=s.index('  assign DispatchPlugin_logic_scheduler_eusFree_0 =',a)
 early=s[a:b]
 a=s.index('  always @(*) begin\n    decode_ctrls_2_down_ready =');b=s.index('\n\n',a)
 ready=s[a:b]
 aliases={
  'fetch_logic_ctrls_3_down_valid':'bits[0]', 'fetch_ready_allow':'bits[1]', 'fetch_ready_empty':'bits[2]',
  'decode_ctrls_2_up_LANE_SEL_0':'bits[3]', 'DispatchPlugin_logic_feeds_0_sent':'bits[4]',
  'decode_ctrls_2_up_isValid':'bits[5]', 'DispatchPlugin_logic_candidates_0_ctx_laneLayerHits[0]':'bits[6]',
  'DispatchPlugin_logic_candidates_0_reservationHazards[0]':'bits[7]',
  'DispatchPlugin_logic_candidates_0_flushHazards':'bits[8]', 'DispatchPlugin_logic_candidates_0_fenceOlderHazards':'bits[9]',
  'execute_freeze_valid':'bits[10]', 'DispatchPlugin_api_haltDispatch':'bits[11]',
  'DispatchPlugin_logic_candidates_0_rsHazards[0]':'(|bits[14:12])'}
 def shorten(t):
  for x,y in aliases.items():t=t.replace(x,y)
  return t
 source='module probe(input[14:0]bits,output old_ready,new_ready,negative_ready);\nreg decode_ctrls_2_down_ready;wire when_DispatchPlugin_l373;\n'
 source+=shorten(early+'\n'+ready+'\n'+OLD.replace('fetch_logic_ctrls_3_down_ready','old_ready')+'\n'+NEW.replace('fetch_logic_ctrls_3_down_ready','new_ready'))
 source+='\nassign negative_ready=fetch_ready_without_rs||fetch_ready_check_rs;\nendmodule\n'
 (d/'probe.sv').write_text(source)
 (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdio>
int main(){Vprobe d;unsigned neg=0;for(unsigned b=0;b<32768;b++){
 d.bits=b;d.eval();if(d.old_ready!=d.new_ready){printf("FAIL %u\\n",b);return 1;}
 neg+=d.old_ready!=d.negative_ready;}
 if(!neg)return 2;printf("PASS checks=32768 negative=%u\\n",neg);}
''')
 cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','3'];save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr)
 m=re.fullmatch(r'PASS checks=32768 negative=(\d+)\n',r.stdout);assert m and int(m[1])
 save(d/'result.json',dict(passed=True,all32768_independent_controls=True,original_literal_early_stall_decode_ready_and_new_fetch_ready=True,
  arbitrary_fetch_valid_allow_empty_lane_sent_valid_layer_reservation_flush_fence_freeze_halt_and_three_RS_hazards=True,
  negative_missing_RS_hazard_mismatches=int(m[1]),
  algebra='D=!G||(E&&!H); F=!V||A&&(B||D) = (!V||A&&(B||!G)) || (A&&E&&!H). This distributive identity holds for arbitrary booleans; no reachable-state, one-hot, cancel or trap assumption.',
  original_capture_reset_flush_and_every_sequential_statement_unchanged=True))
def inherited():
 m=module('cpu_rvc_plane_v7');m.O=O;m.BASE=BASE;return m
def native_prepare():
 inherited().native_prepare()
 p=O/'native/frozen'/CPU;s=p.read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
 monitor='''
wire fetch_hazard_reference=!fetch_logic_ctrls_3_down_valid ||
 (fetch_ready_allow && (fetch_ready_empty || decode_ctrls_2_down_ready));
reg[63:0]fetch_hazard_checks=0,fetch_hazard_stalled=0,fetch_hazard_rs3=0;
always @(posedge clk)if(!reset)begin
 assert(fetch_logic_ctrls_3_down_ready==fetch_hazard_reference);
 assert(decode_ctrls_2_down_ready==(!(decode_ctrls_2_up_LANE_SEL_0&&!DispatchPlugin_logic_feeds_0_sent)||
       (dispatch_ready_early_can&&!DispatchPlugin_logic_candidates_0_rsHazards[0])));
 fetch_hazard_checks<=fetch_hazard_checks+1;
 fetch_hazard_stalled<=fetch_hazard_stalled+!fetch_hazard_reference;
 fetch_hazard_rs3<=fetch_hazard_rs3+DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_2_hazard;
end
final $display("FETCH_HAZARD_COVER checks=%0d stalled=%0d rs3=%0d",fetch_hazard_checks,fetch_hazard_stalled,fetch_hazard_rs3);
'''
 p.write_text(s[:e]+monitor+s[e:]);prep=json.loads((O/'native/prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(O/'native'))]=sha(p)
 (O/'native/prepared.json').write_text(json.dumps(prep,indent=2)+'\n')
def native_model():inherited().native_model()
def native_run():inherited().native_run()
def extra():inherited().extra()
def seal():
 for n in ('proof/result.json','native/result.json','native/extra/result.json'):assert json.loads((O/n).read_text())['passed']
 logs=list((O/'native/runs').glob('*/*-candidate.log'))+list((O/'native/extra').glob('*/*-candidate.log'));assert len(logs)==10
 direct=[];coverage={n:0 for n in ('READY','FP_RS2','FP_RS3','INTWB','RVC','FETCH_HAZARD')};stalled=rs3=0
 for p in logs:
  ref=BASE/p.relative_to(O);a=p.read_text();b=ref.read_text()
  uart=lambda t:t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
  cyc=lambda t:int(re.search(r'cycles=(\d+) PC=',t)[1])
  assert uart(a)==uart(b) and cyc(a)==cyc(b)
  direct.append(dict(candidate_log=str(p.relative_to(O)),reference_log=str(ref.relative_to(ROOT)),reference_sha256=sha(ref),candidate_sha256=sha(p),cycles=cyc(a),UART_equal=True,cycles_equal=True))
  for n in coverage:coverage[n]+=int(re.search(n+r'_COVER checks=(\d+)',a)[1])
  q=re.search(r'FETCH_HAZARD_COVER checks=\d+ stalled=(\d+) rs3=(\d+)',a);stalled+=int(q[1]);rs3+=int(q[2])
 assert stalled and rs3
 save(O/'result.json',dict(passed=True,all10_direct8fca_UART_cycles_exact=True,runs=direct,live_checks=coverage,
  live_fetch_stall_cycles=stalled,live_RS3_hazard_cycles=rs3,added_state=0,added_cycles=0,
  architectural_dispatch_fire_issue_reservation_hazards_unchanged=True,no_fit=True))
 (O/'REPORT.txt').write_text('''Final fetch-ready hazard factor: private proof PASS

Only the final fetch-ready assignment changes. Occupancy/lane/sent and early
dispatch permission are computed independently of the late RS-hazard reduction.
All original dispatch-ready/fire/issue/reservation/hazard expressions and every
register, reset, flush, trap and capture statement remain byte-identical.

With G=lane&&!sent, E=original early_can, H=the three RS hazards, the original
decode-ready is !G||(E&&!H). Thus !V||A&&(B||decode-ready) distributes exactly
to (!V||A&&(B||!G))||(A&&E&&!H). The second term needs no G because the first
already permits !G. This holds for arbitrary independent booleans.

The literal original source and new equation pass all32768 combinations of
the fifteen controls, including each hazard and qualification. A deliberately
missing hazard condition is detected. Ten actual nativeCPU cases match parent
8fca UART and exact cycles, with live original fetch/decode-ready checks plus
all inherited ready, arithmetic, decoder and forwarding monitors.
The original v7 mask/last update statements remain byte-identical.

Only the final-fetch hunk is inherited. The v7 reservation-to-E-to-l373
fetch/capture tail is targeted without changing original reservation or early_can.
The intervening aligner-enable absorption and writeback DATA plane are excluded. No state/cycle/cache/ISA/clock change, mapper,
fitter, production edit or timing-gain claim is made by this component.
''')
 files=[p for p in O.rglob('*') if p.is_file() and not any(c in p.relative_to(O).parts for c in ('obj','frozen')) and p.name!='manifest.json']
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),production_unchanged=True))
 print(sha(O/'manifest.json'),sha(O/'VexiiRiscv.v'))
if __name__=='__main__':globals()[sys.argv[1]]()
