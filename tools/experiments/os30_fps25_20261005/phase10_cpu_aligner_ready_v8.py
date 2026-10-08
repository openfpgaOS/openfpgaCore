#!/usr/bin/env python3
"""Two exact v7 control-cone reductions, without state or cycle changes."""
import difflib,hashlib,importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-rvc-plane-v7';O=P/'cpu-aligner-ready-v8';E=P/'cpu-aligner-enable-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
RES_OLD='''  (* keep *) wire dispatch_ready_early_can = decode_ctrls_2_up_isValid &&
      DispatchPlugin_logic_candidates_0_ctx_laneLayerHits[0] &&
      !DispatchPlugin_logic_candidates_0_reservationHazards[0] &&
      !DispatchPlugin_logic_candidates_0_flushHazards &&
      !DispatchPlugin_logic_candidates_0_fenceOlderHazards &&
      !execute_freeze_valid && !DispatchPlugin_api_haltDispatch;
  assign when_DispatchPlugin_l373 = (decode_ctrls_2_up_LANE_SEL_0 &&
      !DispatchPlugin_logic_feeds_0_sent) &&
      (!dispatch_ready_early_can || DispatchPlugin_logic_candidates_0_rsHazards[0]);'''
RES_NEW='''  // Reservation and RS reductions are both late inputs to the same ready
  // decision. Keep them outside the early qualification; issue is unchanged.
  (* keep *) wire dispatch_ready_early_can = decode_ctrls_2_up_isValid &&
      DispatchPlugin_logic_candidates_0_ctx_laneLayerHits[0] &&
      !DispatchPlugin_logic_candidates_0_flushHazards &&
      !DispatchPlugin_logic_candidates_0_fenceOlderHazards &&
      !execute_freeze_valid && !DispatchPlugin_api_haltDispatch;
  assign when_DispatchPlugin_l373 = (decode_ctrls_2_up_LANE_SEL_0 &&
      !DispatchPlugin_logic_feeds_0_sent) &&
      (!dispatch_ready_early_can || DispatchPlugin_logic_candidates_0_reservationHazards[0] ||
       DispatchPlugin_logic_candidates_0_rsHazards[0]);'''
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'tools/experiments/os30_fps25_20261005'/('phase10_'+name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def enable():
 m=module('cpu_aligner_enable');m.O=O;m.BASE=BASE;return m
def prepare():
 assert json.loads((E/'proof/result.json').read_text())['passed']
 s=(BASE/'VexiiRiscv.v').read_text();m=enable();e=rep(rep(s,m.ANCHOR,m.ANCHOR+m.ADDITION),m.OLD,m.NEW)
 assert e==(E/'VexiiRiscv.v').read_text()
 candidate=rep(e,RES_OLD,RES_NEW)
 other=rep(s,RES_OLD,RES_NEW);assert rep(rep(other,m.ANCHOR,m.ANCHOR+m.ADDITION),m.OLD,m.NEW)==candidate
 O.mkdir(exist_ok=False);(O/'VexiiRiscv.v').write_text(candidate);shutil.copy2(__file__,O/'recipe.py')
 for name,before,after in [('enable',s,e),('reservation',e,candidate),('combined',s,candidate)]:
  (O/(name+'.patch')).write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/VexiiRiscv.v',tofile='b/VexiiRiscv.v')))
 shutil.copytree(E/'proof',O/'enable-proof',ignore=shutil.ignore_patterns('obj'))
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),
  enable_candidate_sha256=sha(E/'VexiiRiscv.v'),enable_proof_sha256=sha(E/'proof/result.json'),checked_disjoint_hunks_commute=True,
  added_state=0,added_cycles=0,architectural_fire_hazards_and_capture_unchanged=True))
def prove():
 d=O/'reservation-proof';d.mkdir(exist_ok=False)
 # Start from the complete original fire/scheduler truth probe, so the new
 # internal early term is never mistaken for an architectural invariant.
 source=(P/'cpu-ready-factor-v1/proof/probe.sv').read_text()
 source=rep(source,'wire early_can=v&&layer&&!reservation&&!flush&&!fence&&!freeze&&!halt;',
  'wire early_can=v&&layer&&!flush&&!fence&&!freeze&&!halt;')
 source=rep(source,'assign new_stall=(lane&&!sent)&&(!early_can||rs);',
  'assign new_stall=(lane&&!sent)&&(!early_can||reservation||rs);')
 (d/'probe.sv').write_text(source);shutil.copy2(P/'cpu-ready-factor-v1/proof/main.cpp',d/'main.cpp')
 cmd=['verilator','--cc','--exe','--build','-Wno-fatal','--top-module','probe','--Mdir',str(d/'obj'),str(d/'probe.sv'),str(d/'main.cpp'),'-j','3'];save(d/'command.json',cmd)
 with (d/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 r=subprocess.run([str(d/'obj/Vprobe')],capture_output=True,text=True,check=True);(d/'run.log').write_text(r.stdout+r.stderr)
 match=re.fullmatch(r'PASS 32768 negative=(\d+)\n',r.stdout);assert match and int(match[1])
 save(d/'result.json',dict(passed=True,exhaustive_binary_controls=32768,original_architectural_fire_stall_fetch_issue_exact=True,
  arbitrary_lane_sent_valid_RS_reservation_flush_fence_freeze_halt_trap_cancel_fetch=True,negative_omitted_fence_mismatches=int(match[1])))
 (O/'proof').mkdir();save(O/'proof/result.json',dict(passed=True,enable_transition_checks=262144,reservation_control_checks=32768,
  independent_proofs_sha256={str(p.relative_to(O)):sha(p) for p in [O/'enable-proof/result.json',d/'result.json']}))
def native_prepare():enable().native_prepare()
def native_model():enable().native_model()
def native_run():enable().native_run()
def extra():enable().extra()
def seal():enable().seal()
if __name__=='__main__':globals()[sys.argv[1]]()
