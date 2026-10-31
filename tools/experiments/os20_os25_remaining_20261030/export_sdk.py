#!/usr/bin/env python3
"""Prepare a qualified os20/os25 SDK update; --sdk applies the reviewed plan.

This exports saved fits, never recompiles firmware/RTL, and never updates os30.
The >=94 MHz gate permits main-clock setup failure at configured 100 MHz;
all other checks, including used clock crossings, must pass at every corner.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / "docs/measurements/os20-os25-remaining-20261030"
POCKET = Path("src/fpga/targets/pocket")
RECIPE = ROOT / "tools/experiments/quake2_tiny_audio_timing_20261013/recipes"
BOOT = "e505071d970e68bb7672be27db82ff304e37d9cb3a9d04180c0f9cf61992b031"
OS = "054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7"
LOADER = "fc1f8d37eb0fb322006b4428f0b59d2801c9b74978bd3f24103f5f9bbfca3c99"
CHANGES = {"pocket/os20.rbf_r", "pocket/os25.rbf_r", "pocket/ap_core.sof", "MANIFEST"}
BIT_REVERSE = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def file_info(path):
    require(path.is_file() and not path.is_symlink(), f"Not a regular file: {path}")
    data = path.read_bytes()
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                md5=hashlib.md5(data).hexdigest())


def tree_info(path):
    require(path.is_dir() and not path.is_symlink(), f"Not a directory: {path}")
    result = {}
    for entry in sorted(path.rglob("*")):
        require(not entry.is_symlink(), f"Symlinks are not accepted: {entry}")
        if entry.is_file():
            result[entry.relative_to(path).as_posix()] = file_info(entry)
    return result


def paired_runtime(runtime):
    require(sha(runtime / "pocket/os.bin") == OS, "Shared Pocket OS changed")
    require(sha(runtime / "pocket/loader.bin") == LOADER, "Pocket loader changed")
    data = (runtime / "pocket/os.bin").read_bytes()
    require(data[-32:-28] == b"OABI" and data[-24:-20] == b"OSE2"
            and data[-8:-4] == b"OFC1", "OS trailer layout changed")
    require(struct.unpack_from("<I", data, len(data) - 28)[0] == 0x4F2EE14D,
            "OS boot ABI mismatch")
    require(struct.unpack_from("<III", data, len(data) - 20)
            == (0x1032C9C0, 0x10341A50, 0x1038E2C0), "OS entry/BSS mismatch")
    require(zlib.crc32(data[:-8]) == struct.unpack("<I", data[-4:])[0], "OS CRC mismatch")


def verify_manifest(runtime):
    seen = set()
    inventory = tree_info(runtime)
    for line in (runtime / "MANIFEST").read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-f]{32})\s+\./(.+)", line)
        require(match is not None, "Invalid MANIFEST line: " + line)
        checksum, name = match.groups()
        require(name in inventory and name != "MANIFEST" and name not in seen,
                "Missing/duplicate MANIFEST member: " + name)
        require(inventory[name]["md5"] == checksum, "MANIFEST mismatch: " + name)
        seen.add(name)
    require(seen == set(inventory) - {"MANIFEST"}, "MANIFEST does not cover runtime exactly")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cpu_body(data):
    # Private generation changes these two provenance comments, not HDL.
    return re.sub(rb"^// (?:Generator|Git hash).*\n", b"", data, flags=re.M)


def qualify(fit_root, variant, seed):
    """Reparse saved physical proof and pin every input/report we consume."""
    fit_root = fit_root.resolve()
    require((fit_root / "complete.json").is_file(), f"Incomplete fit: {fit_root}")
    result_path = fit_root / f"results/pocket-{variant}-s{seed}/result.json"
    fit = read_json(result_path)
    require(fit.get("target") == "pocket" and fit.get("variant") == variant
            and fit.get("seed") == seed and fit.get("state") == "complete"
            and fit.get("valid") is True, "Fit identity/status mismatch")
    require(all(fit["returncodes"].get(stage) == 0 for stage in ("map", "fit", "asm", "sta")),
            "Compilation stage failed")
    require(fit["returncodes"].get("corners") in (0, 3), "Corner analysis failed unexpectedly")
    job = fit_root / f"jobs/pocket-{variant}-s{seed}"
    project = job / POCKET
    require(Path(fit["directory"]).resolve() == project, "Fit directory mismatch")
    frozen = fit_root / "frozen"
    sources = read_json(fit_root / "frozen-sources.json")
    rows = read_json(fit_root / "inventory.json")
    require(len(rows) == 1 and rows[0]["target"] == "pocket"
            and rows[0]["variant"] == variant, "Ambiguous frozen variant")
    row = rows[0]
    config = read_json(fit_root / "config.json")
    require(seed in config["seeds"], "Selected seed was not in the frozen campaign")
    watched = {}

    def watch(path):
        require(path.is_file() and not path.is_symlink(), f"Missing regular input: {path}")
        watched[str(path.resolve())] = sha(path)

    for name, checksum in sources.items():
        relative = Path(name)
        require(not relative.is_absolute() and ".." not in relative.parts, "Unsafe source path")
        require(sha(frozen / name) == checksum, "Frozen input changed: " + name)
        watch(frozen / name)
        if relative != POCKET / "ap_core.qsf":
            require(sha(job / name) == checksum, "Fitted input changed: " + name)
        watch(job / name)
    # Reconstruct the runner's only intentional change to its copied inputs.
    sys.path.insert(0, str(ROOT / "tools"))
    from sweep_target_matrix import pocket_source_overrides
    qsf = (frozen / POCKET / "ap_core.qsf").read_text()
    extra = frozen / POCKET / f"variants/{variant}.qsf"
    if extra.is_file():
        qsf += "\n" + extra.read_text()
    qsf = pocket_source_overrides(qsf, frozen / POCKET / f"variants/{variant}.sources")
    qsf = re.sub(r"^set_global_assignment -name (SEED|NUM_PARALLEL_PROCESSORS) .*\n",
                 "", qsf, flags=re.M)
    qsf += f"\nset_global_assignment -name SEED {seed}\n"
    qsf += f"set_global_assignment -name NUM_PARALLEL_PROCESSORS {config['processors']}\n"
    for macro in row["defs"].split():
        qsf += "set_global_assignment -name VERILOG_MACRO " + macro + "\n"
    qsf += ("set_global_assignment -name VERILOG_FILE ../../vendor/vexriscv/"
            f"VexiiRiscv/VexiiRiscv_{variant}.v\n")
    # Quartus may save the project after deleting overridden singleton defaults.
    # Permit only the three physical settings overridden by variants/os20.qsf;
    # repeated file/macro assignments and every effective setting remain exact.
    singleton = ("PHYSICAL_SYNTHESIS_COMBO_LOGIC", "PHYSICAL_SYNTHESIS_REGISTER_RETIMING",
                 "PHYSICAL_SYNTHESIS_EFFORT")
    cleaned = qsf
    for setting in singleton:
        pattern = re.compile(r"^set_global_assignment -name " + setting + r" .*\n", re.M)
        matches = list(pattern.finditer(cleaned))
        for match in reversed(matches[:-1]):
            cleaned = cleaned[:match.start()] + cleaned[match.end():]
    require((project / "ap_core.qsf").read_text() in (qsf, cleaned), "Fitted QSF changed")
    boot = project / "firmware.mif"
    require(sha(boot) == row["boot_sha256"] == BOOT, "Fitted boot ROM is not the paired e505 image")
    require(sha(ROOT / POCKET / "firmware.mif") == BOOT, "Canonical boot ROM changed")
    watch(ROOT / POCKET / "firmware.mif")
    cpu_relative = Path(f"src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_{variant}.v")
    fitted_cpu, canonical_cpu = job / cpu_relative, ROOT / cpu_relative
    require(sha(fitted_cpu) == row["cpu_sha256"], "Fitted CPU provenance mismatch")
    require(cpu_body(fitted_cpu.read_bytes()) == cpu_body(canonical_cpu.read_bytes()),
            "Canonical CPU differs beyond Generator/Git hash comments")
    watch(canonical_cpu)
    timing = fit_root / f"qualification/seed-{seed}"
    qualifier = fit_root / "qualification/results.py"
    for name in ("results.py", "report_sweep_timing.tcl"):
        require(sha(fit_root / "qualification" / name) == sha(RECIPE / name),
                "Strict timing recipe changed: " + name)
        watch(RECIPE / name)
    strict = load_module("sdk_export_strict", RECIPE / "results.py")
    parsed = strict.parse(project, timing)
    saved = [r for r in read_json(fit_root / "qualification/results.json")
             if r.get("variant") == variant and r.get("seed") == seed]
    require(len(saved) == 1 and saved[0].get("fit_valid") is True
            and saved[0].get("valid_reports") is True, "Missing strict qualification")
    require(all(saved[0].get(key) == value for key, value in parsed.items()),
            "Saved qualification differs from fresh report parsing")
    rc = saved[0].get("sta_returncode")
    require(rc in (0, 3), "Strict STA did not finish")
    require(rc != 3 or "Whole-design timing failed" in (timing / "driver.log").read_text(),
            "Strict STA failed before its verdict")
    for key in ("fit_successful", "nonsetup_clean", "nonmain_setup_clean",
                "cross_clock_setup_clean", "input_setup_clean", "eligible_above_90_mhz_estimate"):
        require(parsed[key] is True, "Rejected timing/resource gate: " + key)
    require(parsed["minimum_main_restricted_fmax_mhz"] >= 94.0, "Worst-corner Fmax is below 94 MHz")
    artifacts = {}
    for suffix in ("rbf", "sof"):
        path = project / f"output_files/ap_core.{suffix}"
        require(sha(path) == fit["hashes"][suffix], "Fitted artifact changed: " + suffix)
        artifacts[suffix] = dict(path=str(path), **file_info(path))
        watch(path)
    for path in [fit_root / n for n in ("complete.json", "config.json", "inventory.json",
                                       "frozen-sources.json", "qualification/results.json")]:
        watch(path)
    for path in (result_path, qualifier, fit_root / "qualification/report_sweep_timing.tcl",
                 project / "output_files/ap_core.fit.rpt", project / "output_files/ap_core.fit.summary",
                 ROOT / "tools/sweep_target_matrix.py"):
        watch(path)
    for path in timing.iterdir():
        if path.is_file():
            watch(path)
    proof = {key: parsed[key] for key in (
        "resources", "main_restricted_fmax_mhz", "minimum_main_restricted_fmax_mhz",
        "worst_slack_ns", "worst_main_setup_ns", "worst_nonmain_setup_ns",
        "worst_used_cross_clock_setup_ns", "worst_used_input_setup_ns",
        "nonsetup_clean", "nonmain_setup_clean", "cross_clock_setup_clean", "input_setup_clean",
        "timing_closed_at_configured_100_mhz")}
    proof.update(variant=variant, seed=seed, fit_root=str(fit_root),
                 actual_fitted_cpu_sha256=sha(fitted_cpu), canonical_cpu_sha256=sha(canonical_cpu),
                 cpu_body_sha256=hashlib.sha256(cpu_body(fitted_cpu.read_bytes())).hexdigest(),
                 canonical_cpu_allowance="Only Generator/Git hash header comments",
                 boot_sha256=BOOT, defines=row["defs"], artifacts=artifacts,
                 fitted_input_hashes={name: sha(job / name) for name in sources})
    return proof, watched


def updated_manifest(runtime, variants):
    text = (runtime / "MANIFEST").read_text()
    for name in sorted(CHANGES - {"MANIFEST"}):
        text, count = re.subn(r"^[0-9a-f]{32}(\s+\./" + re.escape(name) + r")$",
                             file_info(runtime / name)["md5"] + r"\1", text, flags=re.M)
        require(count == 1, "Missing unique manifest entry: " + name)
    notes = ["# Scoped Pocket FPGA refresh 2026-10-30; other entries retain their prior provenance."]
    for variant, info in variants.items():
        notes.append(f"# {variant}: seed {info['seed']}, worst restricted Fmax"
                     f" {info['minimum_main_restricted_fmax_mhz']:.2f} MHz; fitted CPU"
                     f" {info['actual_fitted_cpu_sha256']}")
    notes += ["# Configured 100 MHz; this is not 100 MHz timing closure.",
              "# ap_core.sof is the selected os25 SOF. Shared OS/loader unchanged."]
    return "\n".join(notes) + "\n" + text


def prepare(args):
    require(all(getattr(args, name) is not None for name in
                ("os20_fit", "os20_seed", "os25_fit", "os25_seed")),
            "Prepare requires explicit --os20-fit/--os20-seed/--os25-fit/--os25-seed")
    out = args.output.resolve()
    require(not out.exists(), "Output already exists; review it, then use --sdk to apply")
    reference = args.sdk_reference.resolve()
    require(not out.is_relative_to(reference) and not reference.is_relative_to(out),
            "Output must be separate from the SDK reference")
    before = read_json(args.sdk_inventory)["files"]
    require(tree_info(reference / "runtime") == before, "SDK changed since the prior inventory")
    paired_runtime(reference / "runtime")
    verify_manifest(reference / "runtime")
    pairing_path = CAMPAIGN / "integration/sdk-runtime-pair-audit.json"
    pairing = read_json(pairing_path)
    require(pairing["mif"]["sha256"] == BOOT and pairing["sdk_os"]["sha256"] == OS,
            "Boot/OS pairing receipt does not describe this package")
    require(all(pairing["extracted_boot"][key] is True for key in
                ("matches_boot_bin", "matches_fitted_mif_prefix", "remaining_mif_all_nops"))
            and all(pairing["sdk_os"][key] is True for key in
                ("payload_exactly_matches_same_elf", "entry_bss_match_elf", "crc_ok")),
            "Boot/OS same-link pairing proof is incomplete")
    variants, watched = {}, {}
    for variant in ("os20", "os25"):
        proof, inputs = qualify(getattr(args, variant + "_fit"), variant, getattr(args, variant + "_seed"))
        require(not out.is_relative_to(Path(proof["fit_root"])), "Output must be outside the fit")
        variants[variant] = proof
        watched.update(inputs)
    watched[str(args.sdk_inventory.resolve())] = sha(args.sdk_inventory)
    watched[str(pairing_path)] = sha(pairing_path)
    watched[str(Path(__file__).resolve())] = sha(Path(__file__))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=out.name + ".prepare-", dir=out.parent) as temporary:
        stage = Path(temporary)
        shutil.copytree(reference / "runtime", stage / "runtime")
        for variant, proof in variants.items():
            destination = stage / "artifacts" / variant
            destination.mkdir(parents=True)
            for suffix, info in proof["artifacts"].items():
                shutil.copy2(info["path"], destination / f"ap_core.{suffix}")
            data = (destination / "ap_core.rbf").read_bytes()
            (stage / "runtime/pocket" / f"{variant}.rbf_r").write_bytes(data.translate(BIT_REVERSE))
        shutil.copy2(stage / "artifacts/os25/ap_core.sof", stage / "runtime/pocket/ap_core.sof")
        (stage / "runtime/MANIFEST").write_text(updated_manifest(stage / "runtime", variants))
        after = tree_info(stage / "runtime")
        require({n for n in before if before[n] != after[n]} <= CHANGES,
                "Unexpected proposed runtime change")
        verify_manifest(stage / "runtime")
        paired_runtime(stage / "runtime")
        plan = dict(schema=1, sdk_reference=str(reference), variants=variants,
                    allowed_changes=sorted(CHANGES), before=before, after=after,
                    watched_sources=watched, package_artifacts=tree_info(stage / "artifacts"),
                    shared_os_sha256=OS, loader_sha256=LOADER, boot_rom_sha256=BOOT,
                    boot_os_same_link_proof=pairing,
                    minimum_required_fmax_mhz=94.0, configured_mhz=100,
                    qualification="Strict nonsetup/nonmain/used crossings; >=94 MHz estimate, not 100 MHz closure")
        write_json(stage / "plan.json", plan)
        (stage / "plan.sha256").write_text(sha(stage / "plan.json") + "  plan.json\n")
        # An incomplete prepared package is never installed at the requested output.
        require(tree_info(reference / "runtime") == before, "SDK changed during preparation")
        for name, checksum in watched.items():
            require(sha(Path(name)) == checksum, "Source changed during preparation: " + name)
        os.rename(stage, out)
    print(f"Prepared {out}; no SDK writes. Review plan.json and runtime/MANIFEST.")


def replace_file(source, destination):
    with tempfile.NamedTemporaryFile(prefix=".os20-os25-", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
            shutil.copymode(destination, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def apply(args):
    out = args.output.resolve()
    sdk = args.sdk.resolve()
    runtime = sdk / "runtime"
    require(not out.is_relative_to(sdk) and not sdk.is_relative_to(out),
            "Prepared package and SDK must be separate")
    require(sha(out / "plan.json") == (out / "plan.sha256").read_text().split()[0],
            "Prepared plan changed")
    plan = read_json(out / "plan.json")
    require(plan.get("schema") == 1 and set(plan["allowed_changes"]) == CHANGES,
            "Unexpected update scope")
    require(tree_info(runtime) == plan["before"], "SDK changed since preparation; nothing written")
    require(tree_info(out / "runtime") == plan["after"], "Prepared runtime changed")
    require(tree_info(out / "artifacts") == plan["package_artifacts"], "Prepared raw artifacts changed")
    for name, checksum in plan["watched_sources"].items():
        require(sha(Path(name)) == checksum, "Fitted source/report changed: " + name)
    for variant in ("os20", "os25"):
        prior = plan["variants"][variant]
        fresh, _ = qualify(Path(prior["fit_root"]), variant, prior["seed"])
        require(fresh == prior, "Qualification changed after preparation")
        raw = out / "artifacts" / variant / "ap_core.rbf"
        require((out / "runtime/pocket" / f"{variant}.rbf_r").read_bytes()
                == raw.read_bytes().translate(BIT_REVERSE), "RBF bit reversal mismatch")
    require(sha(out / "runtime/pocket/ap_core.sof") == sha(out / "artifacts/os25/ap_core.sof"),
            "Default SOF is not the selected os25 fit")
    require(set(plan["before"]) == set(plan["after"])
            and {n for n in plan["before"] if plan["before"][n] != plan["after"][n]} <= CHANGES,
            "Plan changes an unrelated runtime file")
    paired_runtime(runtime)
    paired_runtime(out / "runtime")
    verify_manifest(runtime)
    verify_manifest(out / "runtime")
    backup = out / "backup"
    require(not backup.exists(), "Backup already exists; inspect the previous application")
    backup.mkdir()
    shutil.copytree(runtime, backup / "runtime")
    require(tree_info(backup / "runtime") == plan["before"], "Backup validation failed")
    write_json(backup / "receipt.json", dict(sdk=str(sdk), before=plan["before"],
                                             plan_sha256=sha(out / "plan.json")))
    progress = dict(plan["before"])
    replaced = []
    try:
        # MANIFEST last: interrupted or mixed packages fail the SDK assembly guard.
        for name in sorted(CHANGES - {"MANIFEST"}) + ["MANIFEST"]:
            require(tree_info(runtime) == progress, "SDK changed during application")
            replace_file(out / "runtime" / name, runtime / name)
            replaced.append(name)
            progress[name] = plan["after"][name]
        require(tree_info(runtime) == plan["after"], "Installed runtime verification failed")
        verify_manifest(runtime)
    except BaseException:
        for name in reversed(replaced):
            # Do not overwrite a concurrent writer's replacement during rollback.
            if file_info(runtime / name) == plan["after"][name]:
                replace_file(backup / "runtime" / name, runtime / name)
        write_json(out / "failed-application.json", dict(sdk=str(sdk),
                   backup=str(backup), runtime=tree_info(runtime)))
        raise
    write_json(out / "applied.json", dict(sdk=str(sdk), changed=sorted(CHANGES),
               backup=str(backup), plan_sha256=sha(out / "plan.json"), after=tree_info(runtime),
               unrelated_runtime_unchanged=True, deployed_to_device=False))
    print(f"Updated {sdk}: os20.rbf_r, os25.rbf_r, os25 ap_core.sof, MANIFEST. Backup: {backup}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New prepared directory, or reviewed package for --sdk")
    parser.add_argument("--os20-fit", type=Path)
    parser.add_argument("--os20-seed", type=int)
    parser.add_argument("--os25-fit", type=Path)
    parser.add_argument("--os25-seed", type=int)
    parser.add_argument("--sdk-reference", type=Path, default=ROOT.parent / "openfpgaSDK",
                        help="Read-only SDK used for preparation")
    parser.add_argument("--sdk-inventory", type=Path, default=CAMPAIGN / "integration/sdk-runtime-before.json")
    parser.add_argument("--sdk", type=Path, help="Apply an existing prepared package to this SDK; writes only four files")
    args = parser.parse_args()
    try:
        apply(args) if args.sdk else prepare(args)
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, "Refused: " + str(error) + "\n")


if __name__ == "__main__":
    main()
