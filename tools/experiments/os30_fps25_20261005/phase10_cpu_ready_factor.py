#!/usr/bin/env python3
"""Exact single-candidate ready factor; keep architectural issue logic unchanged."""
import hashlib, importlib.util, json, re, shutil, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-rvc-control-v3'; O=P/'cpu-ready-factor-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD='  assign when_DispatchPlugin_l373 = ((decode_ctrls_2_up_LANE_SEL_0 && (! DispatchPlugin_logic_feeds_0_sent)) && (! DispatchPlugin_logic_candidates_0_fire));'
NEW='''  // Ready-only factoring for the selected single lane/candidate. Keep fire,
  // issue, trap selection and every hazard equation unchanged. The late RS
  // reduction bypasses the issue qualification shared by other consumers.
  (* keep *) wire dispatch_ready_early_can = decode_ctrls_2_up_isValid &&
      DispatchPlugin_logic_candidates_0_ctx_laneLayerHits[0] &&
      !DispatchPlugin_logic_candidates_0_reservationHazards[0] &&
      !DispatchPlugin_logic_candidates_0_flushHazards &&
      !DispatchPlugin_logic_candidates_0_fenceOlderHazards &&
      !execute_freeze_valid && !DispatchPlugin_api_haltDispatch;
  assign when_DispatchPlugin_l373 = (decode_ctrls_2_up_LANE_SEL_0 &&
      !DispatchPlugin_logic_feeds_0_sent) &&
      (!dispatch_ready_early_can || DispatchPlugin_logic_candidates_0_rsHazards[0]);'''
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def prepare():
 s=(BASE/'VexiiRiscv.v').read_text();assert sha(BASE/'VexiiRiscv.v')=='b5397043e842bb2ed88005e6f24321668f2ddbffe392db94f08be3b44b36576d'
 for t in (OLD,"  assign DispatchPlugin_logic_scheduler_eusFree_0 = 1'b1;","  assign DispatchPlugin_logic_scheduler_hartFree_0 = 1'b1;","  assign DispatchPlugin_logic_scheduler_arbiters_0_candHazard = 1'b0;",'  assign DispatchPlugin_logic_candidates_0_ctx_valid = ((decode_ctrls_2_up_isValid && decode_ctrls_2_up_LANE_SEL_0) && (! DispatchPlugin_logic_feeds_0_sent));'):
  assert s.count(t)==1,t
 assert re.search(r'reg\s+\[0:0\]\s+DispatchPlugin_logic_candidates_0_ctx_laneLayerHits;',s)
 O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(s.replace(OLD,NEW));shutil.copy2(__file__,O/'recipe.py')
 (O/'original.txt').write_text(OLD+'\n');(O/'candidate.txt').write_text(NEW+'\n')
 import difflib
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),(O/'VexiiRiscv.v').read_text().splitlines(True),fromfile='control-v3',tofile='ready-factor-v1')))
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),parent_sha256=sha(BASE/'VexiiRiscv.v'),candidate_sha256=sha(O/'VexiiRiscv.v'),added_state_bits=0,added_cycles=0,fire_and_issue_unchanged=True))
def prove():
 d=O/'proof';d.mkdir(exist_ok=False)
 (d/'probe.sv').write_text('''module probe(input [14:0] bits, output old_stall,new_stall,old_fetch,new_fetch,
 output old_issue,new_issue,negative_stall);
 wire v=bits[0],lane=bits[1],sent=bits[2],rs=bits[3],reservation=bits[4],flush=bits[5],fence=bits[6];
 wire freeze=bits[7],halt=bits[8],decoded_layer=bits[9],trap=bits[10],cancel=bits[11];
 wire fetch_valid=bits[12],fetch_allow=bits[13],fetch_empty=bits[14];
 wire layer=trap?1'b1:decoded_layer;
 wire ctx_valid=(v&&lane)&&!sent;
 wire layers_hits=((layer&!rs)&!reservation)&1'b1;
 wire layer_oh=layers_hits&&!1'b0;
 wire do_it=(((((ctx_valid&&!flush)&&!fence)&&layer_oh)&&1'b1)&&!1'b0);
 wire fire=(do_it&&!freeze)&&!halt;
 assign old_stall=(lane&&!sent)&&!fire;
 wire early_can=v&&layer&&!reservation&&!flush&&!fence&&!freeze&&!halt;
 assign new_stall=(lane&&!sent)&&(!early_can||rs);
 assign old_fetch=!fetch_valid||(fetch_allow&&(fetch_empty||!old_stall));
 assign new_fetch=!fetch_valid||(fetch_allow&&(fetch_empty||!new_stall));
 assign old_issue=(do_it&&layer_oh)&&!cancel&&!halt;
 assign new_issue=old_issue; // deliberately unchanged architectural issue path
 assign negative_stall=(lane&&!sent)&&(!(v&&layer&&!reservation&&!flush&&!freeze&&!halt)||rs);
 endmodule
''')
 (d/'main.cpp').write_text('''#include "Vprobe.h"
#include <cstdio>
int main(){Vprobe d;unsigned n=0,negative=0;for(unsigned b=0;b<32768;b++){
d.bits=b;d.eval();if(d.old_stall!=d.new_stall||d.old_fetch!=d.new_fetch||d.old_issue!=d.new_issue){printf("FAIL %u\\n",b);return 1;}negative+=d.old_stall!=d.negative_stall;n++;}
if(!negative)return 2;printf("PASS %u negative=%u\\n",n,negative);}
''')
 cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','3'];save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr)
 m=re.fullmatch(r'PASS 32768 negative=(\d+)\n',r.stdout);assert m and int(m[1])>0
 save(d/'result.json',dict(passed=True,exhaustive_combinations=32768,all_binary_valid_lane_sent_trap_cancel_hazard_fetch_states=True,negative_control_mismatches=int(m[1]),added_state_bits=0,added_cycles=0))
