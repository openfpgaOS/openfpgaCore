#!/usr/bin/env python3
"""Check generic mixer scope and source-only Pocket simulator adaptation."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
from common import FIXTURES, mixer_variant, new_output, once, save, verify_fixtures

def preprocess(path, macros):
    output = subprocess.check_output(["verilator", "-E", "-P",
                 *["-D" + name for name in macros], str(path)], text=True)
    return "\n".join(line.strip() for line in output.splitlines() if line.strip())

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--prepared-mixer", type=Path,
                   help="Optional actual scoped generic mixer from a frozen physical candidate")
    a = p.parse_args()
    pins = verify_fixtures()
    out = new_output(a.output)
    baseline = out / "baseline.v"
    baseline.write_text(mixer_variant("baseline"))
    direct = out / "direct.v"
    direct.write_text(mixer_variant("ram"))
    bypass = out / "bypass.v"
    bypass.write_text(mixer_variant("bypass"))
    old = '(* ramstyle = "MLAB" *) reg [21:0] pos_latch [0:31];'
    scoped = out / "scoped.v"
    scoped.write_text(once(baseline.read_text(), old,
        "\x60ifdef INCLUDE_AUDIO_POS_M10K\n" + old.replace("MLAB", "M10K") +
        "\n\x60else\n" + old + "\n\x60endif"))
    if a.prepared_mixer:
        scoped.write_bytes(a.prepared_mixer.read_bytes())
    rows = []
    for lite in (False, True):
        for enable in (False, True):
            macros = (["MIXER_LITE"] if lite else []) + (
                ["INCLUDE_AUDIO_POS_M10K"] if enable else [])
            expected = preprocess(direct if enable and not lite else baseline,
                                  ["MIXER_LITE"] if lite else [])
            actual = preprocess(scoped, macros)
            if actual != expected:
                raise ValueError("Scoped mixer mismatch: " + repr(macros))
            rows.append(dict(lite=lite, enable=enable, identical=True,
                        preprocessed_sha256=hashlib.sha256(actual.encode()).hexdigest()))
    if preprocess(baseline, ["INCLUDE_NONSTREAM_BYPASS"]) != preprocess(bypass, []):
        raise ValueError("Compiler bypass macro differs from tested prefixed source")
    io = (FIXTURES / "io_sdram.v").read_text()
    io = once(io, "inout   wire    [15:0]  phy_dq,",
              "input wire [15:0] phy_dq_in,\noutput wire [15:0] phy_dq_out_port,\noutput wire phy_dq_oe_port,")
    io = once(io, "assign          phy_dq = phy_dq_oe ? phy_dq_out : 16'bZZZZZZZZZZZZZZZZ;",
              "assign phy_dq_out_port = phy_dq_out;\nassign phy_dq_oe_port = phy_dq_oe;")
    io = re.sub(r"\bphy_dq\b", "phy_dq_in", io)
    if io != (FIXTURES / "io_sdram_pocket_test.v").read_text():
        raise ValueError("Pocket controller twin has unexpected behavior edits")
    result = dict(passed=True, fixture_count=len(pins), configurations=rows,
                  bypass_define_identical=True, pocket_split_dq_only=True,
                  prepared_mixer=str(a.prepared_mixer) if a.prepared_mixer else None,
                  limits=["Preprocessor/source identity, not M10K primitive equivalence.",
                          "Run check_mapping.py on the actual candidate map report."])
    save(out / "result.json", result)
    print("PASS: four mixer scope combinations, bypass macro, Pocket split-DQ source identity")

if __name__ == "__main__":
    main()
