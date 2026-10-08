#!/usr/bin/env python3
"""Extend frozen bit26 proof only to measured RVC bits15,16,28."""
import hashlib,importlib.util,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cpu-rvc-bit26-v1';O=P/'cpu-rvc-bits-v2';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
I='AlignerPlugin_logic_extractors_0_ctx_instruction'
EQ='''  wire rvc_rs1_common = (switch_Rvc_l55==5'h02)||(switch_Rvc_l55==5'h03)||
    (switch_Rvc_l55==5'h05)||(switch_Rvc_l55==5'h06)||(switch_Rvc_l55==5'h07)||
    (switch_Rvc_l55==5'h08)||(switch_Rvc_l55==5'h0c)||(switch_Rvc_l55==5'h0e)||
    (switch_Rvc_l55==5'h0f)||(switch_Rvc_l55==5'h10)||
    ((switch_Rvc_l55==5'h14)&&((AlignerPlugin_logic_extractors_0_ctx_instruction[6:2]==5'd0)||AlignerPlugin_logic_extractors_0_ctx_instruction[12]));
  wire rvc_jump_sign = ((switch_Rvc_l55==5'h09)||(switch_Rvc_l55==5'h0d)) && AlignerPlugin_logic_extractors_0_ctx_instruction[12];
  wire rvc_instruction_bit15 =
    (rvc_rs1_common && AlignerPlugin_logic_extractors_0_ctx_instruction[7]) |
    rvc_jump_sign |
    ((switch_Rvc_l55==5'h0b)&&!rvc_rd_is_sp&&AlignerPlugin_logic_extractors_0_ctx_instruction[5]);
  wire rvc_instruction_bit16 =
    (rvc_rs1_common && AlignerPlugin_logic_extractors_0_ctx_instruction[8]) |
    rvc_jump_sign |
    (switch_Rvc_l55==5'h00)||(switch_Rvc_l55==5'h12)||(switch_Rvc_l55==5'h13)||
    (switch_Rvc_l55==5'h16)||(switch_Rvc_l55==5'h17)||
    ((switch_Rvc_l55==5'h0b)&&(rvc_rd_is_sp||AlignerPlugin_logic_extractors_0_ctx_instruction[6]));
  wire rvc_instruction_bit28 =
    (((switch_Rvc_l55==5'h00)||(switch_Rvc_l55==5'h09)||(switch_Rvc_l55==5'h0d)) && AlignerPlugin_logic_extractors_0_ctx_instruction[9]) |
    (((switch_Rvc_l55==5'h08)||(switch_Rvc_l55==5'h0a)||(switch_Rvc_l55==5'h0e)||(switch_Rvc_l55==5'h0f)||
      ((switch_Rvc_l55==5'h0b)&&!rvc_rd_is_sp)||
      ((switch_Rvc_l55==5'h0c)&&(AlignerPlugin_logic_extractors_0_ctx_instruction[11:10]==2'b10))) && AlignerPlugin_logic_extractors_0_ctx_instruction[12]) |
    ((switch_Rvc_l55==5'h0b)&&rvc_rd_is_sp&&AlignerPlugin_logic_extractors_0_ctx_instruction[4]);
'''
def prepare():
 O.mkdir(exist_ok=False);s=(BASE/'VexiiRiscv.v').read_text()
 marker='  assign AlignerPlugin_logic_feeder_lanes_0_isRvc =';assert s.count(marker)==1;s=s.replace(marker,EQ+'\n'+marker)
 assignment='      decode_ctrls_0_up_Decode_INSTRUCTION_0[26] = rvc_instruction_bit26;';assert s.count(assignment)==1
 s=s.replace(assignment,assignment+'\n'+''.join('      decode_ctrls_0_up_Decode_INSTRUCTION_0[%d] = rvc_instruction_bit%d;\n'%(b,b) for b in (15,16,28)).rstrip())
 (O/'VexiiRiscv.v').write_text(s)
 probe=(BASE/'probe.v').read_text();probe=probe.replace('assign old_instruction=',EQ+'\nassign old_instruction=')
 before='assign new_instruction=raw[1:0]==2\'b11 ? raw : {AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst[31:27],rvc_instruction_bit26,AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst[25:0]};'
 d='AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst'
 after="assign new_instruction=raw[1:0]==2'b11 ? raw : {"+d+'[31:29],rvc_instruction_bit28,'+d+'[27],rvc_instruction_bit26,'+d+'[25:17],rvc_instruction_bit16,rvc_instruction_bit15,'+d+'[14:0]};'
 assert probe.count(before)==1;probe=probe.replace(before,after);(O/'probe.v').write_text(probe)
 (O/'prepared.json').write_text(json.dumps(dict(parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),changed_instruction_bits=[15,16,26,28],added_state_bits=0,added_cycles=0,original_illegal_flag_unchanged=True,full32_raw_bypass_unchanged=True),indent=2)+'\n')
 shutil.copy2(__file__,O/'recipe.py')
def prove():
 spec=importlib.util.spec_from_file_location('rvc26',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_bit26.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.O=O;m.prove()
if __name__=='__main__':globals()[sys.argv[1]]()
