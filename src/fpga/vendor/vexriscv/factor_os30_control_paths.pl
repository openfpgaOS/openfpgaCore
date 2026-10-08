#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# os30-only exact branch/LSU-sign/RS3-tag/decode-ready factors. Apply AFTER factor_os30_load_forward.pl.
# Hash-locked to the qualified output; unknown generator changes fail closed.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_control_paths.pl os30 <staging-netlist>\n";
open my $in,'<:raw',$path or die "$path: $!\n";
my $source=do {local $/; <$in>}; close $in;
my $before='82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534';
my $after='878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e';
exit 0 if sha256_hex($source) eq $after;
sha256_hex($source) eq $before or die "Unqualified os30 control-path input; retaining prior output\n";
{
 my $old=<<'HUNK0OLD';
  assign execute_ctrl2_down_early0_BranchPlugin_logic_alu_btb_BAD_TARGET_lane0 = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0 != execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0);
HUNK0OLD
 my $new=<<'HUNK0NEW';
  // Bound the equality reduction depth; target calculation and pipeline stay unchanged.
  (* keep *) wire [7:0] os30_btb_target_eq4;
  (* keep *) wire [1:0] os30_btb_target_eq16;
  assign os30_btb_target_eq4[0] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[3:0] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[3:0]);
  assign os30_btb_target_eq4[1] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[7:4] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[7:4]);
  assign os30_btb_target_eq4[2] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[11:8] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[11:8]);
  assign os30_btb_target_eq4[3] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[15:12] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[15:12]);
  assign os30_btb_target_eq4[4] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[19:16] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[19:16]);
  assign os30_btb_target_eq4[5] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[23:20] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[23:20]);
  assign os30_btb_target_eq4[6] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[27:24] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[27:24]);
  assign os30_btb_target_eq4[7] = (execute_ctrl2_down_Prediction_ALIGNED_JUMPED_PC_lane0[31:28] == execute_ctrl2_down_early0_BranchPlugin_pcCalc_PC_TRUE_lane0[31:28]);
  assign os30_btb_target_eq16[0] = &os30_btb_target_eq4[3:0];
  assign os30_btb_target_eq16[1] = &os30_btb_target_eq4[7:4];
  assign execute_ctrl2_down_early0_BranchPlugin_logic_alu_btb_BAD_TARGET_lane0 = !(&os30_btb_target_eq16);
