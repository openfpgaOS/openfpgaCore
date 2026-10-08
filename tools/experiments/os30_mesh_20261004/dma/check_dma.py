#!/usr/bin/env python3
"""Build and run randomized standalone and production-arbiter DMA tests."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / "src"
    frozen.mkdir()
    sources = [HERE / "gpu_mesh_dma.v", HERE / "dma_tb.v", HERE / "dma_test.cpp",
               ROOT / "src/fpga/common/axi_sdram_arbiter.v"]
    manifest = {}
    for source in sources:
        data = source.read_bytes()
        (frozen / source.name).write_bytes(data)
        manifest[str(source.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    command = ["verilator", "--cc", "--exe", "--build", "-j", "3", "-Wno-fatal",
               "--top-module", "dma_tb", "--Mdir", str(out / "obj"),
               "-CFLAGS", "-O2 -std=c++17", *map(str, [frozen / s.name for s in sources])]
    with (out / "build.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    with (out / "run.log").open("w") as log:
        subprocess.run([str(out / "obj/Vdma_tb")], check=True, stdout=log, stderr=subprocess.STDOUT)
    result = json.loads((out / "run.log").read_text().splitlines()[-1])
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
