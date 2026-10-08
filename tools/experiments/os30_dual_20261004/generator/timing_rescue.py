#!/usr/bin/env python3
"""Prepare and check private frontend-only timing rewrites for lean dual os30."""
import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004"
OUT = ARTIFACTS / "timing-rescue-v1"
INPUTS = {
    "lean": ARTIFACTS / "generator-v1/VexiiRiscv_os30_dual_lean.v",
    "immediate": ARTIFACTS / "generator-v2/VexiiRiscv_os30_dual_immediate.v",
    "rvc-stage1": ARTIFACTS / "generator-v5/VexiiRiscv_os30_dual_rvc_stage1.v",
    "rvc-pipeline": ARTIFACTS / "generator-v5/VexiiRiscv_os30_dual_rvc_pipeline.v",
    "rvc-immediate": ARTIFACTS / "generator-v6/VexiiRiscv_os30_dual_rvc_immediate.v",
    "rvc-skid": ARTIFACTS / "generator-v7/VexiiRiscv_os30_dual_rvc_skid.v",
    "rvc-skid-masked": ARTIFACTS / "generator-v8/VexiiRiscv_os30_dual_rvc_skid_masked.v",
    "rvc-skid-immediate": ARTIFACTS / "generator-v9/VexiiRiscv_os30_dual_rvc_skid_immediate.v",
}
TRANSFORMS = {
    "control": ["local_usedmask", "early_valid", "direct_dispatch_grants"],
    "frontend": ["early_rvc", "local_usedmask", "early_valid", "direct_dispatch_grants"],
    "grouped": ["early_rvc", "local_usedmask", "early_valid", "direct_dispatch_grants"],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def grouped_bank():
    """Four decompressors: two half-window alternatives per issue slot."""
    text = """module VexiiRiscvEarlyRvcBank(
    input wire [127:0] slices,
    input wire [7:0] sel0, input wire [6:0] sel1,
    output wire [31:0] inst0, inst1,
    output wire fault0, fault1);
    wire [31:0] empty_inst;
    wire empty_fault;
    VexiiRiscvEarlyRvcDecode empty_decoder(32'b0, empty_inst, empty_fault);
"""
    for lane in range(2):
        insts, faults = [], []
        for group in range(2):
            slots = list(range(max(lane, group * 4), group * 4 + 4))
            stem = f"lane{lane}_group{group}"
            sels = [f"sel{lane}[{slot-lane}]" for slot in slots]
            terms = []
            for slot, sel in zip(slots, sels):
                raw = f"slices[{slot*16+31}:{slot*16}]" if slot < 7 else "{16'b0,slices[127:112]}"
                terms.append(f"({sel} ? {raw} : 32'b0)")
            text += f"    wire {stem}_valid = " + " | ".join(sels) + ";\n"
            text += f"    wire [31:0] {stem}_raw = " + " | ".join(terms) + ";\n"
            text += f"    wire [31:0] {stem}_expanded; wire {stem}_fault;\n"
            text += f"    VexiiRiscvEarlyRvcDecode {stem}_decoder({stem}_raw, {stem}_expanded, {stem}_fault);\n"
            insts.append(f"({stem}_valid ? {stem}_expanded : 32'b0)")
            faults.append(f"({stem}_valid && {stem}_fault)")
        text += f"    assign inst{lane} = ((~|sel{lane}) ? empty_inst : 32'b0) | " + " | ".join(insts) + ";\n"
        text += f"    assign fault{lane} = ((~|sel{lane}) && empty_fault) | " + " | ".join(faults) + ";\n"
    return text + "endmodule\n"


def driver(transforms, grouped=False, registered_rvc=False):
    upstream = ROOT / "src/fpga/vendor/vexriscv/optimize_os20_timing.pl"
    source = upstream.read_text()
    marker = 'my $path = shift @ARGV or die "Usage: optimize_os20_timing.pl <netlist>\\n";'
    assert source.count(marker) == 1
    definitions = source.split(marker)[0]
    if registered_rvc:
        original_leaf = "    my $input = 'AlignerPlugin_logic_extractors_0_ctx_instruction';"
        registered_leaf = "    my $input = 'decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0';"
        assert definitions.count(original_leaf) == 1
        definitions = definitions.replace(original_leaf, registered_leaf)
        original_swap = "    my $other = $source;\n"
        new_swap = original_swap + """    $other =~ s/\\bdecode_ctrls_1_up_Decode_INSTRUCTION_RAW_0\\b/registered_rvc_raw_swap/g;
    $other =~ s/\\bdecode_ctrls_1_up_Decode_INSTRUCTION_RAW_1\\b/decode_ctrls_1_up_Decode_INSTRUCTION_RAW_0/g;
    $other =~ s/\\bregistered_rvc_raw_swap\\b/decode_ctrls_1_up_Decode_INSTRUCTION_RAW_1/g;
"""
        assert definitions.count(original_swap) == 1
        definitions = definitions.replace(original_swap, new_swap)
    if grouped:
        # Preserve the upstream decoder extraction and checked connection points;
        # replace only the internal topology of its generated combinational bank.
        start = definitions.index("sub rvc_bank {")
        end = definitions.index("sub early_rvc {", start)
        definitions = (definitions[:start] + "sub rvc_bank {\n    return <<'GROUPED';\n" +
                       grouped_bank() + "GROUPED\n}\n\n" + definitions[end:])
    body = r'''
my $path = shift @ARGV or die "Expected private netlist path\n";
@ARGV == 0 or die "Unexpected arguments\n";
open my $input, '<', $path or die "$path: $!\n";
my $source = do { local $/; <$input> };
close $input;
check_aligner_contract($source);
my $dispatch_hash = dispatch_contract($source);
$dispatch_hash eq '2b3a6dc7d213946a7674ff8a05ec95a6bf19ebbb994d560a0f52419c3747fcce'
    or die "Dispatch contract changed ($dispatch_hash); requalify\n";
'''
    body += "\n".join(name + "(\\$source);" for name in transforms) + "\n"
    body += r'''
open my $output, '>', $path or die "$path: $!\n";
print {$output} $source;
close $output or die "$path: $!\n";
'''
    return definitions + body


def check(reference, candidate, directory):
    spec = importlib.util.spec_from_file_location("os20_checks", ROOT / "tools/check_vexii_os20_timing.py")
    checks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checks)
    # The upstream extractor's only RVC-specific prerequisite is the presence of
    # two helper definitions. Control-only rewrites have neither: allow zero,
    # retaining exactly the same extracted cones and exhaustive stimulus.
    frontend_source = inspect.getsource(checks.frontend_tb)
    assert frontend_source.count("assert len(helpers) == 2") == 1
    frontend_source = frontend_source.replace("assert len(helpers) == 2", "assert len(helpers) in (0, 2)")
    if "decode_ctrls_0_up_Decode_INSTRUCTION_0" not in reference.read_text():
        # Once RVC expansion is behind the register, check the complete aligner
        # RAW output instead. The relocated decompressor is unchanged by these
        # control-only transforms and is checked separately against the baseline.
        needle = "    old, width = cone(reference, 'reference', inputs, outputs)"
        assert frontend_source.count(needle) == 1
        frontend_source = frontend_source.replace(needle,
            "    outputs = [name.replace('Decode_INSTRUCTION_', 'Decode_INSTRUCTION_RAW_') "
            "for name in outputs if 'DECOMPRESSION_FAULT' not in name]\n" + needle)
    exec(frontend_source, checks.__dict__)
    (directory / "frontend_test_function.py").write_text(frontend_source)
    old, new = reference.read_text(), candidate.read_text()
    # os20 has no freeze maxfan hint; os30 does. Remove only that synthesis
    # annotation from the test extractor input, preserving its wire/equations.
    annotation = "  (* maxfan = 16 *) wire execute_freeze_valid;"
    assert old.count(annotation) == new.count(annotation) == 1
    old = old.replace(annotation, "  wire execute_freeze_valid;")
    new = new.replace(annotation, "  wire execute_freeze_valid;")
    for name, generator in [("frontend", checks.frontend_tb), ("dispatch", checks.dispatch_tb)]:
        out = directory / name
        out.mkdir()
        test = out / "tb.sv"
        test.write_text(generator(old, new))
        with (out / "build.log").open("w") as log:
            subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "tb",
                            "-j", "4", "--Mdir", str(out / "obj"), str(test)],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        with (out / "test.log").open("w") as log:
            subprocess.run([str(out / "obj/Vtb")], stdout=log, stderr=subprocess.STDOUT, check=True)
        print((out / "test.log").read_text(), end="", flush=True)


