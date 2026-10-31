#!/usr/bin/env python3
"""Compare baseline, attribute-only RAM and nonstream bypass mixer variants."""
import argparse
import csv
import io
import os
from pathlib import Path
import re
from common import build, invoke, mixer_variant, new_output, save, sha, stage, verify_fixtures

NAMES = ["tb_audio_mixer.v", "tb_audio_mixer_main.cpp", "independent_reference.hpp",
         "sync_fifo.v", "mister_audio_output.v"]
SOURCES = ["tb_audio_mixer.v", "audio_mixer.v", "sync_fifo.v",
           "mister_audio_output.v", "tb_audio_mixer_main.cpp"]

def run_variant(out, variant):
    out.mkdir()
    source = stage(out / "sources", NAMES, mixer_variant(variant))
    exe = build(out, source, "tb_audio_mixer", SOURCES,
                ["--assert", "-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC",
                 "-Wno-CASEINCOMPLETE", "-Wno-SYNCASYNCNET", "-Wno-UNOPTFLAT"])
    invoke([str(exe)], out / "inherited.log", dict(os.environ, AUDIO_PERF_REPORT="1"))
    invoke([str(exe), "--reference"], out / "reference.log")
    invoke([str(exe), "--capacity"], out / "capacity.csv")
    inherited = (out / "inherited.log").read_text()
    checks = re.search(r"=== Results: (\d+) passed, (\d+) failed ===", inherited)
    if not checks or tuple(map(int, checks.groups())) != (177, 0):
        raise ValueError("Incomplete inherited mixer test")
    reference = (out / "reference.log").read_text()
    keys = ["samples", "scenarios", "errors", "saturation_samples",
            "stream_holds", "hash", "processing_cycles"]
    match = re.search(r"REFERENCE samples=(\d+) scenarios=(\d+) errors=(\d+) saturation_samples=(\d+) stream_holds=(\d+) hash=([0-9a-f]+) processing_cycles=(\d+)", reference)
    irq = re.search(r"IRQ_READBACK cases=(\d+) position_reads=(\d+) failures=(\d+)", reference)
    if not match or match.groups()[:3] != ("18432", "96", "0"):
        raise ValueError("Incomplete independent reference test")
    if not irq or tuple(map(int, irq.groups())) != (64, 2048, 0):
        raise ValueError("Incomplete IRQ/readback test")
    return dict(mixer_sha256=sha(source / "audio_mixer.v"),
                inherited_checks=177, irq_cases=64, position_reads=2048,
                reference=dict(zip(keys, match.groups())),
                inherited_sha256=sha(out / "inherited.log"))

def capacity(out, variant):
    def rows(name):
        return list(csv.DictReader(io.StringIO((out / name / "capacity.csv").read_text())))
    before, after = rows("baseline"), rows(variant)
    if len(before) != 343 or len(after) != 343:
        raise ValueError("Incomplete capacity matrix")
    better = equal = worse = stream_equal = 0
    for b, c in zip(before, after):
        for k in ("mode", "voices", "read_delay_cycles", "requests_per_output", "beats_per_output"):
            if b[k] != c[k]:
                raise ValueError("Capacity shape or SDRAM traffic changed")
        delta = float(c["mean_cycles"]) - float(b["mean_cycles"])
        better += delta < 0
        equal += delta == 0
        worse += delta > 0
        if int(c["max_cycles"]) > int(b["max_cycles"]):
            raise ValueError("Worst-case mixer capacity regression")
        if "stream" in b["mode"] and b["voices"] == "1":
            if b != c:
                raise ValueError("Stream-only schedule changed")
            stream_equal += 1
    if worse or stream_equal != 14:
        raise ValueError("Mixer capacity or stream-only regression")
    return dict(rows=343, improved=better, unchanged=equal, worse=worse,
                stream_only_rows_identical=stream_equal)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    verify_fixtures()
    out = new_output(a.output)
    result = {"variants": {}, "capacity": {}}
    for variant in ("baseline", "ram", "bypass", "combined"):
        print("Mixer " + variant, flush=True)
        result["variants"][variant] = run_variant(out / variant, variant)
    base = result["variants"]["baseline"]
    for variant in ("ram", "bypass", "combined"):
        row = result["variants"][variant]
        for key in ("samples", "scenarios", "errors", "saturation_samples", "stream_holds", "hash"):
            if row["reference"][key] != base["reference"][key]:
                raise ValueError("Reference mismatch: " + variant + "/" + key)
        result["capacity"][variant] = capacity(out, variant)
    if result["variants"]["ram"] != base:
        # Only the source hash may differ for an attribute-only change.
        for key in base.keys() - {"mixer_sha256"}:
            if result["variants"]["ram"][key] != base[key]:
                raise ValueError("Attribute-only behavioral result differs")
    if (out / "ram/capacity.csv").read_bytes() != (out / "baseline/capacity.csv").read_bytes():
        raise ValueError("Attribute-only capacity changed")
    if (out / "combined/capacity.csv").read_bytes() != (out / "bypass/capacity.csv").read_bytes():
        raise ValueError("Combined RAM/bypass capacity changed")
    for key in ("reference", "inherited_sha256"):
        if result["variants"]["combined"][key] != result["variants"]["bypass"][key]:
            raise ValueError("Combined source changed bypass behavior: " + key)
    result["passed"] = True
    result["limits"] = ["Synthetic component tests, not physical memory primitive equivalence or game FPS."]
    save(out / "result.json", result)
    print("PASS: four full32 variants, inherited/reference/MMIO races, stream schedule and capacity")

if __name__ == "__main__":
    main()
