#!/usr/bin/env python3
"""Check actual Pocket variant selection keeps the os30 mixer shadow intact."""
import argparse
import json
from pathlib import Path
import re
from common import HERE, new_output, save, sha

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, required=True,
                   help="Repository or isolated frozen root containing src/fpga")
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    root = a.source_root.resolve()
    pocket = root / "src/fpga/targets/pocket"
    qsf = (pocket / "ap_core.qsf").read_text()
    declarations = re.findall(r"^set_global_assignment -name (?:SYSTEMVERILOG_FILE|VERILOG_FILE) (.*audio_mixer\.v)$", qsf, re.M)
    if declarations != ["../../common/audio_mixer.v"]:
        raise ValueError("Unexpected base mixer selection: " + repr(declarations))
    pins = json.loads((HERE / "selection-pins.json").read_text())
    if sha(root / pins["os30_audio_mixer"]["path"]) != pins["os30_audio_mixer"]["sha256"]:
        raise ValueError("Retained os30 mixer shadow changed")
    result = {"variants": {}, "os30_shadow_unchanged": True}
    for variant in ("os20", "os25", "os30"):
        mapping = pocket / "variants" / (variant + ".sources")
        choices = {}
        if mapping.exists():
            for line in mapping.read_text().splitlines():
                fields = line.split()
                if not fields or fields[0].startswith("#"):
                    continue
                if len(fields) != 2 or fields[0] in choices:
                    raise ValueError("Invalid/duplicate source override: " + line)
                choices[fields[0]] = fields[1]
        selected = choices.get(declarations[0], declarations[0])
        expected = "../../common/" + ("os30/" if variant == "os30" else "") + "audio_mixer.v"
        if selected != expected:
            raise ValueError(variant + " selected unexpected mixer " + selected)
        source = (pocket / selected).resolve()
        result["variants"][variant] = dict(source=str(source.relative_to(root)), sha256=sha(source))
    out = new_output(a.output)
    result["passed"] = True
    result["limits"] = ["Source-selection check; actual elaboration/map/timing must be checked separately."]
    save(out / "result.json", result)
    print("PASS: generic os20/os25 selection and retained os30 shadow")

if __name__ == "__main__":
    main()
