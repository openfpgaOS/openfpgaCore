#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# os30-only exact uniform FP/ready/GShare factors. Apply AFTER factor_os30_control_paths.pl.
# Hash-locked to the qualified output; unknown generator changes fail closed.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_parallel_paths.pl os30 <staging-netlist>\n";
open my $in,'<:raw',$path or die "$path: $!\n";
my $source=do {local $/; <$in>}; close $in;
my $before='878e6978d7bceb10c9c5323c983c025f32eff2fdde4e24369c4e114d7edf230e';
my $after='3bbe3886be4edceb73b4e338314ee22c6f0777b9197689ba184d5c14d85cba96';
exit 0 if sha256_hex($source) eq $after;
sha256_hex($source) eq $before or die "Unqualified os30 parallel-control input; retaining prior output\n";
{
 my $old=<<'HUNK0OLD';
  always @(*) begin
    execute_lane0_bypasser_float_RS1_bypassEnables[0] = (((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_up_RD_ENABLE_lane0) && (execute_ctrl4_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl4_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[1] = (((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && (execute_ctrl5_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl5_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[2] = (((execute_ctrl6_up_LANE_SEL_lane0 && execute_ctrl6_up_RD_ENABLE_lane0) && (execute_ctrl6_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl6_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[3] = (((execute_ctrl7_up_LANE_SEL_lane0 && execute_ctrl7_up_RD_ENABLE_lane0) && (execute_ctrl7_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl7_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[4] = (((execute_ctrl8_up_LANE_SEL_lane0 && execute_ctrl8_up_RD_ENABLE_lane0) && (execute_ctrl8_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl8_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[5] = (((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && (execute_ctrl9_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl9_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[6] = (((execute_ctrl10_up_LANE_SEL_lane0 && execute_ctrl10_up_RD_ENABLE_lane0) && (execute_ctrl10_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl10_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[7] = (((execute_ctrl11_up_LANE_SEL_lane0 && execute_ctrl11_up_RD_ENABLE_lane0) && (execute_ctrl11_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl11_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[8] = (((execute_ctrl12_up_LANE_SEL_lane0 && execute_ctrl12_up_RD_ENABLE_lane0) && (execute_ctrl12_down_RD_PHYS_lane0 == execute_ctrl1_down_RS1_PHYS_lane0)) && (execute_ctrl12_down_RD_RFID_lane0 == execute_ctrl1_down_RS1_RFID_lane0));
    execute_lane0_bypasser_float_RS1_bypassEnables[9] = 1'b1;
  end
HUNK0OLD
 chop $old;
 my $new=<<'HUNK0NEW';
  // Uniform exact six-bit FP tag match; no change to ownership or priority.
  (* keep *) wire os30_fp_rs1_ctrl4_tag_lo_eq = (execute_ctrl4_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl4_tag_hi_eq = ({execute_ctrl4_down_RD_RFID_lane0, execute_ctrl4_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl5_tag_lo_eq = (execute_ctrl5_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl5_tag_hi_eq = ({execute_ctrl5_down_RD_RFID_lane0, execute_ctrl5_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl6_tag_lo_eq = (execute_ctrl6_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl6_tag_hi_eq = ({execute_ctrl6_down_RD_RFID_lane0, execute_ctrl6_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl7_tag_lo_eq = (execute_ctrl7_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl7_tag_hi_eq = ({execute_ctrl7_down_RD_RFID_lane0, execute_ctrl7_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl8_tag_lo_eq = (execute_ctrl8_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl8_tag_hi_eq = ({execute_ctrl8_down_RD_RFID_lane0, execute_ctrl8_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl9_tag_lo_eq = (execute_ctrl9_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl9_tag_hi_eq = ({execute_ctrl9_down_RD_RFID_lane0, execute_ctrl9_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl10_tag_lo_eq = (execute_ctrl10_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl10_tag_hi_eq = ({execute_ctrl10_down_RD_RFID_lane0, execute_ctrl10_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl11_tag_lo_eq = (execute_ctrl11_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl11_tag_hi_eq = ({execute_ctrl11_down_RD_RFID_lane0, execute_ctrl11_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs1_ctrl12_tag_lo_eq = (execute_ctrl12_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS1_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs1_ctrl12_tag_hi_eq = ({execute_ctrl12_down_RD_RFID_lane0, execute_ctrl12_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS1_RFID_lane0, execute_ctrl1_down_RS1_PHYS_lane0[4:3]});
  always @(*) begin
    execute_lane0_bypasser_float_RS1_bypassEnables[0] = ((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl4_tag_lo_eq && os30_fp_rs1_ctrl4_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[1] = ((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl5_tag_lo_eq && os30_fp_rs1_ctrl5_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[2] = ((execute_ctrl6_up_LANE_SEL_lane0 && execute_ctrl6_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl6_tag_lo_eq && os30_fp_rs1_ctrl6_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[3] = ((execute_ctrl7_up_LANE_SEL_lane0 && execute_ctrl7_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl7_tag_lo_eq && os30_fp_rs1_ctrl7_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[4] = ((execute_ctrl8_up_LANE_SEL_lane0 && execute_ctrl8_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl8_tag_lo_eq && os30_fp_rs1_ctrl8_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[5] = ((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl9_tag_lo_eq && os30_fp_rs1_ctrl9_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[6] = ((execute_ctrl10_up_LANE_SEL_lane0 && execute_ctrl10_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl10_tag_lo_eq && os30_fp_rs1_ctrl10_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[7] = ((execute_ctrl11_up_LANE_SEL_lane0 && execute_ctrl11_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl11_tag_lo_eq && os30_fp_rs1_ctrl11_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[8] = ((execute_ctrl12_up_LANE_SEL_lane0 && execute_ctrl12_up_RD_ENABLE_lane0) && os30_fp_rs1_ctrl12_tag_lo_eq && os30_fp_rs1_ctrl12_tag_hi_eq);
    execute_lane0_bypasser_float_RS1_bypassEnables[9] = 1'b1;
  end
HUNK0NEW
 chop $new;

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK1OLD';
  always @(*) begin
    execute_lane0_bypasser_float_RS2_bypassEnables[0] = (((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_up_RD_ENABLE_lane0) && (execute_ctrl4_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl4_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[1] = (((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && (execute_ctrl5_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl5_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[2] = (((execute_ctrl6_up_LANE_SEL_lane0 && execute_ctrl6_up_RD_ENABLE_lane0) && (execute_ctrl6_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl6_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[3] = (((execute_ctrl7_up_LANE_SEL_lane0 && execute_ctrl7_up_RD_ENABLE_lane0) && (execute_ctrl7_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl7_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[4] = (((execute_ctrl8_up_LANE_SEL_lane0 && execute_ctrl8_up_RD_ENABLE_lane0) && (execute_ctrl8_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl8_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[5] = (((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && (execute_ctrl9_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl9_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[6] = (((execute_ctrl10_up_LANE_SEL_lane0 && execute_ctrl10_up_RD_ENABLE_lane0) && (execute_ctrl10_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl10_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[7] = (((execute_ctrl11_up_LANE_SEL_lane0 && execute_ctrl11_up_RD_ENABLE_lane0) && (execute_ctrl11_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl11_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[8] = (((execute_ctrl12_up_LANE_SEL_lane0 && execute_ctrl12_up_RD_ENABLE_lane0) && (execute_ctrl12_down_RD_PHYS_lane0 == execute_ctrl1_down_RS2_PHYS_lane0)) && (execute_ctrl12_down_RD_RFID_lane0 == execute_ctrl1_down_RS2_RFID_lane0));
    execute_lane0_bypasser_float_RS2_bypassEnables[9] = 1'b1;
  end
HUNK1OLD
 chop $old;
 my $new=<<'HUNK1NEW';
  // Uniform exact six-bit FP tag match; no change to ownership or priority.
  (* keep *) wire os30_fp_rs2_ctrl4_tag_lo_eq = (execute_ctrl4_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl4_tag_hi_eq = ({execute_ctrl4_down_RD_RFID_lane0, execute_ctrl4_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl5_tag_lo_eq = (execute_ctrl5_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl5_tag_hi_eq = ({execute_ctrl5_down_RD_RFID_lane0, execute_ctrl5_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl6_tag_lo_eq = (execute_ctrl6_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl6_tag_hi_eq = ({execute_ctrl6_down_RD_RFID_lane0, execute_ctrl6_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl7_tag_lo_eq = (execute_ctrl7_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl7_tag_hi_eq = ({execute_ctrl7_down_RD_RFID_lane0, execute_ctrl7_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl8_tag_lo_eq = (execute_ctrl8_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl8_tag_hi_eq = ({execute_ctrl8_down_RD_RFID_lane0, execute_ctrl8_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl9_tag_lo_eq = (execute_ctrl9_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl9_tag_hi_eq = ({execute_ctrl9_down_RD_RFID_lane0, execute_ctrl9_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl10_tag_lo_eq = (execute_ctrl10_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl10_tag_hi_eq = ({execute_ctrl10_down_RD_RFID_lane0, execute_ctrl10_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl11_tag_lo_eq = (execute_ctrl11_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl11_tag_hi_eq = ({execute_ctrl11_down_RD_RFID_lane0, execute_ctrl11_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs2_ctrl12_tag_lo_eq = (execute_ctrl12_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS2_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs2_ctrl12_tag_hi_eq = ({execute_ctrl12_down_RD_RFID_lane0, execute_ctrl12_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS2_RFID_lane0, execute_ctrl1_down_RS2_PHYS_lane0[4:3]});
  always @(*) begin
    execute_lane0_bypasser_float_RS2_bypassEnables[0] = ((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl4_tag_lo_eq && os30_fp_rs2_ctrl4_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[1] = ((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl5_tag_lo_eq && os30_fp_rs2_ctrl5_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[2] = ((execute_ctrl6_up_LANE_SEL_lane0 && execute_ctrl6_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl6_tag_lo_eq && os30_fp_rs2_ctrl6_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[3] = ((execute_ctrl7_up_LANE_SEL_lane0 && execute_ctrl7_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl7_tag_lo_eq && os30_fp_rs2_ctrl7_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[4] = ((execute_ctrl8_up_LANE_SEL_lane0 && execute_ctrl8_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl8_tag_lo_eq && os30_fp_rs2_ctrl8_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[5] = ((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl9_tag_lo_eq && os30_fp_rs2_ctrl9_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[6] = ((execute_ctrl10_up_LANE_SEL_lane0 && execute_ctrl10_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl10_tag_lo_eq && os30_fp_rs2_ctrl10_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[7] = ((execute_ctrl11_up_LANE_SEL_lane0 && execute_ctrl11_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl11_tag_lo_eq && os30_fp_rs2_ctrl11_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[8] = ((execute_ctrl12_up_LANE_SEL_lane0 && execute_ctrl12_up_RD_ENABLE_lane0) && os30_fp_rs2_ctrl12_tag_lo_eq && os30_fp_rs2_ctrl12_tag_hi_eq);
    execute_lane0_bypasser_float_RS2_bypassEnables[9] = 1'b1;
  end
HUNK1NEW
 chop $new;

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK2OLD';
  always @(*) begin
    execute_lane0_bypasser_float_RS3_bypassEnables[0] = (((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_up_RD_ENABLE_lane0) && (execute_ctrl4_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl4_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[1] = (((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && (execute_ctrl5_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl5_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[2] = (((execute_ctrl6_up_LANE_SEL_lane0 && execute_ctrl6_up_RD_ENABLE_lane0) && (execute_ctrl6_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl6_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[3] = (((execute_ctrl7_up_LANE_SEL_lane0 && execute_ctrl7_up_RD_ENABLE_lane0) && (execute_ctrl7_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl7_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[4] = (((execute_ctrl8_up_LANE_SEL_lane0 && execute_ctrl8_up_RD_ENABLE_lane0) && (execute_ctrl8_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl8_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[5] = ((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl9_tag_lo_eq && os30_fp_rs3_ctrl9_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[6] = (((execute_ctrl10_up_LANE_SEL_lane0 && execute_ctrl10_up_RD_ENABLE_lane0) && (execute_ctrl10_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl10_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[7] = (((execute_ctrl11_up_LANE_SEL_lane0 && execute_ctrl11_up_RD_ENABLE_lane0) && (execute_ctrl11_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl11_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[8] = (((execute_ctrl12_up_LANE_SEL_lane0 && execute_ctrl12_up_RD_ENABLE_lane0) && (execute_ctrl12_down_RD_PHYS_lane0 == execute_ctrl1_down_RS3_PHYS_lane0)) && (execute_ctrl12_down_RD_RFID_lane0 == execute_ctrl1_down_RS3_RFID_lane0));
    execute_lane0_bypasser_float_RS3_bypassEnables[9] = 1'b1;
  end
HUNK2OLD
 chop $old;
 my $new=<<'HUNK2NEW';
  // Uniform exact six-bit FP tag match; no change to ownership or priority.
  (* keep *) wire os30_fp_rs3_ctrl4_tag_lo_eq = (execute_ctrl4_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl4_tag_hi_eq = ({execute_ctrl4_down_RD_RFID_lane0, execute_ctrl4_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl5_tag_lo_eq = (execute_ctrl5_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl5_tag_hi_eq = ({execute_ctrl5_down_RD_RFID_lane0, execute_ctrl5_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl6_tag_lo_eq = (execute_ctrl6_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl6_tag_hi_eq = ({execute_ctrl6_down_RD_RFID_lane0, execute_ctrl6_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl7_tag_lo_eq = (execute_ctrl7_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl7_tag_hi_eq = ({execute_ctrl7_down_RD_RFID_lane0, execute_ctrl7_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl8_tag_lo_eq = (execute_ctrl8_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl8_tag_hi_eq = ({execute_ctrl8_down_RD_RFID_lane0, execute_ctrl8_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl10_tag_lo_eq = (execute_ctrl10_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl10_tag_hi_eq = ({execute_ctrl10_down_RD_RFID_lane0, execute_ctrl10_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl11_tag_lo_eq = (execute_ctrl11_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl11_tag_hi_eq = ({execute_ctrl11_down_RD_RFID_lane0, execute_ctrl11_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  (* keep *) wire os30_fp_rs3_ctrl12_tag_lo_eq = (execute_ctrl12_down_RD_PHYS_lane0[2:0] == execute_ctrl1_down_RS3_PHYS_lane0[2:0]);
  (* keep *) wire os30_fp_rs3_ctrl12_tag_hi_eq = ({execute_ctrl12_down_RD_RFID_lane0, execute_ctrl12_down_RD_PHYS_lane0[4:3]} == {execute_ctrl1_down_RS3_RFID_lane0, execute_ctrl1_down_RS3_PHYS_lane0[4:3]});
  always @(*) begin
    execute_lane0_bypasser_float_RS3_bypassEnables[0] = ((execute_ctrl4_up_LANE_SEL_lane0 && execute_ctrl4_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl4_tag_lo_eq && os30_fp_rs3_ctrl4_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[1] = ((execute_ctrl5_up_LANE_SEL_lane0 && execute_ctrl5_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl5_tag_lo_eq && os30_fp_rs3_ctrl5_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[2] = ((execute_ctrl6_up_LANE_SEL_lane0 && execute_ctrl6_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl6_tag_lo_eq && os30_fp_rs3_ctrl6_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[3] = ((execute_ctrl7_up_LANE_SEL_lane0 && execute_ctrl7_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl7_tag_lo_eq && os30_fp_rs3_ctrl7_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[4] = ((execute_ctrl8_up_LANE_SEL_lane0 && execute_ctrl8_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl8_tag_lo_eq && os30_fp_rs3_ctrl8_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[5] = ((execute_ctrl9_up_LANE_SEL_lane0 && execute_ctrl9_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl9_tag_lo_eq && os30_fp_rs3_ctrl9_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[6] = ((execute_ctrl10_up_LANE_SEL_lane0 && execute_ctrl10_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl10_tag_lo_eq && os30_fp_rs3_ctrl10_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[7] = ((execute_ctrl11_up_LANE_SEL_lane0 && execute_ctrl11_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl11_tag_lo_eq && os30_fp_rs3_ctrl11_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[8] = ((execute_ctrl12_up_LANE_SEL_lane0 && execute_ctrl12_up_RD_ENABLE_lane0) && os30_fp_rs3_ctrl12_tag_lo_eq && os30_fp_rs3_ctrl12_tag_hi_eq);
    execute_lane0_bypasser_float_RS3_bypassEnables[9] = 1'b1;
  end
HUNK2NEW
 chop $new;

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK3OLD';
  assign execute_ctrl1_down_float_RS1_lane0 = (((((_zz_execute_ctrl1_down_float_RS1_lane0 ? execute_ctrl4_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_1) | (_zz_execute_ctrl1_down_float_RS1_lane0_2 ? execute_ctrl5_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_3)) | ((_zz_execute_ctrl1_down_float_RS1_lane0_4 ? execute_ctrl6_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_5) | (_zz_execute_ctrl1_down_float_RS1_lane0_6 ? execute_ctrl7_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_7))) | (((_zz_execute_ctrl1_down_float_RS1_lane0_8 ? execute_ctrl8_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_9) | (_zz_execute_ctrl1_down_float_RS1_lane0_10 ? execute_ctrl9_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_11)) | ((_zz_execute_ctrl1_down_float_RS1_lane0_12 ? execute_ctrl10_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_13) | (_zz_execute_ctrl1_down_float_RS1_lane0_14 ? execute_ctrl11_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS1_lane0_15)))) | ((execute_lane0_bypasser_float_RS1_sel[8] ? execute_ctrl12_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (execute_lane0_bypasser_float_RS1_sel[9] ? execute_lane0_bypasser_float_RS1_port_data : 32'h0)));
HUNK3OLD
 chop $old;
 my $new=<<'HUNK3NEW';
  // Exact earliest-stage FP RS1 forwarding, factored into local groups.
  wire [31:0] fp_rs1_group0 = execute_lane0_bypasser_float_RS1_bypassEnables[0] ? execute_ctrl4_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS1_bypassEnables[1] ? execute_ctrl5_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl6_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  wire [31:0] fp_rs1_group1 = execute_lane0_bypasser_float_RS1_bypassEnables[3] ? execute_ctrl7_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS1_bypassEnables[4] ? execute_ctrl8_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl9_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  wire [31:0] fp_rs1_group2 = execute_lane0_bypasser_float_RS1_bypassEnables[6] ? execute_ctrl10_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS1_bypassEnables[7] ? execute_ctrl11_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl12_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  assign execute_ctrl1_down_float_RS1_lane0 = (|execute_lane0_bypasser_float_RS1_bypassEnables[2:0]) ? fp_rs1_group0 :
    (|execute_lane0_bypasser_float_RS1_bypassEnables[5:3]) ? fp_rs1_group1 :
    (|execute_lane0_bypasser_float_RS1_bypassEnables[8:6]) ? fp_rs1_group2 :
    (execute_lane0_bypasser_float_RS1_bypassEnables[9] ? execute_lane0_bypasser_float_RS1_port_data : 32'h0);
HUNK3NEW
 chop $new;

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK4OLD';
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
HUNK4OLD
 my $new=<<'HUNK4NEW';
  // Same stage2-to3 ready condition, with all three RS hazards entering last.
  (* keep *) wire os30_decode2_ready_free = !decode_ctrls_3_up_isValid ||
      !decode_ctrls_3_up_LANE_SEL_0 || DispatchPlugin_logic_feeds_0_sent;
  (* keep *) wire os30_decode2_ready_allow = !DispatchPlugin_logic_candidates_0_flushHazards &&
      !DispatchPlugin_logic_candidates_0_fenceOlderHazards && DispatchPlugin_logic_candidates_0_ctx_laneLayerHits[0] &&
      !DispatchPlugin_logic_candidates_0_reservationHazards[0] && !execute_freeze_valid && !DispatchPlugin_api_haltDispatch;
  always @(*) begin
    decode_ctrls_2_down_ready = os30_decode2_ready_free ||
        (os30_decode2_ready_allow && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_0_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_1_hazard && !DispatchPlugin_logic_rsHazardChecker_0_onLl_0_onRs_2_hazard);
  end
HUNK4NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK5OLD';
  assign fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH = ({_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[0],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[1],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[2],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[3],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[4],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[5],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_1,{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_2,_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_3}}}}}}}} ^ _zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_4);
HUNK5OLD
 my $new=<<'HUNK5NEW';
  // Precompute the two priority groups before the late branch redirect.
  (* keep *) wire [12:0] os30_gshare_hash_low = {_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[0],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[1],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[2],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[3],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[4],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[5],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_1,{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_2,_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_3}}}}}}}} ^
      {1'b0, (HistoryPlugin_logic_onFetch_ports_0_valid ? HistoryPlugin_logic_onFetch_ports_0_payload_history : HistoryPlugin_logic_onFetch_value)};
  (* keep *) wire [12:0] os30_gshare_hash_high = {_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[0],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[1],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[2],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[3],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[4],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH[5],{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_1,{_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_2,_zz_fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH_3}}}}}}}} ^
      {1'b0, (HistoryPlugin_logic_onFetch_ports_2_valid ? HistoryPlugin_logic_onFetch_ports_2_payload_history : HistoryPlugin_logic_onFetch_ports_1_payload_history)};
  assign fetch_logic_ctrls_0_down_GSharePlugin_logic_HASH = (HistoryPlugin_logic_onFetch_ports_2_valid || HistoryPlugin_logic_onFetch_ports_1_valid) ?
      os30_gshare_hash_high : os30_gshare_hash_low;
HUNK5NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'HUNK6OLD';
  assign GSharePlugin_logic_mem_write_valid = ((LearnPlugin_logic_learn_valid && LearnPlugin_logic_learn_payload_isBranch) && (! GSharePlugin_logic_onLearn_overflow));
HUNK6OLD
 my $new=<<'HUNK6NEW';
  // All non-cancel qualification is independent of the older LSU trap.
  (* keep *) wire os30_gshare_write_allow = execute_ctrl3_up_LANE_SEL_lane0 &&
      execute_ctrl3_down_isReady && (|execute_ctrl3_down_early0_BranchPlugin_SEL_lane0) &&
      LearnPlugin_logic_learn_payload_isBranch && !GSharePlugin_logic_onLearn_overflow;
  assign GSharePlugin_logic_mem_write_valid = os30_gshare_write_allow && !execute_lane0_ctrls_3_upIsCancel;
HUNK6NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified control-path block\n";
 $source=~s/\Q$old\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected transformed parallel-control hash\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\n";
close $out or die "$temporary: $!\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\n";
rename($temporary,$path) or die "$path rename: $!\n";
print "os30 parallel-control exact output $after\n";
