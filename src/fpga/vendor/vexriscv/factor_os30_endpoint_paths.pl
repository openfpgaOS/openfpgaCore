#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# os30-only exact frontend endpoint/GShare BTB prehash factors. Apply AFTER factor_os30_parallel_paths.pl.
# Hash-locked to the qualified output; unknown generator changes fail closed.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_endpoint_paths.pl os30 <staging-netlist>\n";
open my $in,'<:raw',$path or die "$path: $!\n";
my $source=do {local $/; <$in>}; close $in;
my $before='3bbe3886be4edceb73b4e338314ee22c6f0777b9197689ba184d5c14d85cba96';
my $after='6f50382aa82e6684551756e6c2473492d0bd915a0c94b2aeae9ac4a47e62dec4';
exit 0 if sha256_hex($source) eq $after;
sha256_hex($source) eq $before or die "Unqualified os30 endpoint-control input; retaining prior output\n";
{
 my $old=<<'HUNK0OLD';
  assign when_AlignerPlugin_l256 = ((fetch_logic_ctrls_3_down_isValid && fetch_logic_ctrls_3_down_isReady) && (! fetch_logic_ctrls_3_down_isCancel));
HUNK0OLD
 my $new=<<'HUNK0NEW';
  // Precompute occupancy/qualification before the three late RS hazards.
  // Original ready/fire and capture data/flush ordering remain unchanged.
  (* keep *) wire os30_aligner_capture_free = fetch_logic_ctrls_3_down_valid &&
      fetch_ready_allow && (fetch_ready_empty || os30_decode2_ready_free);
  (* keep *) wire os30_aligner_capture_allow = fetch_logic_ctrls_3_down_valid &&
      fetch_ready_allow && os30_decode2_ready_allow;
  assign when_AlignerPlugin_l256 = os30_aligner_capture_free ||
      (os30_aligner_capture_allow && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_2_hazard);
  (* keep *) wire os30_aligner_mask_last_free = os30_frontend_ready_free ||
      (fetch_logic_ctrls_3_down_valid && !AlignerPlugin_api_haltIt &&
       !(|AlignerPlugin_logic_buffer_mask));
  (* keep *) wire os30_aligner_mask_last_update = os30_aligner_mask_last_free ||
      (os30_decode2_ready_allow && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_2_hazard);
HUNK0NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK1OLD';
  always @(*) begin
    decode_ctrls_0_down_ready = decode_ctrls_1_up_ready;
    if(when_StageLink_l71_2) begin
      decode_ctrls_0_down_ready = 1'b1;
    end
  end
HUNK1OLD
 my $new=<<'HUNK1NEW';
  // Exact empty-stage bypass, expanded before the late hazards.
  (* keep *) wire os30_frontend_ready_free = !decode_ctrls_1_up_isValid ||
      !decode_ctrls_2_up_isValid || os30_decode2_ready_free;
  always @(*) begin
    decode_ctrls_0_down_ready = os30_frontend_ready_free ||
        (os30_decode2_ready_allow && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_2_hazard);
  end
HUNK1NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK2OLD';
      if(AlignerPlugin_logic_buffer_downFire) begin
        AlignerPlugin_logic_buffer_mask <= (AlignerPlugin_logic_buffer_mask & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
        AlignerPlugin_logic_buffer_last <= (AlignerPlugin_logic_buffer_last & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
      end
      if(when_AlignerPlugin_l256) begin
        AlignerPlugin_logic_buffer_mask <= (fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[3 : 2] : 2'b00)));
        AlignerPlugin_logic_buffer_trap <= fetch_logic_ctrls_3_down_TRAP;
        AlignerPlugin_logic_buffer_last <= fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
      end
HUNK2OLD
 chop $old;
 my $new=<<'HUNK2NEW';
      if(os30_aligner_mask_last_update) begin
        if(when_AlignerPlugin_l256) begin
          AlignerPlugin_logic_buffer_mask <= (fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_MASK & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[3 : 2] : 2'b00)));
          AlignerPlugin_logic_buffer_last <= fetch_logic_ctrls_3_down_AlignerPlugin_logic_FETCH_LAST;
        end else begin
          AlignerPlugin_logic_buffer_mask <= (AlignerPlugin_logic_buffer_mask & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
          AlignerPlugin_logic_buffer_last <= (AlignerPlugin_logic_buffer_last & (~ AlignerPlugin_logic_buffer_usedMask[1 : 0]));
        end
      end
      if(when_AlignerPlugin_l256) begin
        AlignerPlugin_logic_buffer_trap <= fetch_logic_ctrls_3_down_TRAP;
      end
HUNK2NEW
 chop $new;

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK3OLD';
  // Precompute the two priority groups before the late branch redirect.
  (* keep *) wire [12:0] os30_gshare_hash_low = {_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[0],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[1],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[2],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[3],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[4],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[5],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_1,{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_2,_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_3}}}}}}}} ^
      {1'b0, (HistoryPlugin_logic_onFetch_ports_0_valid ? HistoryPlugin_logic_onFetch_ports_0_payload_history : HistoryPlugin_logic_onFetch_value)};
  (* keep *) wire [12:0] os30_gshare_hash_high = {_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[0],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[1],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[2],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[3],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[4],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[5],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_1,{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_2,_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_3}}}}}}}} ^
      {1'b0, (HistoryPlugin_logic_onFetch_ports_2_valid ? HistoryPlugin_logic_onFetch_ports_2_payload_history : HistoryPlugin_logic_onFetch_ports_1_payload_history)};
  assign fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH = (HistoryPlugin_logic_onFetch_ports_2_valid || HistoryPlugin_logic_onFetch_ports_1_valid) ?
      os30_gshare_hash_high : os30_gshare_hash_low;
HUNK3OLD
 my $new=<<'HUNK3NEW';
  // Cofactor only the GShare address on the BTB history event.
  // Branch PC/history valids alias; trap PC/history valids stay independent.
  // A hit can update history without taking the BTB target. No state is added.
  wire [31:0] os30_gshare_pc_miss = TrapPlugin_logic_harts_0_trap_pcPort_valid ? TrapPlugin_logic_harts_0_trap_pcPort_payload_pc :
      (early0_BranchPlugin_logic_pcPort_valid ? early0_BranchPlugin_logic_pcPort_payload_pc : PcPlugin_logic_harts_0_self_flow_payload_pc);
  wire [31:0] os30_gshare_pc_hit = TrapPlugin_logic_harts_0_trap_pcPort_valid ? TrapPlugin_logic_harts_0_trap_pcPort_payload_pc :
      (early0_BranchPlugin_logic_pcPort_valid ? early0_BranchPlugin_logic_pcPort_payload_pc :
       (fetch_logic_ctrls_2_down_BtbPlugin_logic_chunksLogic_0_predict_TAKEN ? BtbPlugin_logic_pcPort_payload_pc : PcPlugin_logic_harts_0_self_flow_payload_pc));
  wire [11:0] os30_gshare_hist_miss = HistoryPlugin_logic_onFetch_ports_2_valid ? HistoryPlugin_logic_onFetch_ports_2_payload_history :
      (HistoryPlugin_logic_onFetch_ports_1_valid ? HistoryPlugin_logic_onFetch_ports_1_payload_history : HistoryPlugin_logic_onFetch_value);
  wire [11:0] os30_gshare_hist_hit = HistoryPlugin_logic_onFetch_ports_2_valid ? HistoryPlugin_logic_onFetch_ports_2_payload_history :
      (HistoryPlugin_logic_onFetch_ports_1_valid ? HistoryPlugin_logic_onFetch_ports_1_payload_history :
       (fetch_logic_ctrls_2_down_BtbPlugin_logic_chunksLogic_0_readRsp_ENTRY_isBranch ?
        {fetch_logic_ctrls_2_down_Prediction_BRANCH_HISTORY[10:0],fetch_logic_ctrls_2_down_BtbPlugin_logic_chunksLogic_0_predict_TAKEN} :
        fetch_logic_ctrls_2_down_Prediction_BRANCH_HISTORY));
  (* keep *) wire [12:0] os30_gshare_hash_miss = {os30_gshare_pc_miss[2],os30_gshare_pc_miss[3],os30_gshare_pc_miss[4],os30_gshare_pc_miss[5],os30_gshare_pc_miss[6],os30_gshare_pc_miss[7],os30_gshare_pc_miss[8],os30_gshare_pc_miss[9],os30_gshare_pc_miss[10],os30_gshare_pc_miss[11],os30_gshare_pc_miss[12],os30_gshare_pc_miss[13],os30_gshare_pc_miss[14]} ^ {1'b0,os30_gshare_hist_miss};
  (* keep *) wire [12:0] os30_gshare_hash_hit = {os30_gshare_pc_hit[2],os30_gshare_pc_hit[3],os30_gshare_pc_hit[4],os30_gshare_pc_hit[5],os30_gshare_pc_hit[6],os30_gshare_pc_hit[7],os30_gshare_pc_hit[8],os30_gshare_pc_hit[9],os30_gshare_pc_hit[10],os30_gshare_pc_hit[11],os30_gshare_pc_hit[12],os30_gshare_pc_hit[13],os30_gshare_pc_hit[14]} ^ {1'b0,os30_gshare_hist_hit};
  assign fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH = HistoryPlugin_logic_onFetch_ports_0_valid ? os30_gshare_hash_hit : os30_gshare_hash_miss;
HUNK3NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected transformed endpoint-control hash\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\n";
close $out or die "$temporary: $!\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\n";
rename($temporary,$path) or die "$path rename: $!\n";
print "os30 endpoint-control exact output $after\n";
