#!/usr/bin/env python3
"""Run renderer/loader regressions with ASan/UBSan; no game assets needed."""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "src/quake2"
with tempfile.TemporaryDirectory(prefix="quake2-renderer-") as tmp:
    flags = [
        os.environ.get("CC", "cc"), "-std=gnu11", "-O1", "-g", "-DOF_PC",
        "-DYQ2_OPENFPGA=1", "-DYQ2_GL1_GLES=1", "-ffunction-sections",
        '-DYQ2OSTYPE="test"', '-DYQ2ARCH="host"',
        "-fdata-sections", "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-I" + str(GAME), "-I" + str(GAME / "src/common/header"),
        "-I" + str(GAME / "src/client/refresh/gl1/glad-gles1/include"),
        "-I" + str(ROOT / "src/sdk/include"),
    ]
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
    for name, extra in (
        ("renderer", []),
        ("worldgeometry", []),
        ("worldpackets", []),
        ("aliascache", []),
        ("nativemesh", []),
        ("nativeupload", []),
        ("aliasmesh", [str(GAME / "src/common/shared/shared.c")]),
        ("light", []),
        ("lightmap_history", [str(ROOT / "tools/tests/quake2_lightmap_bridge.c")]),
        ("image", []),
        ("models", [str(GAME / "src/client/refresh/files/models.c"),
                    "-Werror=frame-larger-than=32768"]),
        ("warp", [str(GAME / "src/client/refresh/gl1/gl1_warp.c")]),
        ("sound", []),
        ("soundbackend", []),
        ("network", []),
        ("videomenu", []),
        ("input", []),
        ("options", [str(GAME / "src/common/shared/shared.c")]),
    ):
        exe = Path(tmp) / (name + "-test")
        subprocess.run(flags + extra + [
            str(ROOT / ("tools/tests/quake2_" + name + "_test.c")),
            "-Wl,--gc-sections", "-lm", "-o", str(exe),
        ], check=True)
        subprocess.run([str(exe)], env=env, check=True)
