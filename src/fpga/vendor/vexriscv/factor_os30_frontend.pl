#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# os30-only literal scanner/INTWB/RS2 factoring for the registered RVC profile. Apply AFTER factor_os30_fp_rs3.pl.
# Hash-locked to the qualified output; unknown generator changes fail closed.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_frontend.pl os30 <staging-netlist>\n";
open my $in,'<:raw',$path or die "$path: $!\n";
my $source=do {local $/; <$in>}; close $in;
my $before='668f2c0c2e351c730ad4ccedf314408a7e5f37e5ab799d8327d9724f8be08c6e';
my $after='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295';
exit 0 if sha256_hex($source) eq $after;
sha256_hex($source) eq $before or die "Unqualified os30 frontend input; retaining prior output\n";
{
 my $old=<<'OLD2';
  assign AlignerPlugin_logic_buffer_usedMask = (AlignerPlugin_logic_extractors_0_valid ? AlignerPlugin_logic_extractors_0_usageMask : 4'b0000);
  assign AlignerPlugin_logic_buffer_haltUp = ((|(AlignerPlugin_logic_buffer_mask & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[1 : 0] : 2'b00)))) || AlignerPlugin_api_haltIt);
  // Factor early occupancy terms away from the late dispatch hazard.
  (* keep *) wire fetch_ready_allow = !AlignerPlugin_api_haltIt &&
      !(|(AlignerPlugin_logic_buffer_mask & ~AlignerPlugin_logic_buffer_usedMask[1:0]));
  (* keep *) wire fetch_ready_empty = !(|AlignerPlugin_logic_buffer_mask) ||
      !decode_ctrls_1_up_isValid || !decode_ctrls_2_up_isValid || decode_ctrls_0_up_isCancel;
  assign fetch_logic_ctrls_3_down_ready = !fetch_logic_ctrls_3_down_valid ||
OLD2
 my $new=<<'NEW2';
  assign AlignerPlugin_logic_buffer_usedMask = (AlignerPlugin_logic_extractors_0_valid ? AlignerPlugin_logic_extractors_0_usageMask : 4'b0000);
  assign AlignerPlugin_logic_buffer_haltUp = ((|(AlignerPlugin_logic_buffer_mask & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[1 : 0] : 2'b00)))) || AlignerPlugin_api_haltIt);
  // Factor early occupancy terms away from the late dispatch hazard.
  // Only the first two slices occupy the saved fetch buffer. Express its
  // complete consumption directly, without priority -> valid -> usage muxes.
  // Equivalent for every scanner-valid/width/mask combination (single lane).
  (* keep *) wire fetch_ready_allow = !AlignerPlugin_api_haltIt && (
      !(|AlignerPlugin_logic_buffer_mask) ||
      (AlignerPlugin_logic_scanners_0_valid &&
        (!AlignerPlugin_logic_buffer_mask[1] || AlignerPlugin_logic_scanners_0_checker_1_required)) ||
      (!AlignerPlugin_logic_scanners_0_valid && AlignerPlugin_logic_scanners_1_valid &&
        !AlignerPlugin_logic_buffer_mask[0] &&
        (!AlignerPlugin_logic_scanners_1_checker_1_required || fetch_logic_ctrls_3_down_valid)));
  (* keep *) wire fetch_ready_empty = !(|AlignerPlugin_logic_buffer_mask) ||
      !decode_ctrls_1_up_isValid || !decode_ctrls_2_up_isValid || decode_ctrls_0_up_isCancel;
  assign fetch_logic_ctrls_3_down_ready = !fetch_logic_ctrls_3_down_valid ||
NEW2
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified registered-profile block
";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'INTWBOLD';
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (lane0_integer_WriteBackPlugin_logic_stages_2_hits[0] ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);
INTWBOLD
 my $new=<<'INTWBNEW';
  // The formatter maps all-zero raw data to zero for every width/sign.
  // With no source hits its payload is already zero; retain only lane gating
  // on DATA. Architectural wb/write-valid, commit and cancellation are unchanged.
  assign lane0_integer_WriteBackPlugin_logic_stages_2_muxed = (execute_ctrl4_up_LANE_SEL_lane0 ? lane0_IntFormatPlugin_logic_stages_2_wb_payload : 32'h0);
INTWBNEW
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified registered-profile block
";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'RS2OLD';
  assign execute_ctrl1_down_float_RS2_lane0 = (((((_zz_execute_ctrl1_down_float_RS2_lane0 ? execute_ctrl4_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_1) | (_zz_execute_ctrl1_down_float_RS2_lane0_2 ? execute_ctrl5_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_3)) | ((_zz_execute_ctrl1_down_float_RS2_lane0_4 ? execute_ctrl6_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_5) | (_zz_execute_ctrl1_down_float_RS2_lane0_6 ? execute_ctrl7_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_7))) | (((_zz_execute_ctrl1_down_float_RS2_lane0_8 ? execute_ctrl8_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_9) | (_zz_execute_ctrl1_down_float_RS2_lane0_10 ? execute_ctrl9_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_11)) | ((_zz_execute_ctrl1_down_float_RS2_lane0_12 ? execute_ctrl10_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_13) | (_zz_execute_ctrl1_down_float_RS2_lane0_14 ? execute_ctrl11_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS2_lane0_15)))) | ((execute_lane0_bypasser_float_RS2_sel[8] ? execute_ctrl12_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (execute_lane0_bypasser_float_RS2_sel[9] ? execute_lane0_bypasser_float_RS2_port_data : 32'h0)));
RS2OLD
 my $new=<<'RS2NEW';
  // Exact earliest-stage FP RS2 forwarding, factored into local groups.
  wire [31:0] fp_rs2_group0 = execute_lane0_bypasser_float_RS2_bypassEnables[0] ? execute_ctrl4_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS2_bypassEnables[1] ? execute_ctrl5_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl6_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  wire [31:0] fp_rs2_group1 = execute_lane0_bypasser_float_RS2_bypassEnables[3] ? execute_ctrl7_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS2_bypassEnables[4] ? execute_ctrl8_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl9_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  wire [31:0] fp_rs2_group2 = execute_lane0_bypasser_float_RS2_bypassEnables[6] ? execute_ctrl10_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS2_bypassEnables[7] ? execute_ctrl11_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl12_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  assign execute_ctrl1_down_float_RS2_lane0 = (|execute_lane0_bypasser_float_RS2_bypassEnables[2:0]) ? fp_rs2_group0 :
    (|execute_lane0_bypasser_float_RS2_bypassEnables[5:3]) ? fp_rs2_group1 :
    (|execute_lane0_bypasser_float_RS2_bypassEnables[8:6]) ? fp_rs2_group2 :
    (execute_lane0_bypasser_float_RS2_bypassEnables[9] ? execute_lane0_bypasser_float_RS2_port_data : 32'h0);
RS2NEW
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified registered-profile block
";
 $source=~s/\Q$old\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected transformed frontend hash\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\n";
close $out or die "$temporary: $!\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\n";
rename($temporary,$path) or die "$path rename: $!\n";
print "os30 frontend exact output $after\n";
