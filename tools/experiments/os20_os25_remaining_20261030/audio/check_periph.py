#!/usr/bin/env python3
"""Retain complete peripheral/MMIO readback regression."""
import argparse
from pathlib import Path
import re
from common import build, invoke, new_output, save, stage, verify_fixtures

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    verify_fixtures()
    out = new_output(a.output)
    names = ["tb_axi_periph.v", "altsyncram_stub.v", "axi_periph_slave.v",
             "snac_shifter.v", "sync_fifo.v", "common.v", "tb_axi_periph_main.cpp",
             "analogizer_psx.sv", "dualshock_controller.v"]
    source = stage(out / "sources", names)
    exe = build(out, source, "tb_axi_periph", names,
                ["--trace", "-Wall", "-Wno-WIDTHEXPAND", "-Wno-CASEINCOMPLETE",
                 "-Wno-SYNCASYNCNET", "+define+INCLUDE_ANALOGIZER", "+define+INCLUDE_LINK"])
    invoke([str(exe)], out / "run.log")
    checks = re.search(r"=== Results: (\d+) passed, (\d+) failed ===", (out / "run.log").read_text())
    if not checks or tuple(map(int, checks.groups())) != (159, 0):
        raise ValueError("Incomplete peripheral/MMIO regression")
    save(out / "result.json", dict(passed=True, checks=159, failures=0))
    print("PASS: 159 peripheral/MMIO checks")

if __name__ == "__main__":
    main()
