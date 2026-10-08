#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# Cycle-equivalent dual-issue frontend, dispatch and forwarding transformations.
# Apply after the os20 fetch/FPU hold passes in generate_vexii.sh. The
# complete aligner and dispatch contracts are checked before rewriting; the file
# is published only after every transformation succeeds. Requalify with
# tools/check_vexii_os20_timing.py and check_vexii_interrupts.py after any
# generator change. No instruction, cache or execution stage is added.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);

sub once {
    my ($source, $old, $new) = @_;
    my $count = () = $$source =~ /\Q$old\E/g;
    $count == 1 or die "Unexpected os20 timing dependency: $old\n";
    $$source =~ s/\Q$old\E/$new/;
}

sub combinational_cone {
    my ($source, $roots, $leaves) = @_;
    my %inputs = map { $_ => 1 } @$leaves;
    $source = (split /\nendmodule/, $source, 2)[0];
    my (%decls, %driver, @statements);
    while ($source =~ /^  (?:wire|reg)\s+(?:\[[^\n]+?\]\s+)?(\w+);$/mg) {
        $decls{$1} = $&;
    }
    while ($source =~ /^  assign \w+\s*=.*?;\n|^  always @\(\*\) begin\n.*?^  end\n/msg) {
        my $statement = $&;
        my %lhs;
        while ($statement =~ /\b(\w+)\s*(?:\[[^;\n]*?\])?\s*=(?!=)/g) {
            $lhs{$1} = 1 if exists $decls{$1};
        }
        push @{$driver{$_}}, scalar @statements for keys %lhs;
        push @statements, $statement;
    }
    my @todo = @$roots;
    my (%visited, %items);
    while (@todo) {
        my $name = pop @todo;
        next if $inputs{$name} || $visited{$name};
        $visited{$name} = 1;
        exists $driver{$name} && @{$driver{$name}} == 1
            or die "Non-combinational or multiple-driver RVC dependency: $name\n";
        my $index = $driver{$name}[0];
        $items{$index} = 1;
        my %deps = map { $_ => 1 } ($statements[$index] =~ /\b\w+\b/g);
        push @todo, grep { exists $decls{$_} && !$visited{$_} } keys %deps;
    }
    my $body = join("\n", map { $decls{$_} } sort keys %visited) . "\n";
    $body .= join("\n", map { $statements[$_] } sort { $a <=> $b } keys %items);
    return $body;
}

sub rvc_decoder {
    my ($source) = @_;
    my $input = 'AlignerPlugin_logic_extractors_0_ctx_instruction';
    my $inst = 'AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst';
    my $fault = 'AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_illegal';
    my $body = combinational_cone($source, [$inst, $fault], [$input]);
    $body =~ s/\b\Q$input\E\b/raw/g;
    return "module VexiiRiscvEarlyRvcDecode(input wire [31:0] raw,\n" .
           "    output wire [31:0] expanded, output wire fault);\n" . $body .
           "  assign expanded = raw[1:0] != 2'b11 ? $inst : raw;\n" .
           "  assign fault = raw[1:0] != 2'b11 && $fault;\nendmodule\n";
}

