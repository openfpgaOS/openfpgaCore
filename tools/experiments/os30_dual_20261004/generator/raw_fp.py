#!/usr/bin/env python3
"""Guarded RAW FP classification for already-qualified stage-one RVC CPUs."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "docs/measurements/os30-dual-20261004"
AUDIT = ART / "raw-fp-flags-audit-v1/manifest.json"
REFERENCE = ART / "generator-v5/VexiiRiscv_os30_dual_rvc_stage1.v"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classifier_contract(source, lane):
    assigns = dict(re.findall(r"^  assign (\w+) = (.*);$", source, re.M))
    instruction = f"decode_ctrls_1_down_Decode_INSTRUCTION_{lane}"
    roots = [f"decode_ctrls_1_down_DecoderPlugin_logic_NEED_{kind}_{lane}" for kind in ("FPU", "RM")]
    visited = set()
    def visit(name):
        if name == instruction or name in visited:
            return
        assert name in assigns, name
        visited.add(name)
        for dep in re.findall(r"\b[A-Za-z_]\w*\b", assigns[name]):
            if dep == instruction or dep in assigns:
                visit(dep)
    for name in roots:
        visit(name)
    renames = {instruction: "INST"}
    for kind in ("FPU", "RM"):
        renames[f"decode_ctrls_1_down_DecoderPlugin_logic_NEED_{kind}_{lane}"] = f"NEED_{kind}"
    renames[f"decode_ctrls_1_down_FpuPackerPlugin_RESERVED_ON_early0_AT_10_{lane}"] = "RESERVED"
    def rename(text):
        for old, new in renames.items():
            text = text.replace(old, new)
        return text
    return {rename(name): rename(assigns[name]) for name in sorted(visited)}


def replace_once(source, old, new):
    assert source.count(old) == 1, old
    return source.replace(old, new)


def transform(source):
    expected = classifier_contract(REFERENCE.read_text(), 0)
    for lane in range(2):
        assert classifier_contract(source, lane) == expected, "Generated FP classifier contract changed"
        raw = f"decode_ctrls_1_up_Decode_INSTRUCTION_RAW_{lane}"
        assert re.search(rf"^  reg +\[31:0\] +{raw};$", source, re.M)
        # Exact classifier extracted and qualified by check_raw_fp_flags.py.
        ordinary_fpu = " || ".join(
            f"(({raw} & 32'h{mask:08x}) == 32'h{value:08x})" for mask, value in
            [(0x60, 0x40), (0x5c, 4), (0xffe02050, 0x00202050),
             (0xffe01050, 0x00201050), (0xffd02050, 0x00102050), (0xffd01050, 0x00101050)])
        ordinary_rm = " || ".join(
            f"(({raw} & 32'h{mask:08x}) == 32'h{value:08x})" for mask, value in
            [(0x20000060, 0x40), (0x70, 0x40)])
        compressed_fpu = " || ".join(f"({{{raw}[1:0], {raw}[15:13]}} == 5'd{family})"
                                     for family in (3, 5, 7, 19, 23))
        for kind, expr, suffix in [("FPU", f"({raw}[1:0] != 2'b11) ? ({compressed_fpu}) : ({ordinary_fpu})", "_1"),
                                   ("RM", f"({raw}[1:0] == 2'b11) && ({ordinary_rm})", "")]:
            name = f"decode_ctrls_1_down_DecoderPlugin_logic_NEED_{kind}_{lane}"
            # Only declaration, assignment and fp_triggered may reference these
            # final flags. Don't-care values on RVC faults are masked there by
            # the independent unchanged LEGAL/decompression-fault gate.
            assert len(re.findall(rf"\b{name}\b", source)) == 3, name
            source = replace_once(source, f"  assign {name} = _zz_{name}{suffix}[0];",
                                  f"  assign {name} = {expr};")
        source = replace_once(source,
            f"  assign DecoderPlugin_logic_laneLogic_{lane}_fp_instRm = decode_ctrls_1_down_Decode_UOP_{lane}[14 : 12];",
            f"  assign DecoderPlugin_logic_laneLogic_{lane}_fp_instRm = {raw}[14 : 12];")
    return source


def prove(old_source, new_source, out):
    spec = importlib.util.spec_from_file_location("cones", ROOT / "tools/check_vexii_os20_timing.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tb = ""
    for lane in range(2):
        inputs = [f"decode_ctrls_1_up_Decode_INSTRUCTION_RAW_{lane}",
                  "PrivilegedPlugin_api_harts_0_fpuEnable", "FpuCsrPlugin_api_rm"]
        outputs = [f"decode_ctrls_1_down_Decode_DECOMPRESSION_FAULT_{lane}",
                   f"DecoderPlugin_logic_laneLogic_{lane}_fp_triggered"]
        a, aw = mod.cone(old_source, f"original{lane}", inputs, outputs)
        b, bw = mod.cone(new_source, f"candidate{lane}", inputs, outputs)
        assert aw == bw == 2
        tb += a + b
    tb += """module tb;
