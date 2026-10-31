#!/usr/bin/env python3
"""Run the self-contained source, RAM, mixer, MMIO and 18-case service checks."""
import argparse
from pathlib import Path
import subprocess
import sys
from common import HERE, new_output, save, verify_fixtures

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--suite", choices=["all", "sources", "mixer", "wbuf", "periph", "contention"], default="all")
    a = p.parse_args()
    pins = verify_fixtures()
    out = new_output(a.output)
    cases = [("sources", "check_sources.py", []),
             ("mixer", "check_mixer.py", []),
             ("wbuf-row0", "check_wbuf.py", ["--bank-row-track", "0"]),
             ("wbuf-row1", "check_wbuf.py", ["--bank-row-track", "1"]),
             ("periph", "check_periph.py", []),
             ("contention", "check_contention.py", [])]
    results = {}
    for label, script, extra in cases:
        if a.suite != "all" and a.suite != label.split("-")[0]:
            continue
        subprocess.run([sys.executable, str(HERE / script), "--output",
                        str(out / label), *extra], check=True)
        results[label] = str(out / label / "result.json")
    save(out / "receipt.json", dict(completed=True, suite=a.suite,
         fixture_hashes={n:p["sha256"] for n,p in pins.items()}, results=results,
         tools=dict(verilator=subprocess.check_output(["verilator", "--version"], text=True).strip()),
         limits=["Inspect contention/result.json: comparison pass does not mean overload audio passed.",
                 "No physical timing/placement or game FPS is established here."]))
    print("Completed " + str(out))

if __name__ == "__main__":
    main()