def native_module():
 spec=importlib.util.spec_from_file_location('native',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_native.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.O=O;m.N=O/'native';return m
def native_prepare():
 m=native_module();m.prepare();p=m.N/'frozen'/m.CPU;s=p.read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('))
 monitor='''
wire dispatch_stall_reference=(decode_ctrls_2_up_LANE_SEL_0 && !DispatchPlugin_logic_feeds_0_sent) && !DispatchPlugin_logic_candidates_0_fire;
wire fetch_ready_reference=!fetch_logic_ctrls_3_down_valid || (fetch_ready_allow && (fetch_ready_empty || !dispatch_stall_reference));
reg [63:0] ready_checks=0,ready_stalls=0,ready_rs=0,ready_cancel=0,ready_trap=0;
always @(posedge clk) if(!reset)begin
 assert(dispatch_stall_reference==when_DispatchPlugin_l373);
 assert(fetch_ready_reference==fetch_logic_ctrls_3_down_ready);
 ready_checks<=ready_checks+1;
 ready_stalls<=ready_stalls+dispatch_stall_reference;
 ready_rs<=ready_rs+DispatchPlugin_logic_candidates_0_rsHazards[0];
 ready_cancel<=ready_cancel+DispatchPlugin_logic_candidates_0_cancel;
 ready_trap<=ready_trap+decode_ctrls_2_down_TRAP_0;
end
final $display("READY_COVER checks=%0d stalls=%0d rs=%0d cancel=%0d trap=%0d",ready_checks,ready_stalls,ready_rs,ready_cancel,ready_trap);
'''
 p.write_text(s[:e]+monitor+s[e:]);prep=json.loads((m.N/'prepared.json').read_text());prep['sources_sha256'][str(p.relative_to(m.N))]=sha(p);prep['native_reference_manifest_sha256']=sha(BASE/'manifest.json');(m.N/'prepared.json').write_text(json.dumps(prep,indent=2)+'\n');shutil.copy2(__file__,m.N/'ready-recipe.py')
def native_model():native_module().model()
def native_run():
 m=native_module();m.OLD=BASE/'native';m.run();out=[]
 for p in (m.N/'runs').glob('*/*-candidate.log'):
  t=p.read_text();a=re.search(r'READY_COVER checks=(\d+) stalls=(\d+) rs=(\d+) cancel=(\d+) trap=(\d+)',t);assert a
  row=dict(zip(('checks','stalls','rs','cancel','trap'),map(int,a.groups())));assert row['checks'] and row['stalls'] and row['rs'];out.append(dict(log=str(p.relative_to(O)),**row))
 save(m.N/'ready-result.json',dict(passed=True,runs=out,total_checks=sum(x['checks'] for x in out)))
def seal():
 assert json.loads((O/'proof/result.json').read_text())['passed'];assert json.loads((O/'native/result.json').read_text())['passed'];assert json.loads((O/'native/ready-result.json').read_text())['passed']
 files=[O/'VexiiRiscv.v',O/'prepared.json',O/'recipe.py',O/'candidate.patch',O/'proof/result.json',O/'proof/command.json',O/'proof/probe.sv',O/'proof/main.cpp',O/'proof/run.log',O/'native/prepared.json',O/'native/ready-recipe.py',O/'native/build.json',O/'native/result.json',O/'native/ready-result.json']+list((O/'native/runs').glob('*/*.log'))
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},parent_manifest_sha256=sha(BASE/'manifest.json'),scope='Exact ready-only combinational factor; no map/fit/regeneration or performance claim.'))
if __name__=='__main__':globals()[sys.argv[1]]()