reg [31:0] raw;
reg enable_fp;
reg [2:0] frm;
wire [1:0] a0,b0,a1,b1;
original0 ref0(raw,enable_fp,frm,a0);
candidate0 cand0(raw,enable_fp,frm,b0);
original1 ref1(raw,enable_fp,frm,a1);
candidate1 cand1(raw,enable_fp,frm,b1);
task check; begin
for(integer flags=0;flags<16;flags++) begin
 {enable_fp,frm}=4'(flags);#1;
 if((|a0)!==(|b0) || (|a1)!==(|b1))
  $fatal(1,"RAW FP legality mismatch raw=%h fp_state=%h",raw,flags);
end
end endtask
initial begin
for(integer i=0;i<65536;i++) begin raw={$random,16'(i)};check();end
for(integer i=0;i<1000000;i++) begin raw=$random;check();end
$display("PASS: actual candidate/reference FP legality, both lanes, 65536 low16 encodings and 1000000 random words x16 dynamic FP CSR states");
$finish;
end
endmodule
"""
    (out / "tb.sv").write_text(tb)
    with (out / "build.log").open("w") as log:
        subprocess.run(["verilator", "--binary", "--timing", "-Wno-fatal", "--top-module", "tb", "-j", "4",
                        "--Mdir", str(out / "obj"), str(out / "tb.sv")], stdout=log, stderr=subprocess.STDOUT, check=True)
    with (out / "test.log").open("w") as log:
        subprocess.run([str(out / "obj/Vtb")], stdout=log, stderr=subprocess.STDOUT, check=True)


if __name__ == "__main__":
    audit = json.loads(AUDIT.read_text())
    assert "all16 FP CSR states" in audit["results"]
    assert audit["input_hashes"][str(REFERENCE.relative_to(ROOT))] == digest(REFERENCE)
    for profile in ("rvc-stage1-control", "rvc-immediate-control"):
        original = ART / "timing-rescue-v2" / profile / "VexiiRiscv.v"
        out = ART / "raw-fp-v1" / profile
        out.mkdir(parents=True, exist_ok=False)
        candidate = out / "VexiiRiscv.v"
        old = original.read_text()
        new = transform(old)
        candidate.write_text(new)
        assert re.findall(r"^  always @\((?:posedge|negedge).*?^  end\n", old, re.M | re.S) == re.findall(
            r"^  always @\((?:posedge|negedge).*?^  end\n", new, re.M | re.S)
        prove(old, new, out)
        manifest = {"reference": str(original.relative_to(ROOT)), "reference_sha256": digest(original),
                    "candidate": str(candidate.relative_to(ROOT)), "candidate_sha256": digest(candidate),
                    "audit_manifest_sha256": digest(AUDIT), "generator_sha256": digest(Path(__file__)),
                    "added_registers_or_cycles": False, "actual_emitted_cone_equivalence_passed": True,
                    "tests": (out / "test.log").read_text()}
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest), flush=True)
