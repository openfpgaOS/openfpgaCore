#!/usr/bin/env python3
"""Stage and generate a private os30 lean-dual experiment; never edit vendor RTL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
VENDOR = ROOT / "src/fpga/vendor/vexriscv"
ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004/generator-v1"
STAGE = ARTIFACTS / "source"
PATCHES = [HERE / "lean-lane.patch"]

# Production os30 flags, except D$=64 KiB; SB remains its original 2/16.
# The same base is used for single, stock dual, and restricted dual controls.
BASE_FLAGS = """
--xlen=32 --with-rvm --with-rva --with-rvf --with-rvc --with-rvZcbm
--with-fetch-l1 --fetch-l1-sets=256 --fetch-l1-ways=2 --fetch-l1-refill-count=2
--fetch-l1-mem-data-width-min=64 --fetch-l1-read-at=1 --fetch-l1-hits-at=2
--fetch-l1-hit-at=2 --fetch-l1-bank-muxes-at=2 --fetch-l1-bank-mux-at=3
--fetch-l1-ctrl-at=3 --fetch-l1-hardware-prefetch=none --fetch-axi4
--with-lsu-l1 --lsu-l1-sets=512 --lsu-l1-ways=2 --lsu-l1-refill-count=2
--lsu-l1-writeback-count=2 --lsu-l1-store-buffer-slots=2
--lsu-l1-store-buffer-ops=16 --lsu-l1-axi4 --lsu-hardware-prefetch=none
--with-btb --btb-sets=1024 --relaxed-btb --relaxed-btb-hit
--with-gshare --gshare-bytes=4096 --with-ras --allow-bypass-from=0
--fpu-ignore-subnormal --fpu-wb-at=1 --fpu-add-preshift-stage=1
--fpu-add-shifter-stage=2 --fpu-add-math-stage=3 --fpu-add-norm-stage=4
--fpu-add-pack-at=5 --relaxed-src --relaxed-branch --relaxed-div --relaxed-shift
--div-radix=4 --with-store-rs2-late --dispatcher-at=2 --reset-vector=0
--region base=0,size=8000,main=0,exe=1
--region base=10000000,size=4000000,main=1,exe=1
--region base=20000000,size=10000000,main=0,exe=0
--region base=30000000,size=1000000,main=0,exe=0
--region base=40000000,size=40000000,main=0,exe=0
--fpu-i2f-pipelined --fma-reduced-accuracy
""".split()
PROFILES = {
    "lean": ("VexiiRiscv_os30_dual_lean.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only"]),
    "stock-dual": ("VexiiRiscv_os30_dual_stock.v", ["--decoders=2", "--lanes=2"]),
    "single64": ("VexiiRiscv_os30_single64.v", []),
    "lean-immediate": ("VexiiRiscv_os30_dual_immediate.v", ["--decoders=2", "--lanes=2", "--lane1-immediate-only"]),
    "lean-i16-d128": ("VexiiRiscv_os30_dual_lean_i16_d128.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only"]),
    "single-i16-d128": ("VexiiRiscv_os30_single_i16_d128.v", []),
    "lean-smallpred": ("VexiiRiscv_os30_dual_lean_smallpred.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only"]),
    "rvc-stage1": ("VexiiRiscv_os30_dual_rvc_stage1.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only", "--rvc-decompress-at=1"]),
    "rvc-pipeline": ("VexiiRiscv_os30_dual_rvc_pipeline.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only", "--rvc-decompress-at=1", "--decoder-at=2"]),
    "rvc-immediate": ("VexiiRiscv_os30_dual_rvc_immediate.v", ["--decoders=2", "--lanes=2", "--lane1-immediate-only", "--rvc-decompress-at=1"]),
    "rvc-skid": ("VexiiRiscv_os30_dual_rvc_skid.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only", "--rvc-decompress-at=1", "--decoder-at=2", "--with-decode-skid"]),
    "rvc-skid-masked": ("VexiiRiscv_os30_dual_rvc_skid_masked.v", ["--decoders=2", "--lanes=2", "--lane1-alu-only", "--rvc-decompress-at=1", "--decoder-at=2", "--with-decode-skid"]),
    "rvc-skid-immediate": ("VexiiRiscv_os30_dual_rvc_skid_immediate.v", ["--decoders=2", "--lanes=2", "--lane1-immediate-only", "--rvc-decompress-at=1", "--decoder-at=2", "--with-decode-skid"]),
}
HELPERS = ["retime_fetch_reads.pl", "retime_aligner_pc.pl", "factor_fetch_ready.pl", "retime_dcache_writes.pl"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if not STAGE.exists():
        shutil.copytree(VENDOR / "VexiiRiscv", STAGE,
                        ignore=shutil.ignore_patterns(".git", "VexiiRiscv*.v", "*.vcd", "simWorkspace"))
        for patch in PATCHES:
            subprocess.run(["patch", "-p1", "--forward", "-i", str(patch)],
                           cwd=STAGE, check=True)
        helpers = ARTIFACTS / "helpers"
        helpers.mkdir()
        for name in HELPERS:
            shutil.copy2(VENDOR / name, helpers / name)
        sources = {}
        for path in sorted(STAGE.rglob("*.scala")):
            if "target" not in path.relative_to(STAGE).parts:
                sources[str(path.relative_to(STAGE))] = digest(path)
        for rel in ["build.sbt", "project/build.properties"]:
            sources[rel] = digest(STAGE / rel)
        source_manifest = {
            "private_only": True,
            "vendor_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                       cwd=VENDOR / "VexiiRiscv", text=True).strip(),
            "patch_sha256": {patch.name: digest(patch) for patch in PATCHES},
            "source_sha256": sources,
            "helper_sha256": {name: digest(helpers / name) for name in HELPERS},
        }
        (ARTIFACTS / "source-manifest.json").write_text(json.dumps(source_manifest, indent=2) + "\n")


def postprocess(path):
    data = path.read_text()
    data, count = re.subn(r"  wire +execute_freeze_valid;",
                         "  (* maxfan = 16 *) wire execute_freeze_valid;", data)
    if count != 1:
        raise RuntimeError(f"Expected one execute_freeze_valid declaration, got {count}")
    data = data.replace("= {$urandom};", "= 0;")
    if "$urandom" in data:
        raise RuntimeError("Unsupported randomization remains")
    data, count = re.subn(r"^module VexiiRiscv \(",
                         '(* altera_attribute = "-name AUTO_SHIFT_REGISTER_RECOGNITION OFF" *)\nmodule VexiiRiscv (',
                         data, flags=re.M)
    if count != 1:
        raise RuntimeError("Expected one top-level declaration")
    path.write_text(data)
    for helper in HELPERS:
        subprocess.run(["perl", str(ARTIFACTS / "helpers" / helper), str(path)], check=True)


def generate(profile):
    name, flags = PROFILES[profile]
    flags = BASE_FLAGS + flags
    if profile in ("lean-i16-d128", "single-i16-d128"):
        flags = [arg.replace("--fetch-l1-sets=256", "--fetch-l1-sets=128")
                 .replace("--lsu-l1-sets=512", "--lsu-l1-sets=1024") for arg in flags]
    if profile in ("lean-smallpred", "rvc-stage1", "rvc-pipeline", "rvc-immediate", "rvc-skid", "rvc-skid-masked", "rvc-skid-immediate"):
        flags = [arg.replace("--lsu-l1-sets=512", "--lsu-l1-sets=1024")
                 .replace("--btb-sets=1024", "--btb-sets=256")
                 .replace("--gshare-bytes=4096", "--gshare-bytes=1024") for arg in flags]
    if profile in ("rvc-pipeline", "rvc-skid", "rvc-skid-masked", "rvc-skid-immediate"):
        flags = [arg.replace("--dispatcher-at=2", "--dispatcher-at=3") for arg in flags]
    generated = STAGE / "VexiiRiscv.v"
    generated.unlink(missing_ok=True)
    command = [
        "docker", "run", "--rm", "--name", f"os30-dual-{profile}",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "-v", f"{ROOT}:{ROOT}", "-v", f"{ROOT}/tools/.vexii-home:/vexiihome",
        "--tmpfs", "/tmp:exec", "-w", str(STAGE), "openfpgaos-vexii",
        "sbt", "-Duser.home=/vexiihome", "-Dsbt.boot.directory=/vexiihome/.sbt/boot",
        "-Dsbt.global.base=/vexiihome/.sbt", "-Dsbt.ivy.home=/vexiihome/.ivy2",
        "-Dsbt.server.forcestart=true", "--batch", "Test/runMain vexiiriscv.Generate " + " ".join(flags),
    ]
    with (ARTIFACTS / f"{profile}-generate.log").open("w") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    output = ARTIFACTS / name
    shutil.copy2(generated, output)
    postprocess(output)
    source = output.read_text()
    if "--lane1-alu-only" in flags or "--lane1-immediate-only" in flags:
        for banned in ["early1_BarrelShifterPlugin", "early1_BranchPlugin", "early1_MulPlugin", "early1_DivPlugin"]:
            if banned in source:
                raise RuntimeError(f"Lean RTL retains {banned}")
        if "early1_IntAluPlugin" not in source:
            raise RuntimeError("Lean RTL lacks second integer ALU")
    if "--lane1-immediate-only" in flags and "execute_lane1_bypasser_integer_RS2" in source:
        raise RuntimeError("Immediate-only RTL retains second RF operand")
    manifest = {"profile": profile, "flags": flags, "command": command,
                "rtl": str(output.relative_to(ROOT)), "rtl_sha256": digest(output),
                "rtl_bytes": output.stat().st_size,
                "source_manifest_sha256": digest(ARTIFACTS / "source-manifest.json"),
                "postprocessing": {"maxfan": 16, "no_shift_taps": True, "helpers": HELPERS}}
    (ARTIFACTS / f"{profile}-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({key: manifest[key] for key in
                      ("profile", "rtl", "rtl_sha256", "rtl_bytes")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=[*PROFILES, "all"], default="lean")
    parser.add_argument("--stage-only", action="store_true")
    args = parser.parse_args()
    for profile in (PROFILES if args.profile == "all" else [args.profile]):
        HELPERS = ["retime_fetch_reads.pl", "retime_aligner_pc.pl", "factor_fetch_ready.pl", "retime_dcache_writes.pl"]
        if profile == "lean-immediate":
            ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004/generator-v2"
            STAGE = ARTIFACTS / "source"
            PATCHES = [HERE / "lean-lane.patch", HERE / "immediate-lane.patch"]
        elif profile in ("lean-i16-d128", "single-i16-d128"):
            ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004/generator-v3"
            STAGE = ARTIFACTS / "source"
            PATCHES = [HERE / "lean-lane.patch"]
        elif profile == "lean-smallpred":
            ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004/generator-v4"
            STAGE = ARTIFACTS / "source"
            PATCHES = [HERE / "lean-lane.patch"]
        elif profile in ("rvc-stage1", "rvc-pipeline"):
            ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004/generator-v5"
            STAGE = ARTIFACTS / "source"
            PATCHES = [HERE / "lean-lane.patch", HERE / "rvc-pipeline.patch"]
        elif profile == "rvc-immediate":
            ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004/generator-v6"
            STAGE = ARTIFACTS / "source"
            PATCHES = [HERE / "lean-lane.patch", HERE / "rvc-pipeline.patch", HERE / "immediate-after-rvc.patch"]
        elif profile in ("rvc-skid", "rvc-skid-masked", "rvc-skid-immediate"):
            version = {"rvc-skid": 7, "rvc-skid-masked": 8, "rvc-skid-immediate": 9}[profile]
            ARTIFACTS = ROOT / "docs/measurements/os30-dual-20261004" / f"generator-v{version}"
            STAGE = ARTIFACTS / "source"
            PATCHES = [HERE / "lean-lane.patch", HERE / "rvc-pipeline.patch", HERE / "decode-skid.patch"]
            if profile != "rvc-skid":
                PATCHES.append(HERE / "decode-skid-mask.patch")
            if profile == "rvc-skid-immediate":
                PATCHES.insert(2, HERE / "immediate-after-rvc.patch")
            # The registered-ready skid replaces this helper's direct stage0->1
            # ready-chain contract. Preserve the generator's new ready equation.
            HELPERS.remove("factor_fetch_ready.pl")
        stage()
        if not args.stage_only:
            generate(profile)
