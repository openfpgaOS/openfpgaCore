#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# Factor fetch readiness without changing occupancy, cancellation or stalls.
use strict;
use warnings;

my $path = shift @ARGV or die "Usage: factor_fetch_ready.pl <netlist>\n";
@ARGV == 0 or die "Unexpected arguments\n";
open my $input, '<', $path or die "$path: $!\n";
my $source = do { local $/; <$input> };
close $input;

# The factored expression depends on these exact ready-chain and aligner
# contracts. Reject a changed generator before modifying its staging output.
# Single-issue fetch buffers have two slices; dual-issue buffers have four.
my @mask_high = $source =~ /^  reg\s+\[(\d+):0\]\s+AlignerPlugin_logic_buffer_mask;$/mg;
@mask_high == 1 && ($mask_high[0] == 1 || $mask_high[0] == 3)
    or die "Unexpected fetch buffer mask width\n";
my $high = $mask_high[0];
my $width = $high + 1;
my $zero = $width . "'b" . ('0' x $width);
my @required = (
    '  assign decode_ctrls_0_up_ready = decode_ctrls_0_down_isReady;',
    '  assign decode_ctrls_1_up_ready = decode_ctrls_1_down_isReady;',
    '  assign decode_ctrls_2_up_ready = decode_ctrls_2_down_isReady;',
    '  assign decode_ctrls_0_down_isReady = decode_ctrls_0_down_ready;',
    '  assign decode_ctrls_1_down_isReady = decode_ctrls_1_down_ready;',
    '  assign decode_ctrls_2_down_isReady = decode_ctrls_2_down_ready;',
    '  assign decode_ctrls_0_up_isReady = decode_ctrls_0_up_ready;',
    '  assign when_StageLink_l71_2 = (! decode_ctrls_1_up_isValid);',
    '  assign when_StageLink_l71_3 = (! decode_ctrls_2_up_isValid);',
    '  assign AlignerPlugin_logic_buffer_downFire = (decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel);',
    "  assign AlignerPlugin_logic_buffer_haltUp = ((|(AlignerPlugin_logic_buffer_mask & (~ (AlignerPlugin_logic_buffer_downFire ? AlignerPlugin_logic_buffer_usedMask[$high : 0] : $zero)))) || AlignerPlugin_api_haltIt);",
    <<'READY0',
  always @(*) begin
    decode_ctrls_0_down_ready = decode_ctrls_1_up_ready;
    if(when_StageLink_l71_2) begin
      decode_ctrls_0_down_ready = 1'b1;
    end
  end
READY0
    <<'READY1',
  always @(*) begin
    decode_ctrls_1_down_ready = decode_ctrls_2_up_ready;
    if(when_StageLink_l71_3) begin
      decode_ctrls_1_down_ready = 1'b1;
    end
  end
READY1
);
for my $fragment (@required) {
    my $count = () = $source =~ /\Q$fragment\E/g;
    $count == 1 or die "Unexpected fetch-ready dependency: $fragment\n";
}
my $writers = () = $source =~ /\bassign\s+fetch_logic_ctrls_3_down_ready\s*=/g;
$writers == 1 or die "Unexpected fetch-ready writer count\n";
my $old = '  assign fetch_logic_ctrls_3_down_ready = ((! fetch_logic_ctrls_3_down_valid) || (! AlignerPlugin_logic_buffer_haltUp));';
my $new = <<'FACTORED';
  // Factor early occupancy terms away from the late dispatch hazard.
  (* keep *) wire fetch_ready_allow = !AlignerPlugin_api_haltIt &&
      !(|(AlignerPlugin_logic_buffer_mask & ~AlignerPlugin_logic_buffer_usedMask[MASK_HIGH:0]));
  (* keep *) wire fetch_ready_empty = !(|AlignerPlugin_logic_buffer_mask) ||
      !decode_ctrls_1_up_isValid || !decode_ctrls_2_up_isValid || decode_ctrls_0_up_isCancel;
  assign fetch_logic_ctrls_3_down_ready = !fetch_logic_ctrls_3_down_valid ||
      (fetch_ready_allow && (fetch_ready_empty || decode_ctrls_2_down_ready));
FACTORED
$new =~ s/MASK_HIGH/$high/g;
chomp $new;
exit 0 if index($source, $new) >= 0 && index($source, $old) < 0;
index($source, $old) >= 0 or die "Unexpected fetch-ready expression\n";
$source !~ /\bfetch_ready_(?:allow|empty)\b/
    or die "Partial or conflicting fetch-ready transformation\n";
$source =~ s/\Q$old\E/$new/;
open my $output, '>', $path or die "$path: $!\n";
print {$output} $source;
close $output or die "$path: $!\n";
