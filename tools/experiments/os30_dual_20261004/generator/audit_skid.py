#!/usr/bin/env python3
"""Check the private skid handshake/payload contract and emit testbench taps."""
from pathlib import Path
import argparse
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[4]
ART = ROOT / "docs/measurements/os30-dual-20261004"
parser = argparse.ArgumentParser(description=__doc__)
profile_options = parser.add_mutually_exclusive_group()
profile_options.add_argument("--masked", action="store_true", help="Check corrected v8 lane cancellation")
profile_options.add_argument("--immediate", action="store_true", help="Check corrected v9 immediate-only fallback")
args = parser.parse_args()
MASKED = args.masked or args.immediate
GEN = "generator-v9" if args.immediate else "generator-v8" if args.masked else "generator-v7"
PROFILE = "rvc-skid-immediate" if args.immediate else "rvc-skid-masked" if args.masked else "rvc-skid"
RESCUE = "timing-rescue-v5" if args.immediate else "timing-rescue-v4" if args.masked else "timing-rescue-v3"
CPU = ART / RESCUE / (PROFILE + "-control/VexiiRiscv.v")
REF = ART / "generator-v5/VexiiRiscv_os30_dual_rvc_pipeline.v"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def once(source, fragment):
    assert source.count(fragment) == 1, fragment


source = CPU.read_text()
if args.immediate:
    assert "execute_lane1_bypasser_integer_RS2" not in source
manifest = json.loads((CPU.parent / "manifest.json").read_text())
assert digest(CPU) == manifest["candidate_sha256"]
assert manifest["cycle_equivalence_checks_passed"]
assert source.split("module VexiiRiscv (", 1)[1].split(");", 1)[0] == REF.read_text().split("module VexiiRiscv (", 1)[1].split(");", 1)[0]

log = (ART / GEN / (PROFILE + "-generate.log")).read_text()
ages = re.search(r"OS30_DECODE_SKID age=(\d+) flushSources=([0-9,]+)", log)
assert ages
age = int(ages[1])
sources = [int(v) for v in ages[2].split(",")]
assert age == 1005 and sources == [21, 1021, 2020, 2030, 2040]
assert all(v < 1000 or v > age for v in sources)

required = [
    "  assign decode_ctrls_0_down_ready = (! decode_skid_rValid);",
    "  assign decode_ctrls_0_up_ready = decode_ctrls_0_down_isReady;",
    "  assign decode_ctrls_0_down_isReady = decode_ctrls_0_down_ready;",
    "  assign decode_ctrls_0_up_isReady = decode_ctrls_0_up_ready;",
    "  assign decode_skid_gate_up_valid = (decode_ctrls_0_down_isValid || decode_skid_rValid);",
    "  assign decode_skid_gate_up_forgetOne = (|decode_skid_gate_throwWhen_DecodePipelinePlugin_l80);",
    "  assign decode_skid_gate_up_cancel = (|decode_skid_gate_throwWhen_DecodePipelinePlugin_l80);",
    "  assign decode_skid_gate_throwWhen_DecodePipelinePlugin_l80 = decode_logic_skid_flushIt;",
    "  assign decode_ctrls_0_down_forgetOne = (decode_skid_gate_up_forgetOne && (! decode_skid_rValid));",
    "  assign decode_ctrls_0_down_cancel = (decode_skid_gate_up_cancel && (! decode_skid_rValid));",
    "  assign decode_ctrls_0_up_cancel = (|decode_ctrls_0_down_cancel);",
    "  assign decode_ctrls_0_up_isCancel = decode_ctrls_0_up_cancel;",
    "  assign AlignerPlugin_logic_buffer_downFire = (decode_ctrls_0_up_isReady || decode_ctrls_0_up_isCancel);",
    "  assign decode_logic_skid_trapPending = (decode_skid_gate_down_isValid && (|{(decode_skid_gate_down_LANE_SEL_1 && decode_skid_gate_down_TRAP_1),(decode_skid_gate_down_LANE_SEL_0 && decode_skid_gate_down_TRAP_0)}));",
    "  assign when_CtrlLink_l198_2 = (|decode_skid_gate_throwWhen_DecodePipelinePlugin_l80);",
    """  always @(*) begin
    decode_skid_gate_down_valid = decode_skid_gate_up_valid;
    if(when_CtrlLink_l198_2) begin
      decode_skid_gate_down_valid = 1'b0;
    end
  end""",
    """      if(decode_ctrls_0_down_isValid) begin
        decode_skid_rValid <= 1'b1;
      end
      if(decode_skid_gate_up_ready) begin
        decode_skid_rValid <= 1'b0;
      end
      if(decode_skid_gate_up_forgetOne) begin
        decode_skid_rValid <= 1'b0;
      end""",
]
for fragment in required:
    once(source, fragment)
