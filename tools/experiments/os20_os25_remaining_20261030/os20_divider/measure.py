#!/usr/bin/env python3
"""Exercise integer DIV/REM ISA edges and paired dependent-operation throughput."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def measure(base):
    base = base.resolve()
    out = base / "isa-latency"
    out.mkdir()
    for name in ["start.S", "link.ld"]:
        shutil.copyfile(ROOT / "src/fpga/test/cpu_stress" / name, out / name)
    shutil.copyfile(HERE / "main.c", out / "main.c")
    rng = random.Random(20261030)
    edge = [0, 1, 2, 3, 7, 15, 16, 127, 128, 255, 256, 65535, 65536,
            0x7fffffff, 0x80000000, 0xfffffffe, 0xffffffff]
    pairs = [(a, b) for a in edge for b in edge]
    pairs += [(rng.getrandbits(32), rng.getrandbits(32)) for _ in range(2048)]
    rows = []
    signed = lambda value: value if value < 0x80000000 else value - 0x100000000
    for a, b in pairs:
        sa, sb = signed(a), signed(b)
        if not b:
            q, r, uq, ur = 0xffffffff, a, 0xffffffff, a
        else:
            q = abs(sa) // abs(sb)
            if (sa < 0) != (sb < 0):
                q = -q
            r, uq, ur = sa - q * sb, a // b, a % b
        rows.append([a, b, q & 0xffffffff, r & 0xffffffff, uq, ur])
    (out / "vectors.h").write_text(
        "static const unsigned cases[][6]={\n" +
        ",\n".join("{" + ",".join(hex(x) + "u" for x in row) + "}" for row in rows) +
        "\n};\n")
    (out / "vectors.json").write_text(json.dumps(
        dict(integer_pairs=len(rows), checked_instructions=4 * len(rows),
             seed=20261030, edge_values=edge), indent=2) + "\n")
    container = ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
                 "-v", f"{ROOT}:{ROOT}", "-v", f"{out}:{out}", "-w", str(out),
                 "openfpgaos-firmware"]
    with (out / "build.log").open("w") as log:
        commands = [
            ["riscv64-unknown-elf-gcc", "-march=rv32imafc_zicsr_zifencei",
             "-mabi=ilp32f", "-O2", "-ffreestanding", "-fno-builtin", "-nostdlib",
             "-nostartfiles", "-Wl,-T,link.ld", "-Wl,--no-relax", "start.S", "main.c",
             "-o", "division.elf"],
            ["riscv64-unknown-elf-objcopy", "-O", "binary", "division.elf", "division.bin"],
        ]
        for command in commands:
            subprocess.run(container + command, stdout=log, stderr=subprocess.STDOUT, check=True)
    shutil.copyfile(base / "irq-radix2/firmware/firmware.mif", out / "firmware.mif")
    results = {}
    for mode, scan in [("quiet", "0"), ("scanout", "1")]:
        for radix in [2, 4]:
            logpath = out / f"{mode}-radix{radix}.log"
            env = dict(os.environ, SCAN_ENABLE=scan, SCAN_PERIOD="700",
                       SCAN_LEN="128", SCAN_PHASE="317")
            bench = base / f"irq-radix{radix}/bench/Vtb_system"
            with logpath.open("w") as log:
                subprocess.run([str(bench), "division.bin", "8000000"], cwd=out,
                               env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
            text = logpath.read_text()
            if "DIVLAT PASS HAL init" not in text or "DIVISA PASS 00002484" not in text:
                raise RuntimeError("Missing ISA/latency verdict: " + str(logpath))
            if "WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0" not in text:
                raise RuntimeError("Write scoreboard did not finish cleanly")
            parsed = [[int(x, 16) for x in row.split()]
                      for row in re.findall(r"^DIVLAT ([0-9a-f ]+)$", text, re.M)]
            if len(parsed) != 14 or any(len(row) != 5 for row in parsed):
                raise RuntimeError("Incomplete latency rows")
            for i, row in enumerate(parsed):
                if row[:3] != [i // 7, i % 7, 1024]:
                    raise RuntimeError("Misordered latency rows")
            results[f"{mode}-radix{radix}"] = dict(
                rows=parsed, total_cycles=int(re.search(r"^cycles=(\d+)", text, re.M)[1]),
                log_sha256=sha(logpath), simulator_sha256=sha(bench))
    for mode in ["quiet", "scanout"]:
        a, b = (results[f"{mode}-radix{r}"]["rows"] for r in [2, 4])
        if [row[4] for row in a] != [row[4] for row in b]:
            raise RuntimeError("Architectural outputs differ between CPUs")
    report = dict(program_sha256=sha(out / "division.elf"),
                  names=["divu", "remu", "div", "rem", "fdiv.s", "fmul.s", "fsqrt.s"],
                  rows_format=["round", "kind", "operations", "cycles", "final_result"],
                  runs=results)
    (out / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    measure(args.campaign)
    print("PASS: 9348 integer DIV/REM cases and paired quiet/scanout latency runs")
