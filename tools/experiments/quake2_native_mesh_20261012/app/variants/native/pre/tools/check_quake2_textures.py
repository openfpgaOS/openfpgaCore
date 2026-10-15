#!/usr/bin/env python3
"""Exercise actual candidate manager + extracted lifecycle under ASan/UBSan."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def function(text, name):
    match = re.search(r"(?m)^(?:static )?(?:void|int)\n" + re.escape(name) + r"\([^;]*?\)\n\{", text)
    if not match:
        raise ValueError("Function not found: " + name)
    # Mask comments/string literals without altering offsets before brace scan.
    mask = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                  lambda m: " " * len(m.group()), text, flags=re.S)
    start = text.index("{", match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (mask[end] == "{") - (mask[end] == "}")
        end += 1
    return text[match.start():end] + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    root = args.candidate_root.resolve()
    emit_path = root / "src/quake2/openfpga/of_emit_q2.c"
    source = emit_path.read_text()
    names = ["ofq2_texture_route_sdram", "OFQ2_Init", "OFQ2_TexInit", "OFQ2_TexReset",
             "OFQ2_TexCreate", "OFQ2_TexBindDomain", "OFQ2_Shutdown"]
    extracted = "\n".join(function(source, name) for name in names)
    with tempfile.TemporaryDirectory(prefix="quake2-texture-") as temp:
        work = Path(temp)
        (work / "extracted_texture_functions.inc").write_text(extracted)
        exe = work / "texture_test"
        cmd = [os.environ.get("CC", "cc"), "-std=c11", "-O1", "-g",
               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-fno-pie", "-no-pie",
               "-I" + str(work), "-I" + str(root / "src/sdk/include"),
               "-I" + str(root / "src/quake2/openfpga"),
               str(HERE / "tests/quake2_texture_test.c"), "-o", str(exe)]
        subprocess.run(cmd, check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True,
                                env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0",
                                         UBSAN_OPTIONS="halt_on_error=1"))
        if result.returncode:
            raise RuntimeError("Texture helper test failed:\n" + result.stdout + result.stderr)
    print(result.stdout, end="")
    if args.report:
        inputs = [emit_path, root / "src/sdk/include/of_texture.h",
                  root / "src/quake2/openfpga/of_emit_q2.h", root / "src/sdk/include/of_caps.h",
                  HERE / "tests/quake2_texture_test.c", Path(__file__)]
        args.report.write_text(json.dumps({"passed": True, "candidate_root": str(root),
            "test_stdout": result.stdout, "extracted_functions": names,
            "extracted_sha256": hashlib.sha256(extracted.encode()).hexdigest(),
            "inputs": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
            "limitations": ["Records MMIO leaf actions; not a cycle-accurate GPU/cache model.",
                            "Does not prove device FPS or sustained performance."]}, indent=2) + "\n")


if __name__ == "__main__":
    main()