sub rvc_bank {
    my $text = <<'BANK';
module VexiiRiscvEarlyRvcBank(
    input wire [127:0] slices,
    input wire [7:0] sel0, input wire [6:0] sel1,
    output wire [31:0] inst0, inst1,
    output wire fault0, fault1);
    wire [31:0] empty_inst;
    wire empty_fault;
    VexiiRiscvEarlyRvcDecode empty_decoder(32'b0, empty_inst, empty_fault);
BANK
    for my $i (0 .. 7) {
        my $raw = $i < 7 ? 'slices[' . ($i*16+31) . ':' . ($i*16) . ']' : "{16'b0,slices[127:112]}";
        $text .= "    wire [31:0] expanded$i; wire fault${i}_slice;\n";
        $text .= "    VexiiRiscvEarlyRvcDecode slice$i($raw, expanded$i, fault${i}_slice);\n";
    }
    for my $lane (0 .. 1) {
        my @data = ("((~|sel$lane) ? empty_inst : 32'b0)");
        my @fault = ("((~|sel$lane) && empty_fault)");
        for my $i ($lane .. 7) {
            my $bit = $i - $lane;
            push @data, "(sel$lane\[$bit\] ? expanded$i : 32'b0)";
            push @fault, "(sel$lane\[$bit\] && fault${i}_slice)";
        }
        $text .= "    assign inst$lane = " . join(' | ', @data) . ";\n";
        $text .= "    assign fault$lane = " . join(' | ', @fault) . ";\n";
    }
    return $text . "endmodule\n";
}

sub early_rvc {
    my ($source) = @_;
    $$source !~ /VexiiRiscvEarlyRvcBank/ or die "RVC transform already present\n";
    my $decoder = rvc_decoder($$source);
    for my $i (0 .. 7) {
        my $stem = '_zz_AlignerPlugin_logic_extractors_0_ctx_instruction';
        my $a = '  assign ' . $stem . '_' . (2*$i+1) . " = $stem\[$i\];";
        my $b = '  assign ' . $stem . '_' . (2*$i+2) . " = 32'h0;";
        once($source, $a, $a); once($source, $b, $b);
    }
    for my $lane (0 .. 1) {
        for my $field (['INSTRUCTION', "AlignerPlugin_logic_extractors_${lane}_ctx_instruction", 'inst'],
                       ['DECOMPRESSION_FAULT', "1'b0", 'illegal']) {
            my ($payload, $default, $result) = @$field;
            my $name = "decode_ctrls_0_up_Decode_${payload}_$lane";
            my $old = "  always \@(*) begin\n    $name = $default;\n" .
                      "    if(AlignerPlugin_logic_feeder_lanes_${lane}_isRvc) begin\n" .
                      "      $name = AlignerPlugin_logic_feeder_lanes_${lane}_withRvc_dec_$result;\n" .
                      "    end\n  end\n";
            once($source, $old, '');
            my $n = $$source =~ s/^(  )reg(\s+(?:\[[^\n]*?\]\s+)?\Q$name\E;)$/${1}wire$2/mg;
            $n == 1 or die "Unexpected RVC output declaration: $name\n";
        }
    }
    my $instance = <<'INSTANCE';

  // Decode each candidate before selection; no additional pipeline stage.
  VexiiRiscvEarlyRvcBank early_rvc(
    .slices({AlignerPlugin_logic_slices_data_7, AlignerPlugin_logic_slices_data_6,
             AlignerPlugin_logic_slices_data_5, AlignerPlugin_logic_slices_data_4,
             AlignerPlugin_logic_slices_data_3, AlignerPlugin_logic_slices_data_2,
             AlignerPlugin_logic_slices_data_1, AlignerPlugin_logic_slices_data_0}),
    .sel0(_zz_AlignerPlugin_logic_extractors_0_ctx_instruction),
    .sel1(_zz_AlignerPlugin_logic_extractors_1_ctx_instruction),
    .inst0(decode_ctrls_0_up_Decode_INSTRUCTION_0),
    .inst1(decode_ctrls_0_up_Decode_INSTRUCTION_1),
    .fault0(decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_0),
    .fault1(decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_1));
INSTANCE
    $$source =~ s/\nendmodule/$instance\nendmodule/ or die "Missing top module\n";
    $$source .= "\n$decoder\n" . rvc_bank();
}

sub fp_before_select {
    my ($source) = @_;
    my $s0 = '_zz_execute_ctrl0_up_LANE_AGE_lane0';
    my $s1 = $s0 . '_1';
    for my $rs (1 .. 3) {
        for my $field (['PHYS', "5'h0"], ['RFID', "1'b0"]) {
            my ($kind, $zero) = @$field;
            my $name = "RS${rs}_$kind";
            my $expected = "  assign execute_ctrl0_up_${name}_lane0 = (($s0 ? DispatchPlugin_logic_candidates_0_ctx_hm_$name : $zero) | ($s1 ? DispatchPlugin_logic_candidates_1_ctx_hm_$name : $zero));";
            once($source, $expected, $expected);
            $expected = "  assign execute_ctrl0_down_${name}_lane0 = execute_ctrl0_up_${name}_lane0;";
            once($source, $expected, $expected);
        }
        for my $stage (4 .. 12) {
            my $prev = $stage - 1;
            my $name = "fp_match_RS${rs}_$stage";
            my $old = "  reg $name;\n  always \@(posedge clk) if(execute_ctrl0_down_isReady)\n" .
                      "    $name <= (execute_ctrl${prev}_down_RD_PHYS_lane0 == execute_ctrl0_down_RS${rs}_PHYS_lane0) && (execute_ctrl${prev}_down_RD_RFID_lane0 == execute_ctrl0_down_RS${rs}_RFID_lane0);";
            my $new = "  reg $name;\n  always \@(posedge clk) if(execute_ctrl0_down_isReady) begin\n" .
                      "    case ({$s1, $s0})\n";
            for my $sel (0 .. 3) {
                my @expr;
                for my $field (['PHYS', "5'h0"], ['RFID', "1'b0"]) {
                    my ($kind, $zero) = @$field;
                    my @values = map { "DispatchPlugin_logic_candidates_${_}_ctx_hm_RS${rs}_$kind" }
                                 grep { $sel & (1 << $_) } (0 .. 1);
                    my $value = @values ? '(' . join(' | ', @values) . ')' : $zero;
                    push @expr, "(execute_ctrl${prev}_down_RD_${kind}_lane0 == $value)";
                }
                $new .= sprintf("      2'b%02b: %s <= %s;\n", $sel, $name, join(' && ', @expr));
            }
            $new .= "    endcase\n  end";
            once($source, $old, $new);
        }
    }
}

sub direct_fetch_lowbits {
    my ($source) = @_;
    for my $bank (0 .. 1) {
        my $name = "fetch_logic_ctrls_3_up_FetchL1Plugin_logic_BANKS_MUXES_$bank";
        my $data = "fetch_logic_ctrls_2_down_FetchL1Plugin_logic_BANKS_MUXES_$bank";
        my $old = "  assign $name = ${name}_enabled ? ${name}_raw : ${name}_held;";
        my $new = "  reg [7:0] ${name}_lowbits;\n";
        for my $i (0 .. 3) {
            my ($lo, $hi, $bhi, $blo) = (16*$i, 16*$i+15, 2*$i+1, 2*$i);
            $new .= '  assign ' . $name . '[' . ($lo+1) . ":$lo] = ${name}_lowbits[$bhi:$blo];\n";
            $new .= "  assign $name\[$hi:" . ($lo+2) . "] = ${name}_enabled ? ${name}_raw[$hi:" . ($lo+2) . "] : ${name}_held[$hi:" . ($lo+2) . "];\n";
        }
        $new .= "  always \@(posedge clk) if (fetch_logic_ctrls_2_down_isReady)\n";
        $new .= "    ${name}_lowbits <= {" . join(', ', map { $data . '[' . (16*$_+1) . ':' . (16*$_) . ']' } reverse 0 .. 3) . '};';
        once($source, $old, $new);
    }
}

sub aligner_hold {
    my ($source) = @_;
    $$source =~ /    if\(when_AlignerPlugin_l256\) begin\n((?:      AlignerPlugin_logic_buffer_(?:data|hm_\w+) <= [^;]+;\n)+)    end/
        or die "Missing aligner payload capture\n";
    my ($group, $body) = ($&, $1);
    my @pairs;
    while ($body =~ /      (\w+) <= ([^;]+);/g) { push @pairs, [$1, $2]; }
    @pairs == 12 or die "Unexpected aligner payload count\n";
    my (@captures, @kept);
    for my $pair (@pairs) {
        my ($name, $data) = @$pair;
        $$source =~ /^  reg\s+(?:\[(\d+):0\]\s+)?\Q$name\E;$/m
            or die "Unexpected aligner payload declaration: $name\n";
        my ($decl, $width) = ($&, defined $1 ? $1+1 : 1);
        if ($width < 8) { push @kept, "      $name <= $data;\n"; next; }
        my $writers = () = $$source =~ /\b\Q$name\E\s*<=/g;
        $writers == 1 or die "Unexpected aligner payload writers: $name\n";
        my $high = $width - 1;
        my $new = "  wire [$high:0] $name;\n  reg [$high:0] ${name}_raw, ${name}_held;\n" .
                  "  (* preserve, dont_retime *) reg ${name}_enabled;\n";
        if ($name =~ /_data$/) {
            $width == 64 or die "Unexpected aligner data width\n";
            $new .= "  reg [7:0] ${name}_lowbits;\n";
            for my $i (0 .. 3) {
                my ($lo, $hi, $bhi, $blo) = (16*$i, 16*$i+15, 2*$i+1, 2*$i);
                $new .= '  assign ' . $name . '[' . ($lo+1) . ":$lo] = ${name}_lowbits[$bhi:$blo];\n";
                $new .= "  assign $name\[$hi:" . ($lo+2) . "] = ${name}_enabled ? ${name}_raw[$hi:" . ($lo+2) . "] : ${name}_held[$hi:" . ($lo+2) . "];\n";
            }
            push @kept, "      ${name}_lowbits <= {" . join(', ', map { $data . '[' . (16*$_+1) . ':' . (16*$_) . ']' } reverse 0 .. 3) . "};\n";
        } else {
            $new .= "  assign $name = ${name}_enabled ? ${name}_raw : ${name}_held;\n";
        }
        chomp $new;
        once($source, $decl, $new);
        push @captures, "    ${name}_raw <= $data;\n", "    ${name}_enabled <= when_AlignerPlugin_l256;\n",
                        "    if (${name}_enabled) ${name}_held <= ${name}_raw;\n";
    }
    @captures == 12 or die "Unexpected wide aligner payload count\n";
    once($source, $group, join('', @captures) . "    if(when_AlignerPlugin_l256) begin\n" . join('', @kept) . '    end');
}

sub local_usedmask {
    my ($source) = @_;
    my $old = "  assign AlignerPlugin_logic_buffer_usedMask = ((AlignerPlugin_logic_extractors_0_valid ? AlignerPlugin_logic_extractors_0_usageMask : 8'h0) | (AlignerPlugin_logic_extractors_1_valid ? AlignerPlugin_logic_extractors_1_usageMask : 8'h0));";
    my @terms;
    for my $lane (0 .. 1) {
        my $single = $lane ? ' && !AlignerPlugin_api_singleFetch' : '';
        for my $i ($lane .. 7) {
            my $bit = $i - $lane;
            push @terms, "((AlignerPlugin_logic_extractors_${lane}_slicesOh[$bit] && !AlignerPlugin_api_haltIt$single && (fetch_logic_ctrls_3_down_valid || !(|AlignerPlugin_logic_scanners_${i}_usageMask[7:4]))) ? AlignerPlugin_logic_scanners_${i}_usageMask : 8'b0)";
        }
    }
    once($source, $old, "  // Qualify each selected instruction locally before combining consumed slices.\n  assign AlignerPlugin_logic_buffer_usedMask =\n      " . join(" |\n      ", @terms) . ';');
}

sub early_valid {
    my ($source) = @_;
    for my $lane (0 .. 1) {
        my $name = "AlignerPlugin_logic_extractors_${lane}_valid";
        my $suffix = $lane ? '_1' : '';
        my $old = "  always \@(*) begin\n    $name = (|AlignerPlugin_logic_extractors_${lane}_slicesOh);\n" .
                  "    if(when_AlignerPlugin_l160$suffix) begin\n      $name = 1'b0;\n    end\n" .
                  "    if(when_AlignerPlugin_l240) begin\n      if(when_AlignerPlugin_l241$suffix) begin\n" .
                  "        $name = 1'b0;\n      end\n    end\n  end";
        my $single = $lane ? ' && !AlignerPlugin_api_singleFetch' : '';
        my $usable = "AlignerPlugin_logic_extractors_${lane}_usableMask";
        my $new = "  // The first usable instruction is valid if fully buffered, or fetch is present.\n" .
                  "  assign $name = !AlignerPlugin_api_haltIt$single &&\n" .
                  '      ((|' . $usable . '[' . (2-$lane) . ":0]) ||\n" .
                  '       (' . $usable . '[' . (3-$lane) . "] && !AlignerPlugin_logic_scanners_3_checker_1_required) ||\n" .
                  "       (fetch_logic_ctrls_3_down_valid && (|$usable)));";
        once($source, $old, $new);
        my $n = $$source =~ s/^(  )reg(\s+\Q$name\E;)$/${1}wire$2/mg;
        $n == 1 or die "Unexpected aligner valid declaration: $name\n";
    }
}

sub check_aligner_contract {
    my ($source) = @_;
    my @roots = ('AlignerPlugin_logic_buffer_usedMask');
    for my $lane (0 .. 1) {
        push @roots, "AlignerPlugin_logic_extractors_${lane}_valid",
                     "AlignerPlugin_logic_extractors_${lane}_ctx_instruction";
    }
    my @leaves = ('AlignerPlugin_logic_slices_mask', 'AlignerPlugin_logic_slices_last',
                  'AlignerPlugin_api_haltIt', 'AlignerPlugin_api_singleFetch',
                  'fetch_logic_ctrls_3_down_valid',
                  map { "AlignerPlugin_logic_slices_data_$_" } (0 .. 7));
    # Pin the complete scanner/selection/validity cone that was qualified.
    # This catches injection, instruction-width, priority and branch-boundary
    # changes before a future generator can silently invalidate the rewrite.
    my $body = combinational_cone($source, \@roots, \@leaves);
    my $hash = sha256_hex($body);
    $hash eq 'cc0a70294b49892313dd8a29fcadb02ed2def7622070f703f7ea60e032f4b822' or die "Aligner contract changed ($hash); requalify os20 timing rewrites\n";
    my $other = $source;
    for my $stem ('AlignerPlugin_logic_feeder_lanes_', 'AlignerPlugin_logic_extractors_') {
        $other =~ s/\Q${stem}0_\E/${stem}swap_/g;
        $other =~ s/\Q${stem}1_\E/${stem}0_/g;
        $other =~ s/\Q${stem}swap_\E/${stem}1_/g;
    }
    my $lane1 = rvc_decoder($other);
    $lane1 =~ s/\b((?:when|switch)_Rvc_l\d+)_1\b/$1/g;
    rvc_decoder($source) eq $lane1 or die "Lane RVC decoders differ; requalify os20 timing rewrites\n";
}

sub dispatch_contract {
    my ($source) = @_;
    my (@roots, @leaves);
    for my $lane (0 .. 1) {
        push @roots, "DispatchPlugin_logic_scheduler_arbiters_${lane}_doIt",
                     "DispatchPlugin_logic_candidates_${lane}_fire",
                     "DispatchPlugin_logic_inserter_${lane}_oh";
        push @leaves, map { "DispatchPlugin_logic_candidates_${lane}_$_" }
                      qw(ctx_valid flushHazards fenceOlderHazards ctx_laneLayerHits rsHazards reservationHazards);
    }
    push @leaves, 'DispatchPlugin_logic_scheduler_arbiters_1_candHazard',
                  'execute_freeze_valid', 'DispatchPlugin_api_haltDispatch';
    # Pin both arbitration decisions and all selectors, including priority,
    # lane mappings and empty-candidate handling. The entire 21-bit input
    # space is checked by check_vexii_os20_timing.py.
    return sha256_hex(combinational_cone($source, \@roots, \@leaves));
}

sub direct_dispatch_grants {
    my ($source) = @_;
    my $name = 'DispatchPlugin_logic_scheduler_arbiters_1_doIt';
    $$source =~ /^  assign \Q$name\E = [^;]+;/m or die "Missing second dispatch decision\n";
    my $old = $&;
    my $new = '';
    for my $lane (0 .. 1) {
        my $p = "DispatchPlugin_logic_candidates_$lane";
        $new .= "  wire [1:0] dispatch_available$lane;\n" .
                "  assign dispatch_available$lane = ${p}_ctx_laneLayerHits & ~${p}_rsHazards & ~${p}_reservationHazards;\n";
    }
    $new .= <<'DISPATCH';
  wire dispatch_second_allowed;
  assign dispatch_second_allowed = DispatchPlugin_logic_candidates_1_ctx_valid && !DispatchPlugin_logic_candidates_1_flushHazards && !DispatchPlugin_logic_candidates_1_fenceOlderHazards && !DispatchPlugin_logic_scheduler_arbiters_1_candHazard;
  wire dispatch_first_allowed;
  assign dispatch_first_allowed = !DispatchPlugin_logic_candidates_0_flushHazards && !DispatchPlugin_logic_candidates_0_fenceOlderHazards;
  assign DispatchPlugin_logic_scheduler_arbiters_1_doIt = DispatchPlugin_logic_candidates_1_ctx_valid && !DispatchPlugin_logic_candidates_1_flushHazards && !DispatchPlugin_logic_candidates_1_fenceOlderHazards &&
      !DispatchPlugin_logic_scheduler_arbiters_1_candHazard &&
      ((!DispatchPlugin_logic_candidates_0_ctx_valid && (|dispatch_available1)) ||
       (DispatchPlugin_logic_candidates_0_ctx_valid && dispatch_first_allowed &&
        ((dispatch_available0[0] && dispatch_available1[1]) ||
         (!dispatch_available0[0] && dispatch_available0[1] && dispatch_available1[0]))));
DISPATCH
    chomp $new;
    once($source, $old, $new);
    my @grants = (
        'dispatch_second_allowed && dispatch_available1[1] && ((!DispatchPlugin_logic_candidates_0_ctx_valid && !dispatch_available1[0]) || (DispatchPlugin_logic_candidates_0_ctx_valid && dispatch_first_allowed && dispatch_available0[0]))',
        'dispatch_second_allowed && dispatch_available1[0] && (!DispatchPlugin_logic_candidates_0_ctx_valid || (dispatch_first_allowed && !dispatch_available0[0] && dispatch_available0[1]))');
    for my $lane (0 .. 1) {
        $name = "DispatchPlugin_logic_inserter_${lane}_oh";
        $old = "  assign $name = {(DispatchPlugin_logic_scheduler_arbiters_1_doIt && DispatchPlugin_logic_scheduler_arbiters_1_eusOh[$lane]),(DispatchPlugin_logic_scheduler_arbiters_0_doIt && DispatchPlugin_logic_scheduler_arbiters_0_eusOh[$lane])};";
        $new = "  assign $name = {($grants[$lane]),(DispatchPlugin_logic_scheduler_arbiters_0_doIt && DispatchPlugin_logic_scheduler_arbiters_0_eusOh[$lane])};";
        once($source, $old, $new);
    }
}

sub allow_forwarding_optimization {
    my ($source) = @_;
    # Keep the original forwarding equations and priority; allow Quartus to
    # optimize across the four combinational output boundaries. This added
    # 281 ALMs versus direct grants alone and improved 92.39 to 94.24 MHz.
    for my $lane (0 .. 1) {
        for my $rs (1 .. 2) {
            my $name = "_zz_execute_ctrl1_down_integer_RS${rs}_lane${lane}_1";
            once($source,
                 "  (* keep , syn_keep *) reg        [31:0]   $name /* synthesis syn_keep = 1 */ ;",
                 "  reg        [31:0]   $name;");
        }
    }
}

my $path = shift @ARGV or die "Usage: optimize_os20_timing.pl <netlist>\n";
@ARGV == 0 or die "Unexpected arguments\n";
open my $input, '<', $path or die "$path: $!\n";
my $source = do { local $/; <$input> };
close $input;
check_aligner_contract($source);
my $dispatch_hash = dispatch_contract($source);
$dispatch_hash eq '2b3a6dc7d213946a7674ff8a05ec95a6bf19ebbb994d560a0f52419c3747fcce' or die "Dispatch contract changed ($dispatch_hash); requalify os20 timing rewrites\n";
early_rvc(\$source);
fp_before_select(\$source);
direct_fetch_lowbits(\$source);
aligner_hold(\$source);
local_usedmask(\$source);
early_valid(\$source);
direct_dispatch_grants(\$source);
allow_forwarding_optimization(\$source);
open my $output, '>', $path or die "$path: $!\n";
print {$output} $source;
close $output or die "$path: $!\n";
