#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# Exact os30-only FP RS3 factoring. Run last, on the unpublished staging RTL.
# Hashes bind the qualified queue profile and every prior helper. A changed
# emitter/body must be qualified explicitly; do not weaken this check to regex.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_fp_rs3.pl os30 <staging-netlist>\n";
open my $in, '<:raw', $path or die "$path: $!\n";
my $source=do {local $/; <$in>}; close $in;
my $before='8440755f0e7ad74d306583f0b91eff9b79c4319fb9819378f8855d87a46574b6';
my $after='668f2c0c2e351c730ad4ccedf314408a7e5f37e5ab799d8327d9724f8be08c6e';
exit 0 if sha256_hex($source) eq $after;
# Source archives may omit Git metadata. Normalize only the leading emitter
# comments, after checking their exact grammar; the complete body remains bound.
my ($header,$body)=split(/\n\n/,$source,2);
defined($body) && $header =~ /\A\/\/ Generator : [^\n]+\n\/\/ Component : VexiiRiscv(?:\n\/\/ Git hash  : [^\n]+)?\z/
    or die "Unexpected generator provenance header\n";
my $qualified_header=<<'HEADER';
// Generator : SpinalHDL dev    git head : 6f8510cdbb8ad7b8bcc0f6d58395669c4c4d7e2d
// Component : VexiiRiscv
// Git hash  : 81842b296999de1087b814227bd5296bac0bf361
HEADER
chomp $qualified_header;
$source=$qualified_header."\n\n".$body;
my $hash=sha256_hex($source);
$hash eq $before || $hash eq $after
    or die "Unqualified os30 CPU body ($hash); retaining prior output\n";
if($hash eq $before){
 my $old=<<'OLD';
  assign execute_ctrl1_down_float_RS3_lane0 = (((((_zz_execute_ctrl1_down_float_RS3_lane0 ? execute_ctrl4_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_1) | (_zz_execute_ctrl1_down_float_RS3_lane0_2 ? execute_ctrl5_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_3)) | ((_zz_execute_ctrl1_down_float_RS3_lane0_4 ? execute_ctrl6_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_5) | (_zz_execute_ctrl1_down_float_RS3_lane0_6 ? execute_ctrl7_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_7))) | (((_zz_execute_ctrl1_down_float_RS3_lane0_8 ? execute_ctrl8_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_9) | (_zz_execute_ctrl1_down_float_RS3_lane0_10 ? execute_ctrl9_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_11)) | ((_zz_execute_ctrl1_down_float_RS3_lane0_12 ? execute_ctrl10_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_13) | (_zz_execute_ctrl1_down_float_RS3_lane0_14 ? execute_ctrl11_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : _zz_execute_ctrl1_down_float_RS3_lane0_15)))) | ((execute_lane0_bypasser_float_RS3_sel[8] ? execute_ctrl12_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : 32'h0) | (execute_lane0_bypasser_float_RS3_sel[9] ? execute_lane0_bypasser_float_RS3_port_data : 32'h0)));
OLD
 my $new=<<'NEW';
  // Exact earliest-stage FP RS3 forwarding, factored into local groups.
  wire [31:0] fp_rs3_group0 = execute_lane0_bypasser_float_RS3_bypassEnables[0] ? execute_ctrl4_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS3_bypassEnables[1] ? execute_ctrl5_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl6_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  wire [31:0] fp_rs3_group1 = execute_lane0_bypasser_float_RS3_bypassEnables[3] ? execute_ctrl7_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS3_bypassEnables[4] ? execute_ctrl8_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl9_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  wire [31:0] fp_rs3_group2 = execute_lane0_bypasser_float_RS3_bypassEnables[6] ? execute_ctrl10_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : (execute_lane0_bypasser_float_RS3_bypassEnables[7] ? execute_ctrl11_down_lane0_float_WriteBackPlugin_logic_DATA_lane0 : execute_ctrl12_down_lane0_float_WriteBackPlugin_logic_DATA_lane0);
  assign execute_ctrl1_down_float_RS3_lane0 = (|execute_lane0_bypasser_float_RS3_bypassEnables[2:0]) ? fp_rs3_group0 :
    (|execute_lane0_bypasser_float_RS3_bypassEnables[5:3]) ? fp_rs3_group1 :
    (|execute_lane0_bypasser_float_RS3_bypassEnables[8:6]) ? fp_rs3_group2 :
    (execute_lane0_bypasser_float_RS3_bypassEnables[9] ? execute_lane0_bypasser_float_RS3_port_data : 32'h0);
NEW
 chomp $old; chomp $new;
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one qualified FP RS3 assignment\n";
 $source=~s/\Q$old\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected FP RS3 transformed hash\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\n";
close $out or die "$temporary: $!\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\n";
rename($temporary,$path) or die "$path rename: $!\n";
print "os30 FP RS3 exact output $after\n";
