#!/usr/bin/env python3
"""Generate and validate an isolated OS20 radix4 CPU. Never edit active defaults."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from measure import measure

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
VENDOR = ROOT / "src/fpga/vendor/vexriscv"
BODY_SHA = {
    2: "d58f23c264b533fb2e6f7e2c8e8b84c3b30d5d3d624d9e6811242c3e49827e82",
    4: "35b663636545144575dc3b69cad64b1e1de11faea44a4c5b0e26bdedcce24d9e",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, log):
    with log.open("w") as output:
        subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True)


def generate(out):
    stage = out / "generator"
    stage.mkdir()
    for source in VENDOR.iterdir():
        if source.name == "VexiiRiscv" or source.name.startswith("._"):
            continue
        if source.is_dir():
            shutil.copytree(source, stage / source.name)
        else:
            shutil.copy2(source, stage / source.name)
    shutil.copytree(VENDOR / "VexiiRiscv", stage / "VexiiRiscv",
                    ignore=shutil.ignore_patterns(".git", "VexiiRiscv*.v", "*.vcd", "simWorkspace"))
    subprocess.run(["git", "init", "-q", str(stage / "VexiiRiscv")], check=True)
    source_inputs = {}
    for source in stage.rglob("*"):
        if not source.is_file() or ".git" in source.parts or "target" in source.parts:
            continue
        if source.suffix in [".scala", ".sbt", ".pl", ".sh", ".patch", ".cfg"] or source.name == "build.properties":
            source_inputs[str(source.relative_to(stage))] = sha(source)
    (out / "source-inputs.json").write_text(json.dumps(source_inputs, indent=2, sort_keys=True) + "\n")
    config = stage / "configs/os20.cfg"
    template = re.sub(r"^DIV_RADIX=.*\n?", "", config.read_text(), flags=re.M)
    output = {}
    for radix in [2, 4]:
        config.write_text(template + f"\n# Isolated validation only.\nDIV_RADIX={radix}\n")
        run(["bash", str(stage / "generate_vexii.sh"), "os20"], out / f"radix{radix}-generate.log")
        cpu = out / f"VexiiRiscv_os20_radix{radix}.v"
        shutil.copyfile(stage / "VexiiRiscv/VexiiRiscv_os20.v", cpu)
        body = re.sub(r"^// (?:Generator|Git hash).*\n", "", cpu.read_text(), flags=re.M)
        if hashlib.sha256(body.encode()).hexdigest() != BODY_SHA[radix]:
            raise RuntimeError(f"Unreviewed radix{radix} CPU body; no tests or qualification inherited")
        output[radix] = cpu
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New isolated directory")
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    cpus = generate(out)
    run([sys.executable, str(ROOT / "tools/check_vexii_divider.py"),
         "--radix2", str(cpus[2]), "--radix4", str(cpus[4]),
         "--output", str(out / "component-test"), "--jobs", "2"], out / "component-console.log")
    for radix in [2, 4]:
        run([sys.executable, str(ROOT / "tools/check_vexii_interrupts.py"),
             "--profile", "pocket", "--bank-row-track", "0", "--netlist", str(cpus[radix]),
             "--output", str(out / f"irq-radix{radix}")], out / f"irq-radix{radix}-console.log")
    latency = measure(out)
    result = dict(
        scope="OS20 radix4 functional/synthetic-performance validation; no Quartus or application FPS measurement.",
        cpu_sha256={str(radix): sha(path) for radix, path in cpus.items()},
        cpu_body_sha256={str(radix): expected for radix, expected in BODY_SHA.items()},
        component_pass="PASS 200397" in (out / "component-test/test.log").read_text(),
        interrupt_stress={}, latency_result_sha256=sha(out / "isa-latency/results.json"),
        configured_clock_changed=False, production_files_changed=False, deployed=False)
    for radix in [2, 4]:
        text = (out / f"irq-radix{radix}/test.log").read_text()
        result["interrupt_stress"][str(radix)] = dict(
            cycles=int(re.search(r"^cycles=(\d+)", text, re.M)[1]),
            timer_interrupts=int(re.search(r"IRQ count: ([0-9a-f]+)", text)[1], 16),
            passed="CPU stress PASS" in text,
            log_sha256=sha(out / f"irq-radix{radix}/test.log"))
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