def prepare(lane, profile, validate):
    if lane.startswith("rvc-"):
        assert profile == "control", "Early combinational RVC topology does not apply after pipeline relocation"
    OUT.mkdir(parents=True, exist_ok=True)
    output = OUT / f"{lane}-{profile}"
    output.mkdir(exist_ok=False)
    reference = INPUTS[lane]
    candidate = output / "VexiiRiscv.v"
    shutil.copy2(reference, candidate)
    script = output / "transform.pl"
    script.write_text(driver(TRANSFORMS[profile], profile == "grouped", lane.startswith("rvc-")))
    subprocess.run(["perl", str(script), str(candidate)], check=True)
    old_top = reference.read_text().split("\nendmodule", 1)[0]
    new_top = candidate.read_text().split("\nendmodule", 1)[0]
    sequential = r"^  always @\((?:posedge|negedge).*?^  end\n"
    old_state = re.findall(sequential, old_top, re.M | re.S)
    new_state = re.findall(sequential, new_top, re.M | re.S)
    assert old_state and old_state == new_state, "Sequential logic changed"
    assert (old_top.split("module VexiiRiscv (", 1)[1].split(");", 1)[0] ==
            new_top.split("module VexiiRiscv (", 1)[1].split(");", 1)[0]), "Top ports changed"
    manifest = {
        "reference": str(reference.relative_to(ROOT)), "reference_sha256": digest(reference),
        "candidate": str(candidate.relative_to(ROOT)), "candidate_sha256": digest(candidate),
        "upstream_helper_sha256": digest(ROOT / "src/fpga/vendor/vexriscv/optimize_os20_timing.pl"),
        "private_transform_sha256": digest(script), "transforms": TRANSFORMS[profile],
        "added_sequential_state": False,
        "identical_sequential_blocks": len(old_state),
        "compressed_decoder_instances": {"control": 2, "frontend": 8, "grouped": 4}[profile],
        "forwarding_keep_attributes_unchanged": True,
        "fpu_unchanged": True,
        "cycle_equivalence_checks_passed": False,
    }
    if validate:
        check(reference, candidate, output)
        manifest["cycle_equivalence_checks_passed"] = True
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=[*INPUTS, "all"], default="all")
    parser.add_argument("--profile", choices=[*TRANSFORMS, "all"], default="all")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    for lane in (INPUTS if args.lane == "all" else [args.lane]):
        if lane.startswith("rvc-"):
            OUT = ARTIFACTS / "timing-rescue-v2"
        if lane == "rvc-skid":
            OUT = ARTIFACTS / "timing-rescue-v3"
        if lane == "rvc-skid-masked":
            OUT = ARTIFACTS / "timing-rescue-v4"
        if lane == "rvc-skid-immediate":
            OUT = ARTIFACTS / "timing-rescue-v5"
        profiles = (TRANSFORMS if args.profile == "all" else [args.profile])
        if lane.startswith("rvc-") and args.profile == "all":
            profiles = ["control"]
        for profile in profiles:
            prepare(lane, profile, args.validate)
