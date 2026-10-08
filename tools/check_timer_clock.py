#!/usr/bin/env python3
"""Run the shared Pocket/MiSTer timer HAL at production and trial clock rates."""
import argparse
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path,
                        default=ROOT / "src/firmware/os/targets/pocket/timer.c")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="openfpgaos-timer-") as tmp:
        binary = Path(tmp) / "timer-test"
        subprocess.run([
            "cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2", "-g",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            f'-DTIMER_SOURCE="{args.source.resolve()}"',
            f"-I{ROOT / 'src/firmware/os/hal'}",
            str(ROOT / "tools/tests/test_timer_clock.c"), "-o", str(binary),
        ], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    main()