HUNK0NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK1OLD';
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (execute_ctrl4_up_LANE_SEL_lane0 ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);
HUNK1OLD
 my $new=<<'HUNK1NEW';
  // Alignment and sign selection are linear in each data source. Precompute
  // only the scalar extension bits, then apply the late cache-way hit mask.
  // Existing raw data, SC low byte, FP loads and all write controls stay intact.
  function os30_load_byte_sign;
    input [31:0] data;
    input [1:0] address;
    begin
      case (address)
        2'b00: os30_load_byte_sign = data[7];
        2'b01: os30_load_byte_sign = data[15];
        2'b10: os30_load_byte_sign = data[23];
        default: os30_load_byte_sign = data[31];
      endcase
    end
  endfunction
  function os30_load_half_sign;
    input [31:0] data;
    input address;
    begin
      os30_load_half_sign = address ? data[31] : data[15];
    end
  endfunction
  wire os30_load_nonload_bit7 =
      (early0_MulPlugin_logic_formatBus_valid && early0_MulPlugin_logic_formatBus_payload[7]) ||
      (FpuF2iPlugin_logic_iwb_valid && FpuF2iPlugin_logic_iwb_payload[7]);
  wire os30_load_nonload_bit15 =
      (early0_MulPlugin_logic_formatBus_valid && early0_MulPlugin_logic_formatBus_payload[15]) ||
      (FpuF2iPlugin_logic_iwb_valid && FpuF2iPlugin_logic_iwb_payload[15]);
  (* keep *) wire os30_load_sign_byte_way0 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
      LsuPlugin_logic_iwb_valid && !when_LsuPlugin_l1025 && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0,
          execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]);
  (* keep *) wire os30_load_sign_byte_way1 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
      LsuPlugin_logic_iwb_valid && !when_LsuPlugin_l1025 && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1,
          execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0]);
  (* keep *) wire os30_load_sign_upper_way0 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      LsuPlugin_logic_iwb_valid && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      (((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
        !when_LsuPlugin_l1025 && os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0])) ||
       ((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01) &&
        os30_load_half_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_0,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1])));
  (* keep *) wire os30_load_sign_upper_way1 =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      LsuPlugin_logic_iwb_valid && !LsuPlugin_logic_onCtrl_io_cmdSent &&
      (((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
        !when_LsuPlugin_l1025 && os30_load_byte_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0])) ||
       ((execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01) &&
        os30_load_half_sign(execute_ctrl4_down_LsuL1Plugin_logic_BANKS_MUXES_lane0_1,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1])));
  (* keep *) wire os30_load_sign_byte_other =
      execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b00) &&
      (os30_load_nonload_bit7 || (LsuPlugin_logic_iwb_valid && !when_LsuPlugin_l1025 &&
       LsuPlugin_logic_onCtrl_io_cmdSent && os30_load_byte_sign(LsuPlugin_logic_onCtrl_io_rsp_payload_data,
           execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1:0])));
  (* keep *) wire os30_load_sign_upper_other = os30_load_sign_byte_other ||
      (execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_down_lane0_IntFormatPlugin_logic_SIGNED_lane0 &&
       (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 == 2'b01) &&
       (os30_load_nonload_bit15 || (LsuPlugin_logic_iwb_valid && LsuPlugin_logic_onCtrl_io_cmdSent &&
        os30_load_half_sign(LsuPlugin_logic_onCtrl_io_rsp_payload_data,
            execute_ctrl4_down_LsuL1_MIXED_ADDRESS_lane0[1]))));
  (* keep *) wire os30_load_sign_byte =
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[0] && os30_load_sign_byte_way0) ||
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1] && os30_load_sign_byte_way1) ||
      os30_load_sign_byte_other;
  (* keep *) wire os30_load_sign_upper =
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[0] && os30_load_sign_upper_way0) ||
      (execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0[1] && os30_load_sign_upper_way1) ||
      os30_load_sign_upper_other;
  (* keep *) wire os30_load_copy_byte = execute_ctrl4_up_LANE_SEL_lane0 &&
      (execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0 != 2'b00);
  (* keep *) wire os30_load_copy_upper = execute_ctrl4_up_LANE_SEL_lane0 &&
      execute_ctrl4_down_lane0_IntFormatPlugin_logic_WIDTH_ID_lane0[1];
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = {
      (({16{os30_load_copy_upper}} & lane0_IntFormatPlugin_logic_stages_2_raw[31:16]) |
       {16{os30_load_sign_upper}}),
      (({8{os30_load_copy_byte}} & lane0_IntFormatPlugin_logic_stages_2_raw[15:8]) |
       {8{os30_load_sign_byte}}),
      ({8{execute_ctrl4_up_LANE_SEL_lane0}} & lane0_IntFormatPlugin_logic_stages_2_raw[7:0])};
HUNK1NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK2OLD';
    execute_lane0_bypasser_float_RS3_bypassEnables[5] = (((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && (execute_ctrl9_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl9_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
HUNK2OLD
 my $new=<<'HUNK2NEW';
    execute_lane0_bypasser_float_RS3_bypassEnables[5] = ((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl9_tag_lo_eq && os30_fp_rs3_ctrl9_tag_hi_eq);
HUNK2NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK3OLD';
  always @(*) begin
    execute_lane0_bypasser_float_RS3_bypassEnables[0]
HUNK3OLD
 chop $old;
 my $new=<<'HUNK3NEW';
  // Exact six-bit tag match; retain all ownership and forwarding priority.
  (* keep *) wire os30_fp_rs3_ctrl9_tag_lo_eq = (execute_ctrl9_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl9_tag_hi_eq = ({execute_ctrl9_down_RD_RFID_lane0, execute_ctrl9_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  always @(*) begin
    execute_lane0_bypasser_float_RS3_bypassEnables[0]
HUNK3NEW
 chop $new;

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK4OLD';
  always @(*) begin
    decode_ctrls_2_down_ready = decode_ctrls_3_up_ready;
    if(when_StageLink_l71_4) begin
      decode_ctrls_2_down_ready = 1'b1;
    end
  end
HUNK4OLD
 my $new=<<'HUNK4NEW';
  // Same stage2-to3 ready condition, with RS3 hazard entering last.
  (* keep *) wire os30_decode2_ready_free = !decode_ctrls_3_up_isValid ||
      !decode_ctrls_3_up_LANE_SEL_0 || DispatchPlugin_logic_feeds_0_sent;
  (* keep *) wire os30_decode2_ready_allow = !DispatchPlugin_logic_candidates_0_flushHazards &&
      !DispatchPlugin_logic_candidates_0_fenceOlderHazards && DispatchPlugin_logic_candidates_0_ctx_laneLayerHits[0] &&
      !DispatchPlugin_logic_candidates_0_reservationHazards[0] && !execute_freeze_valid && !DispatchPlugin_api_haltDispatch &&
      !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard;
  always @(*) begin
    decode_ctrls_2_down_ready = os30_decode2_ready_free ||
        (os30_decode2_ready_allow && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_2_hazard);
  end
HUNK4NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected transformed control-path hash\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\n";
close $out or die "$temporary: $!\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\n";
rename($temporary,$path) or die "$path rename: $!\n";
print "os30 control-path exact output $after\n";