assert re.search(r"assign decode_logic_trapPending\[0\] = .*\|\| decode_logic_skid_trapPending\);", source)
assert len(re.findall(r"^      decode_skid_rValid <= 1'b0;$", source, re.M)) == 1

widths = {name: int(high) + 1 if high else 1 for high, name in re.findall(
    r"^  (?:reg|wire)\s+(?:\[(\d+):0\]\s+)?(\w+);$", source, re.M)}
pairs = re.findall(r"      (\w+_s2mBuffer(?:_\d+)?) <= (\w+);", source)
assert len(pairs) == 32
blocks = re.findall(r"    if\(decode_ctrls_0_down_ready\) begin\n(.*?)    end", source, re.S)
stage1_blocks = re.findall(r"    if\(decode_skid_gate_down_isReady\) begin\n(.*?)    end", source, re.S)
rows = []
for saved, live in pairs:
    line = f"      {saved} <= {live};"
    assert sum(line in block for block in blocks) == 1
    assert len(re.findall(rf"\b{saved}\s*<=", source)) == 1
    gate_up = live.replace("decode_ctrls_0_down_", "decode_skid_gate_up_")
    gate_down = live.replace("decode_ctrls_0_down_", "decode_skid_gate_down_")
    once(source, f"""  always @(*) begin
    if(decode_skid_rValid) begin
      {gate_up} = {saved};
    end else begin
      {gate_up} = {live};
    end
  end""")
    if MASKED and "_LANE_SEL_" in live:
        bypass = gate_up.replace("_up_", "_") + "_bypass"
        once(source, f"  assign {gate_down} = {bypass};")
        once(source, f"  assign {bypass} = ({gate_up} && (! decode_logic_skid_flushIt));")
    else:
        once(source, f"  assign {gate_down} = {gate_up};")
    stage1 = live.replace("decode_ctrls_0_down_", "decode_ctrls_1_up_")
    stage1_capture = f"      {stage1} <= {gate_down};"
    assert sum(stage1_capture in block for block in stage1_blocks) == 1
    assert re.search(rf"^  reg\s+(?:\[\d+:0\]\s+)?{stage1};$", source, re.M)
    assert widths[saved] == widths[live] == widths[gate_up] == widths[gate_down]
    rows.append({"input": live, "saved": saved, "output": gate_down, "bits": widths[saved]})
assert sum(row["bits"] for row in rows) == 278

# Reapplying the saved patch to the pre-skid sources should give exactly the
# elaborated sources. This checks the two files without copying build trees.
import subprocess
import tempfile
with tempfile.TemporaryDirectory(prefix="os30-skid-source-") as temporary:
    stage = Path(temporary)
    for name in ["Param.scala", "decode/DecodePipelinePlugin.scala", "execute/IntAluPlugin.scala"]:
        rel = Path("src/main/scala/vexiiriscv") / name
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        (stage / rel).write_bytes((ART / "generator-v5/source" / rel).read_bytes())
    patches = (["immediate-after-rvc.patch"] if args.immediate else []) + ["decode-skid.patch"] + (["decode-skid-mask.patch"] if MASKED else [])
    for name in patches:
        patch = ROOT / "tools/experiments/os30_dual_20261004/generator" / name
        subprocess.run(["patch", "-p1", "--forward", "-i", str(patch)], cwd=stage, check=True,
                       stdout=subprocess.DEVNULL)
    for path in stage.rglob("*.scala"):
        assert path.read_bytes() == (ART / GEN / "source" / path.relative_to(stage)).read_bytes()

report = {
    "candidate": str(CPU.relative_to(ROOT)), "sha256": digest(CPU),
    "structural_contract_passed": True, "age": age, "actual_flush_ages": sources,
    "payload_bits": 278, "occupancy_bits": 1,
    "ready_only_depends_on_occupancy": True,
    "live_cancel_consumed_by_existing_aligner": True,
    "pending_trap_gated_by_unflushed_frame_valid": True,
    "flush_masks_downstream_lane_selects": MASKED,
    "original_stage1_registers_retained": True,
    "skid_adds_unstalled_forward_cycles": 0,
    "saved_patch_reproduces_elaborated_sources": True,
    "signals": {"full": "decode_skid_rValid", "input_valid": "decode_ctrls_0_down_isValid",
                "input_ready": "decode_ctrls_0_down_ready", "input_cancel": "decode_ctrls_0_down_cancel",
                "output_valid": "decode_skid_gate_down_valid", "output_ready": "decode_skid_gate_down_ready",
                "flush": "decode_logic_skid_flushIt", "forget": "decode_skid_gate_up_forgetOne",
                "trap_pending": "decode_logic_skid_trapPending"},
    "payload": rows,
}
(ART / GEN / "skid-structure.json").write_text(json.dumps(report, indent=2) + "\n")
print(f"PASS: {len(rows)} payload fields, 278 data bits +1 occupancy, flush/cancel/trap contracts and source reproduction")
