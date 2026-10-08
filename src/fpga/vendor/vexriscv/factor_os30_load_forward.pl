#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# os30-only exact integer late-load forwarding reassociation. Apply AFTER factor_os30_frontend.pl.
# Hash-locked to the qualified output; unknown generator changes fail closed.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_load_forward.pl os30 <staging-netlist>\n";
open my $in,'<:raw',$path or die "$path: $!\n";
my $source=do {local $/; <$in>}; close $in;
my $before='aecbd7d8f63675333909df756c072b767bab269d29188190f80cfb3cde376295';
my $after='82b7e440177b195cbe84f5679c7e88c44b5df2aae004f798c63b4adc3b926534';
exit 0 if sha256_hex($source) eq $after;
sha256_hex($source) eq $before or die "Unqualified os30 load-forward input; retaining prior output\n";
{
 my $old=<<'RS1OLD';
  always @(*) begin
    _zz_execute_ctrl1_down_integer_RS1_lane0_1 = (((_zz_execute_ctrl1_down_integer_RS1_lane0[0] ? execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS1_lane0[1] ? execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0)) | ((_zz_execute_ctrl1_down_integer_RS1_lane0[2] ? execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS1_lane0[3] ? execute_lane0_bypasser_integer_RS1_port_data : 32'h0)));
    if(when_ExecuteLanePlugin_l196) begin
      _zz_execute_ctrl1_down_integer_RS1_lane0_1 = execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
    end
  end
RS1OLD
 my $new=<<'RS1NEW';
  // Keep the older writeback register contribution off the late load leg.
  // Architectural writeback, formatting and forwarding priority are unchanged.
  (* keep *) wire [31:0] os30_integer_rs1_early = when_ExecuteLanePlugin_l196
      ? execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0
      : (((_zz_execute_ctrl1_down_integer_RS1_lane0[0] ? execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS1_lane0[1] ? execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0)) | ((_zz_execute_ctrl1_down_integer_RS1_lane0[2] ? execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS1_lane0[3] ? execute_lane0_bypasser_integer_RS1_port_data : 32'h0)));
  (* keep *) wire os30_integer_rs1_late_enable = (!when_ExecuteLanePlugin_l196) && _zz_execute_ctrl1_down_integer_RS1_lane0[1];
  always @(*) begin
    _zz_execute_ctrl1_down_integer_RS1_lane0_1 = os30_integer_rs1_early |
        ({32{os30_integer_rs1_late_enable}} & lane0_integer_WriteBackPlugin_logic_stages_2_muxed);
  end
RS1NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified integer forwarding block\n";
 $source=~s/\Q$old\E/$new/;
}
{
 my $old=<<'RS2OLD';
  always @(*) begin
    _zz_execute_ctrl1_down_integer_RS2_lane0_1 = (((_zz_execute_ctrl1_down_integer_RS2_lane0[0] ? execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS2_lane0[1] ? execute_ctrl4_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0)) | ((_zz_execute_ctrl1_down_integer_RS2_lane0[2] ? execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS2_lane0[3] ? execute_lane0_bypasser_integer_RS2_port_data : 32'h0)));
    if(when_ExecuteLanePlugin_l196_1) begin
      _zz_execute_ctrl1_down_integer_RS2_lane0_1 = execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0;
    end
  end
RS2OLD
 my $new=<<'RS2NEW';
  // Keep the older writeback register contribution off the late load leg.
  // Architectural writeback, formatting and forwarding priority are unchanged.
  (* keep *) wire [31:0] os30_integer_rs2_early = when_ExecuteLanePlugin_l196_1
      ? execute_ctrl2_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0
      : (((_zz_execute_ctrl1_down_integer_RS2_lane0[0] ? execute_ctrl3_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS2_lane0[1] ? execute_ctrl4_up_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0)) | ((_zz_execute_ctrl1_down_integer_RS2_lane0[2] ? execute_ctrl5_down_lane0_integer_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (_zz_execute_ctrl1_down_integer_RS2_lane0[3] ? execute_lane0_bypasser_integer_RS2_port_data : 32'h0)));
  (* keep *) wire os30_integer_rs2_late_enable = (!when_ExecuteLanePlugin_l196_1) && _zz_execute_ctrl1_down_integer_RS2_lane0[1];
  always @(*) begin
    _zz_execute_ctrl1_down_integer_RS2_lane0_1 = os30_integer_rs2_early |
        ({32{os30_integer_rs2_late_enable}} & lane0_integer_WriteBackPlugin_logic_stages_2_muxed);
  end
RS2NEW

 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified integer forwarding block\n";
 $source=~s/\Q$old\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected transformed load-forward hash\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\n";
close $out or die "$temporary: $!\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\n";
rename($temporary,$path) or die "$path rename: $!\n";
print "os30 load-forward exact output $after\n";
