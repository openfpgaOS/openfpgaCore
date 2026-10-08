#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# Preserve fetch pipeline data with local registered capture selectors.
use strict;
use warnings;
my $path = shift @ARGV or die "Usage: retime_fetch_pipeline.pl <netlist>\n";
@ARGV == 0 or die "Unexpected arguments\n";
open my $input, '<', $path or die "$path: $!\n";
my $source = do { local $/; <$input> };
close $input;
my @fields = (map { [$_, 'Fetch_WORD_PC', 32] } (1 .. 3));
push @fields, [3, 'FetchL1Plugin_logic_BANKS_MUXES_0', undef],
              [3, 'FetchL1Plugin_logic_BANKS_MUXES_1', undef];
for my $field (@fields) {
    my ($stage, $payload, $width) = @$field;
    my $prev = $stage - 1;
    my $name = "fetch_logic_ctrls_${stage}_up_$payload";
    if (!defined $width) {
        # Single-issue os30 fetches 32 bits per bank; dual-issue os20 uses
        # 64. Read the generated width without changing the capture cycle.
        $source =~ /^  (?:reg|wire) +\[(31|63):0\] +\Q$name\E;$/m
            or die "Unexpected fetch bank width: $name\n";
        $width = $1 + 1;
    }
    my $high = $width - 1;
    my $enable = "fetch_logic_ctrls_${prev}_down_isReady";
    my $data = "fetch_logic_ctrls_${prev}_down_$payload";
    my $declaration = "  wire [$high:0] $name;\n" .
        "  reg [$high:0] ${name}_raw;\n" .
        "  reg [$high:0] ${name}_held;\n" .
        "  (* preserve, dont_retime *) reg ${name}_enabled;\n" .
        "  assign $name = ${name}_enabled ? ${name}_raw : ${name}_held;";
    my $capture = "    ${name}_raw <= $data;\n" .
        "    ${name}_enabled <= $enable;\n" .
        "    if (${name}_enabled) ${name}_held <= ${name}_raw;\n";
    next if index($source, $declaration) >= 0 && index($source, $capture) >= 0;
    $source =~ /^  reg +\[$high:0\] +\Q$name\E;$/m
        or die "Missing fetch pipeline declaration: $name\n";
    my $old_declaration = $&;
    my $assignment = "      $name <= $data;\n";
    my $group = "    if($enable) begin\n";
    my $writes = () = $source =~ /\b\Q$name\E\s*<=/g;
    $writes == 1 && index($source, $assignment) >= 0
        or die "Unexpected fetch pipeline writer: $name\n";
    $source =~ /\Q$group\E(?:(?!^    end).)*?\Q$assignment\E/ms
        or die "Unexpected fetch pipeline enable: $name\n";
    my $old_group = $&;
    my $new_group = $old_group;
    $new_group =~ s/\Q$assignment\E//;
    $source =~ s/\Q$old_declaration\E/$declaration/;
    $source =~ s/\Q$old_group\E/$capture$new_group/;
}
open my $output, '>', $path or die "$path: $!\n";
print {$output} $source;
close $output or die "$path: $!\n";
