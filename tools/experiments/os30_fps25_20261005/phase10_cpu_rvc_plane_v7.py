#!/usr/bin/env python3
"""Measured v6 timing repair: independent bit16 SOP and direct bit18 plane."""
import difflib,hashlib,importlib.util,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';BASE=P/'cpu-final-paths-v1';O=P/'cpu-rvc-plane-v7'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
OLD='''  wire rvc_instruction_bit16 =
    (rvc_rs1_common && AlignerPlugin_logic_extractors_0_ctx_instruction[8]) |
    rvc_jump_sign |
    (switch_Rvc_l55==5'h00)||(switch_Rvc_l55==5'h12)||(switch_Rvc_l55==5'h13)||
    (switch_Rvc_l55==5'h16)||(switch_Rvc_l55==5'h17)||
    ((switch_Rvc_l55==5'h0b)&&(rvc_rd_is_sp||AlignerPlugin_logic_extractors_0_ctx_instruction[6]));'''
NEW='''  // Keep bit16 independent of the shared bit15 RS1 qualification chain.
  // In C.JR/C.JALR, combine rd[1] with the five-bit zero test in one term.
  (* keep *) wire rvc16_cr_zero_rs2_rd1 =
    ({AlignerPlugin_logic_extractors_0_ctx_instruction[6:2],
      !AlignerPlugin_logic_extractors_0_ctx_instruction[8]} == 6'b0);
  (* keep *) wire rvc16_cr_add_rd1 =
    AlignerPlugin_logic_extractors_0_ctx_instruction[12] &&
    AlignerPlugin_logic_extractors_0_ctx_instruction[8];
  wire rvc_instruction_bit16 =
    (((switch_Rvc_l55==5'h02)||(switch_Rvc_l55==5'h03)||
      (switch_Rvc_l55==5'h05)||(switch_Rvc_l55==5'h06)||(switch_Rvc_l55==5'h07)||
      (switch_Rvc_l55==5'h08)||(switch_Rvc_l55==5'h0c)||(switch_Rvc_l55==5'h0e)||
      (switch_Rvc_l55==5'h0f)||(switch_Rvc_l55==5'h10)) &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[8]) |
    ((switch_Rvc_l55==5'h14) && rvc16_cr_zero_rs2_rd1) |
    ((switch_Rvc_l55==5'h14) && rvc16_cr_add_rd1) |
    rvc_jump_sign |
    (switch_Rvc_l55==5'h00)||(switch_Rvc_l55==5'h12)||(switch_Rvc_l55==5'h13)||
    (switch_Rvc_l55==5'h16)||(switch_Rvc_l55==5'h17)||
    ((switch_Rvc_l55==5'h0b) && rvc_rd_is_sp) |
    ((switch_Rvc_l55==5'h0b) && AlignerPlugin_logic_extractors_0_ctx_instruction[6]);

  // Instruction bit18 is rs1[3] except the sign-extended jump/LUI forms.
  wire rvc_instruction_bit18 =
    (switch_Rvc_l55==5'h02)||(switch_Rvc_l55==5'h03)||
    (switch_Rvc_l55==5'h05)||(switch_Rvc_l55==5'h06)||(switch_Rvc_l55==5'h07)||
    (switch_Rvc_l55==5'h0c)||(switch_Rvc_l55==5'h0e)||(switch_Rvc_l55==5'h0f)||
    (((switch_Rvc_l55==5'h08)||(switch_Rvc_l55==5'h10)) &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[10]) |
    ((switch_Rvc_l55==5'h14) && AlignerPlugin_logic_extractors_0_ctx_instruction[10] &&
      ((AlignerPlugin_logic_extractors_0_ctx_instruction[6:2]==5'b0)||
       AlignerPlugin_logic_extractors_0_ctx_instruction[12])) |
    rvc_jump_sign |
    ((switch_Rvc_l55==5'h0b) && !rvc_rd_is_sp &&
      AlignerPlugin_logic_extractors_0_ctx_instruction[12]);'''
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):assert s.count(a)==1;return s.replace(a,b)
def module(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'tools/experiments/os30_fps25_20261005'/('phase10_'+name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def prepare():
 assert sha(BASE/'VexiiRiscv.v')=='022f46fbbb0834bba1499df65ecdda79ecf13447fb33eff85effcb3bbda1e73d';O.mkdir(exist_ok=False);s=(BASE/'VexiiRiscv.v').read_text();t=rep(s,OLD,NEW)
 a='      decode_ctrls_0_up_Decode_INSTRUCTION_0[16] = rvc_instruction_bit16;';t=rep(t,a,a+'\n      decode_ctrls_0_up_Decode_INSTRUCTION_0[18] = rvc_instruction_bit18;');(O/'VexiiRiscv.v').write_text(t);(O/'candidate.patch').write_text(''.join(difflib.unified_diff(s.splitlines(True),t.splitlines(True),fromfile='cpu022',tofile='rvc-plane-v7')));shutil.copy2(__file__,O/'recipe.py')
 probe=(P/'cpu-rvc-bits-v2/probe.v').read_text();probe=rep(probe,OLD,NEW);probe=rep(probe,'AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst[25:17],rvc_instruction_bit16','AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst[25:19],rvc_instruction_bit18,AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst[17],rvc_instruction_bit16');(O/'probe.v').write_text(probe)
 save(O/'prepared.json',dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),changed_bits=[16,18],added_FF=0,added_cycles=0,architectural_controls_unchanged=True,critical_report_sha256=sha(P/'compact-timing-audit-v6/reports/slow_85_rvc.rpt'),scope='Independentbit16 sum of products removes shared special-case RS1 qualification; bit18 direct original decode plane; otherbits/control unchanged.'))
