#!/usr/bin/env python3
"""Freeze and validate the integrated two-bank DMA/microengine/ring frontend."""
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
    ap.add_argument("--code-pages", type=int, choices=[2,3], default=3)
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / "src"
    frozen.mkdir()
    sources = [*sorted(HERE.glob("gpu_mesh*.v")),
               HERE / "frontend_test.cpp", *sorted((HERE.parent / "engine").glob("*.v")),
               *sorted((HERE.parent / "kernel").glob("gpu_mesh*.v"))]
    manifest = {}
    for source in sources:
        data = source.read_bytes()
        (frozen / source.name).write_bytes(data)
        manifest[str(source.relative_to(ROOT))] = hashlib.sha256(data).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    command = ["verilator", "--cc", "--exe", "--build", "-j", "3", "-Wno-SYMRSVDWORD", "-Wno-fatal",
               "--top-module", "gpu_mesh_frontend", "--Mdir", str(out / "obj"),
               f"-GCODE_PAGES={args.code_pages}", "-CFLAGS", f"-O2 -std=c++17 -DFRONTEND_CODE_PAGES={args.code_pages}",
               *map(str, [frozen / s.name for s in sources])]
    with (out / "build.log").open("w") as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    with (out / "run.log").open("w") as log:
        subprocess.run([str(out / "obj/Vgpu_mesh_frontend")], check=True, stdout=log, stderr=subprocess.STDOUT)
    result = json.loads((out / "run.log").read_text().splitlines()[-1])
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))

if __name__ == "__main__":
    main()
