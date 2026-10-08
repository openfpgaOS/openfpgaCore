#!/usr/bin/env python3
"""Read-only retained-A resource/provenance audit; writes new phase10 evidence."""
from pathlib import Path
import csv
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/measurements/os30-fps25-20261005/phase10/resource-audit"
FIT = ROOT / "docs/measurements/os30-dual-20261004/fit-single128-v1"
REPORT = FIT / "jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.fit.rpt"
PROVENANCE = ROOT / "docs/measurements/os30-sm64-ab-20261005/provenance/manifest.json"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def table_rows(text):
    for line in text.splitlines():
        yield [x.strip() for x in line.split(";")][1:-1]


def main():
    OUT.mkdir(exist_ok=False)
    evidence = {}

    def bind(p):
        p = p.resolve()
        try:
            key = str(p.relative_to(ROOT))
        except ValueError:
            key = str(p)
        evidence[key] = sha(p)
        return evidence[key]

    a = json.loads(PROVENANCE.read_text())["configurations"]["A_single_geo0"]
    bind(PROVENANCE)
    frozen = json.loads((FIT / "frozen-sources.json").read_text())
    assert bind(FIT / "frozen-sources.json") == a["frozen_manifest_sha256"]
    for name, expected in frozen.items():
        assert bind(FIT / "frozen" / name) == expected, name
    assert bind(FIT / "results.json") == a["fit_result_sha256"]
    assert bind(ROOT / a["effective_qsf"]) == a["effective_qsf_sha256"]
    assert bind(ROOT / a["pocket_bitstream"]) == a["pocket_sha256"]
    assert bind(ROOT / a["raw_bitstream"]) == a["raw_sha256"]
    reverse = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))
    assert (ROOT / a["raw_bitstream"]).read_bytes().translate(reverse) == (ROOT / a["pocket_bitstream"]).read_bytes()
    bind(REPORT)
    text = REPORT.read_text(errors="replace")
    summary = text.split("; Fitter Resource Usage Summary")[1].split("; Fitter Resource Usage Summary")[0]
    (OUT / "fit-summary.txt").write_text("\n".join(summary.splitlines()[:80]) + "\n")
    entities = []
    sec = text.split("; Fitter Resource Utilization by Entity")[1].split("; Fitter RAM Summary")[0]
    for r in table_rows(sec):
        if len(r) != 17 or not r[0].startswith("|"):
            continue
        entities.append(dict(hierarchy=r[14], alms_needed=r[1], alms_placed=r[2],
                             m10ks=int(r[10]), dsps=int(r[11]), memory_bits=int(r[9])))
    save("entities.json", entities)
    rams = []
    for r in table_rows(text.split("; Fitter RAM Summary")[1]):
        if len(r) != 28 or not r[0].startswith("core_top:ic|"):
            continue
        rams.append(dict(name=r[0], kind=r[1], mode=r[2], clock=r[3],
                         depth=int(r[4]), width=int(r[5]), implementation_bits=int(r[17]),
                         listed_m10ks=int(r[18]), mlabs=int(r[19]),
                         locations=[s.strip() for s in r[21].split(",")]))
    locations = {loc for r in rams for loc in r["locations"] if loc.startswith("M10K_")}
    assert len(locations) == 306, len(locations)
    # Some logical memories share physical blocks; sum only unique locations.
    save("ram-owners.json", {"unique_m10k_locations": len(locations), "logical_memories": rams})
    assert not any("xf_v" in r["name"] or "xf_M" in r["name"] for r in rams)
    ring = []
    folder = ROOT / "docs/measurements/os30-fps25-20261005/phase9/coupled"
    for p in sorted(folder.glob("run-checks-state-f11-*/coupled.json")):
        bind(p)
        cp, pp = p.parent / "counters.csv", p.parent / "publishes.csv"
        bind(cp); bind(pp)
        counters = list(csv.DictReader(cp.open()))
        publications = list(csv.DictReader(pp.open()))
        ring.append(dict(run=p.parent.name,
                         peak_upload_words=max(int(r["max_upload"]) for r in counters),
                         peak_output_words=max(int(r["max_output"]) for r in counters),
                         publications=len(publications),
                         publications_above_2047=sum(int(r["upload_queued"]) > 2047 for r in publications),
                         read_pointer_polls=int(counters[-1]["rdptr_polls"])))
    assert len(ring) == 8
    save("ring-occupancy.json", {"scope": "Existing F11 coupled model; full run including initial frame. Publication counts are not time fractions.", "runs": ring})
    apps = []
    for label, p in [
        ("SM64_F11_normal", ROOT / "docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/app.elf"),
        ("Quake2_current_local_package", ROOT.parent / "Quake2/build/pocket/quake2/Assets/quake2/common/quake2.elf"),
    ]:
        elf = p.read_bytes()
        # ELF32 program headers, little endian; addresses are definitive.
        import struct
        assert elf[:6] == b"\x7fELF\x01\x01"
        phoff = struct.unpack_from("<I", elf, 28)[0]
        entsize, count = struct.unpack_from("<HH", elf, 42)
        loads = []
        for i in range(count):
            h = struct.unpack_from("<8I", elf, phoff + i * entsize)
            if h[0] == 1:
                loads.append(dict(vaddr=h[2], paddr=h[3], filesz=h[4], memsz=h[5]))
        assert all(r["vaddr"] >= 0x10000000 for r in loads)
        apps.append(dict(label=label, path=str(p), sha256=bind(p), loads=loads,
                         app_bram_load_present=False))
        (OUT / (label + "-readelf.txt")).write_text(subprocess.check_output(["readelf", "-l", str(p)], text=True))
    save("app-b ram.json".replace(" ", ""), apps)
    mif = (FIT / "frozen/src/fpga/targets/pocket/firmware.mif").read_text()
    explicit_words = [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s*:", mif, re.M)]
    assert max(explicit_words) == 4029
    for p in ["src/firmware/os/os.ld", "src/firmware/os/targets/pocket/boot/start.S",
              "src/firmware/os/kernel/loader.c", "src/firmware/os/targets/pocket/target_platform.h",
              "src/firmware/api/app.ld", "src/firmware/api/of_fastram.h",
              "docs/measurements/os30-feature-audit-20261003/features.csv",
              "docs/measurements/os30-feature-audit-20261003/gpu-screen.json",
              "docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/of_gpu.h"]:
        bind(ROOT / p)
    bind(ROOT.parent / "Quake2/src/quake2/openfpga/of_gl1.c")
    bind(ROOT.parent / "Quake2/src/quake2/openfpga/of_emit_q2.c")
    save("result.json", {
        "passed": True, "scope": "Read-only retained-A resource audit; no new core fit or performance qualification",
        "pocket_sha256": a["pocket_sha256"], "raw_sha256": a["raw_sha256"],
        "bit_reversal_exact": True, "frozen_files_verified": len(frozen),
        "fit": {"alms_needed": 17248, "alms_placed": 18041, "alms_total": 18480,
                "alms_recoverable_estimate": 1177, "alms_unavailable_estimate": 384,
                "labs_used": 1846, "labs_total": 1848, "logic_labs": 1829, "memory_labs": 17,
                "packing_difficulty": "High", "m10ks_used": 306, "m10ks_total": 308,
                "memory_useful_bits": 2331224, "memory_implementation_bits": 3133440,
                "dsps_used": 42, "dsps_total": 66,
                "main_fmax_mhz": a["main_fmax_mhz"], "worst_setup_slack_ns": -0.842},
        "already_pruned_coordinate_memories": True,
        "ring_current_capacity_words": 4096, "ring_capacity_available_words": 4095,
        "ring_shrink_performance_neutral_proven": False,
        "cpu_bram_m10ks": 32, "firmware_explicit_initialized_bytes": 16120,
        "app_bram_range": [0x4000, 0x7800], "boot_stack_top": 0x8000,
        "boot_linker_stack_headroom_bytes": 8192,
        "app_bram_reuse_safe_proven": False,
        "whole_core_accelerator_fit_proven": False,
        "evidence_sha256": evidence,
    })
    print(json.dumps({"passed": True, "output": str(OUT), "verified_frozen_files": len(frozen), "unique_m10k_locations": len(locations)}))


if __name__ == "__main__":
    main()
