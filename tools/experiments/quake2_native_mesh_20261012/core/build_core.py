#!/usr/bin/env python3
"""Snapshot and fit the isolated native mesh prototype; never deploy.

Keep each attempt in a new directory. Timing failures remain failures even if
assembly produced a usable experimental bitstream. No seed sweep is performed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--without-mixer", action="store_true")
    args = p.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        p.error("Output must be a new directory")
    shutil.copytree(source, output, ignore=shutil.ignore_patterns(
        "db", "incremental_db", "output_files", "*.log", "*.rpt", "*.summary"))
    directory = output / "src/fpga/targets/pocket"
    qsf = directory / "ap_core.qsf"
    text = qsf.read_text()
    if args.without_mixer:
        line = "set_global_assignment -name VERILOG_MACRO INCLUDE_HW_MIXER\n"
        assert text.count(line) == 1
        text = text.replace(line, "")
        qsf.write_text(text)
    assert "VERILOG_MACRO INCLUDE_GPU_NATIVE_MESH" in text
    original = output / "build-receipt.json"
    original.rename(output / "parent-preparation-receipt.json")
    receipt = dict(source=str(source), output=str(output),
                   mixer=not args.without_mixer, deployed=False,
                   timing_passed=False, commands=[], returncodes={},
                   recipe_sha256=sha(Path(__file__)),
                   source_sha256={str(f.relative_to(output)): sha(f)
                                  for f in sorted(output.rglob("*")) if f.is_file()})

    def save():
        (output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

    quartus = Path("/home/alberto/altera_lite/25.1std/quartus/bin")
    version = subprocess.check_output([str(quartus / "quartus_sh"), "--version"], text=True)
    if "25.1std.0 Build 1129" not in version:
        raise ValueError("Unexpected Quartus version: " + version)
    receipt["quartus_version"] = version.strip()
    save()
    steps = [(phase, [str(quartus / ("quartus_" + phase)), "ap_core"])
             for phase in ("map", "fit", "asm", "sta")]
    steps.append(("corners", [str(quartus / "quartus_sta"), "-t",
        str(output / "tools/report_core_timing.tcl"), "ap_core", str(output / "timing")]))
    for phase, command in steps:
        print("Starting", phase, "mixer", receipt["mixer"], flush=True)
        before = time.monotonic()
        receipt["commands"].append(dict(phase=phase, argv=command, cwd=str(directory)))
        save()
        with (output / (phase + ".log")).open("w") as stream:
            result = subprocess.run(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT)
        receipt["returncodes"][phase] = result.returncode
        receipt["commands"][-1]["elapsed_seconds"] = time.monotonic() - before
        save()
        if result.returncode:
            print("Failed", phase, "see", output / (phase + ".log"), flush=True)
            return result.returncode
        if phase == "asm":
            reverse = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))
            artifact = output / "os30-native-mesh.rbf_r"
            artifact.write_bytes((directory / "output_files/ap_core.rbf").read_bytes().translate(reverse))
            receipt["bitstream_sha256"] = sha(artifact)
            save()
    receipt["timing_passed"] = True
    save()
    print("Completed constrained all-corner timing checks", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
