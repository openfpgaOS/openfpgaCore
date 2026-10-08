#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# Precompute FPU forwarding register matches alongside the existing ID flops.
# The execute stages advance together. Valid/write-enable gating remains in
# its original stage, preserving cancellation, stalls and bypass priority.
use strict;
use warnings;
my $path = shift @ARGV or die "Usage: retime_fpu_matches.pl <netlist>\n";
@ARGV == 0 or die "Unexpected arguments\n";
open my $input, '<', $path or die "$path: $!\n";
my $source = do { local $/; <$input> };
close $input;
sub require_once {
    my ($fragment) = @_;
    my $count = () = $source =~ /\Q$fragment\E/g;
    $count == 1 or die "Unexpected FPU match dependency: $fragment\n";
}
# Reject independent stage stalls: all ID registers must share one enable.
for my $stage (0 .. 11) {
    my $next = $stage + 1;
    require_once("  assign execute_ctrl${stage}_down_ready = execute_ctrl${next}_up_ready;");
    require_once("  assign execute_ctrl${stage}_down_isReady = execute_ctrl${stage}_down_ready;");
    require_once("  assign execute_ctrl${stage}_up_ready = execute_ctrl${stage}_down_isReady;");
}
sub check_register {
    my ($stage, $field) = @_;
    my $prev = $stage - 1;
    my $q = "execute_ctrl${stage}_up_${field}_lane0";
    my $d = "execute_ctrl${prev}_down_${field}_lane0";
    require_once("  assign execute_ctrl${stage}_down_${field}_lane0 = $q;");
    my $stmt = "      $q <= $d;";
    require_once($stmt);
    my $writers = () = $source =~ /\b\Q$q\E\s*<=/g;
    $writers == 1 or die "Unexpected ID register writer count: $q\n";
    my @blocks = $source =~ /    if\(execute_ctrl${prev}_down_isReady\) begin\n((?:      [^\n]*\n)+)    end/g;
    my @matching = grep { index($_, $stmt) >= 0 } @blocks;
    @matching == 1 or die "Unexpected ID register enable: $q\n";
    for my $line (split /\n/, $matching[0]) {
        $line =~ /^      \w+ <= [^;]+;$/ or die "Unexpected ID pipeline block: $line\n";
    }
}
for my $rs (1 .. 3) {
    check_register(1, "RS${rs}_PHYS");
    check_register(1, "RS${rs}_RFID");
}
for my $stage (4 .. 12) {
    check_register($stage, 'RD_PHYS');
    check_register($stage, 'RD_RFID');
}
my @lines;
my @replacements;
for my $rs (1 .. 3) {
    for my $stage (4 .. 12) {
        my $prev = $stage - 1;
        my $index = $stage - 4;
        my $name = "fp_match_RS${rs}_${stage}";
        push @lines, "  reg $name;", '  always @(posedge clk) if(execute_ctrl0_down_isReady)',
            "    $name <= (execute_ctrl${prev}_down_RD_PHYS_lane0 == execute_ctrl0_down_RS${rs}_PHYS_lane0) && (execute_ctrl${prev}_down_RD_RFID_lane0 == execute_ctrl0_down_RS${rs}_RFID_lane0);";
        my $lhs = "execute_lane0_bypasser_float_RS${rs}_bypassEnables[$index]";
        my $old = "    $lhs = (((execute_ctrl${stage}_up_LANE_SEL_lane0 && execute_ctrl${stage}_up_RD_ENABLE_lane0) && (execute_ctrl${stage}_down_RD_PHYS_lane0 == execute_ctrl1_down_RS${rs}_PHYS_lane0)) && (execute_ctrl${stage}_down_RD_RFID_lane0 == execute_ctrl1_down_RS${rs}_RFID_lane0));";
        my $new = "    $lhs = (execute_ctrl${stage}_up_LANE_SEL_lane0 && execute_ctrl${stage}_up_RD_ENABLE_lane0) && $name;";
        push @replacements, [$old, $new];
    }
}
my $block = join("\n", @lines) . "\n";
my $needle = '  assign execute_lane0_bypasser_float_RS1_port_valid =';
require_once($needle);
if (index($source, $block) >= 0) {
    for my $pair (@replacements) {
        require_once($pair->[1]);
        index($source, $pair->[0]) < 0 or die "Partial FPU match retiming\n";
    }
    exit 0;
}
$source !~ /\bfp_match_RS\d_\d+\b/ or die "Conflicting FPU match retiming\n";
for my $pair (@replacements) {
    require_once($pair->[0]);
    $source =~ s/\Q$pair->[0]\E/$pair->[1]/;
}
$source =~ s/\Q$needle\E/$block$needle/;
open my $output, '>', $path or die "$path: $!\n";
print {$output} $source;
close $output or die "$path: $!\n";
