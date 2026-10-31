#!/usr/bin/env python3
"""Rebuild and compare all 18 fixed-work Pocket memory/audio cases.

A passing comparison is a bounded NONREGRESSION verdict. Both 32-voice saturated
cases already underrun; their failure is retained explicitly in the receipt.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from common import build, invoke, mixer_variant, new_output, save, sha, stage, verify_fixtures

NAMES = ["tb_pocket_audio.v", "io_sdram_pocket_test.v", "sdram_model_full.v",
         "axi_sdram_arbiter.v", "sync_fifo.v", "axi_sdram_slave.v", "synch_3.v",
         "pocket_memory_contention.cpp"]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--candidate", choices=["bypass", "combined"], default="bypass")
    p.add_argument("--jobs", type=int, choices=range(1, 9), default=2)
    a = p.parse_args()
    verify_fixtures()
    out = new_output(a.output)
    exes = {}
    for variant in ("baseline", a.candidate):
        job = out / variant
        job.mkdir()
        sources = stage(job / "sources", NAMES, mixer_variant(variant))
        exes[variant] = build(job, sources, "tb_sdram_cont", NAMES + ["audio_mixer.v"],
            ["--public-flat-rw", "-GBANK_ROW_TRACK=0", "-GREFRESH_CYCLES=736"])
        print("Built " + variant, flush=True)

    def run(case):
        variant, voices, mode = case
        name = f"{variant}-voices{voices}-{mode}"
        raw, log = out / (name + ".raw"), out / (name + ".log")
        run = invoke([str(exes[variant]), str(raw), str(voices), "100", mode], log, check=False)
        text = log.read_text()
        stats = {}
        for line in text.splitlines():
            if line.startswith(("PASS ", "SERVICE ")):
                stats.update({k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)})
        if "underruns" not in stats or not raw.exists():
            raise ValueError("Incomplete run " + name + ": " + text[-1000:])
        if "FAIL " in text or run.returncode != int(stats["underruns"] != 0):
            raise ValueError("Unexpected simulator failure: " + name)
        pcm = raw.read_bytes()
        if len(pcm) != 8192 * 4 or len(set(pcm[i:i+4] for i in range(0, len(pcm), 4))) < 100:
            raise ValueError("PCM missing or degenerate: " + name)
        result = dict(variant=variant, voices=voices, mode=mode, returncode=run.returncode,
                      stats=stats, pcm_sha256=sha(raw), zero_underruns=stats["underruns"] == 0)
        print(name + ": underruns=" + str(stats["underruns"]), flush=True)
        return result

    cases = [(variant, voices, mode) for voices in (9, 20, 32)
             for mode in ("quiet", "cpu", "stress") for variant in ("baseline", a.candidate)]
    result = dict(candidate=a.candidate, mhz=100, bank_row_track=0, runs=[])
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for row in pool.map(run, cases):
            result["runs"].append(row)
            save(out / "progress.json", result)
    result["pairs"] = []
    for voices in (9, 20, 32):
        def get(variant, mode):
            return next(r for r in result["runs"] if r["variant"] == variant
                        and r["voices"] == voices and r["mode"] == mode)
        ref = get("baseline", "quiet")["pcm_sha256"]
        for mode in ("quiet", "cpu", "stress"):
            b, c = get("baseline", mode), get(a.candidate, mode)
            pair = dict(voices=voices, mode=mode,
                        pcm_exact=b["pcm_sha256"] == c["pcm_sha256"] == ref,
                        protocol_clean=b["stats"]["protocol_errors"] == c["stats"]["protocol_errors"] == 0,
                        baseline_underruns=b["stats"]["underruns"],
                        candidate_underruns=c["stats"]["underruns"])
            pair["underruns_not_increased"] = pair["candidate_underruns"] <= pair["baseline_underruns"]
            pair["normal_service_pass"] = (voices == 32 and mode != "quiet") or (
                pair["baseline_underruns"] == pair["candidate_underruns"] == 0)
            pair["completion"] = {}
            for key in ("cycles", "cpu_cycles", "gpu_cycles"):
                old, new = b["stats"][key], c["stats"][key]
                pair["completion"][key] = dict(baseline=old, candidate=new,
                    delta=new-old, percent_change=(100*(new-old)/old if old else 0))
            result["pairs"].append(pair)
    result["comparison_passed"] = all(all(row[key] for key in (
        "pcm_exact", "protocol_clean", "underruns_not_increased", "normal_service_pass"))
        for row in result["pairs"])
    result["all_cases_zero_underruns"] = all(r["zero_underruns"] for r in result["runs"])
    result["limits"] = [
        "All 18 simulations are rebuilt/run here; no reused historical outputs.",
        "CPU-only uses 320x240x60 scanout and 100000 16-word read/write jobs.",
        "Mixed stress uses 320x200x60 scanout, 100000 CPU 16-word and GPU 8-word jobs.",
        "48 kHz consumer begins after 256-sample prefill; Pocket pin/CDC timing is outside this model.",
        "32-voice saturated overload is a known baseline failure, not accepted zero-underrun service.",
        "Completion deltas are reported, not asserted identical: arbitration phases can shift.",
        "Mixer source change only. Real M10K inference, timing and gameplay require separate qualification."]
    save(out / "result.json", result)
    if not result["comparison_passed"]:
        raise SystemExit("FAIL: PCM, protocol or audio service regression")
    print("PASS: bounded comparison; all cases zero underruns=" +
          str(result["all_cases_zero_underruns"]))

if __name__ == "__main__":
    main()