def prove():
 m=module('cpu_rvc_bit26');m.O=O;m.prove()
def native_prepare():
 N=O/'native';N.mkdir(exist_ok=False);shutil.copytree(BASE/'native/frozen',N/'frozen')
 old=(BASE/'native/frozen'/CPU).read_text();a=old.index('wire [31:0] fp_rs3_reference =');monitor=old[a:old.index('\nendmodule',a)]
 s=(O/'VexiiRiscv.v').read_text();e=s.index('\nendmodule',s.index('module VexiiRiscv ('));(N/'frozen'/CPU).write_text(s[:e]+'\n'+monitor+s[e:])
 cmd=[x.replace(str(BASE/'native'),str(N)) for x in json.loads((BASE/'native/prepared.json').read_text())['command']]
 save(N/'prepared.json',dict(command=cmd,candidate_sha256=sha(O/'VexiiRiscv.v'),sources_sha256={str(p.relative_to(N)):sha(p) for p in (N/'frozen').rglob('*') if p.is_file()},inherited_combined_monitors=True))
def native_model():
 m=module('cpu_rvc_native');m.O=O;m.N=O/'native';m.model()
def native_run():
 m=module('cpu_rvc_native');m.O=O;m.N=O/'native';m.OLD=BASE/'native';m.run()
def extra():
 m=module('cpu_final_paths');m.O=O;m.extra()
def seal():
 for p in (O/'proof/result.json',O/'native/result.json',O/'native/extra/result.json'):assert json.loads(p.read_text())['passed']
 logs=list((O/'native/runs').glob('*/*-candidate.log'))+list((O/'native/extra').glob('*/*-candidate.log'));assert len(logs)==10
 coverage={}
 for n in ['READY','FP_RS2','FP_RS3','INTWB','RVC']:
  coverage[n]=sum(int(re.search(n+r'_COVER checks=(\d+)',p.read_text())[1]) for p in logs)
 save(O/'result.json',dict(passed=True,all10nativeUART_and_cycle_comparisons_exact=True,live_checks=coverage,added_state=0,added_cycles=0,no_fit=True))
 files=[p for p in O.rglob('*') if p.is_file() and not any(c in p.relative_to(O).parts for c in ['obj','frozen']) and p.name!='manifest.json']
 save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),production_unchanged=True));print(sha(O/'manifest.json'),sha(O/'VexiiRiscv.v'))
if __name__=='__main__':globals()[sys.argv[1]]()
