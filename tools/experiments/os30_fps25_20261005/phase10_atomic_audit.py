#!/usr/bin/env python3
"""Audit A-extension instructions in exact local game and retained OS ELFs."""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs/measurements/os30-fps25-20261005/phase10/resource-audit/atomics"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def functions(elf):
    shoff = struct.unpack_from("<I", elf, 32)[0]
    stride, count = struct.unpack_from("<HH", elf, 46)
    sections = [struct.unpack_from("<10I", elf, shoff + stride*i) for i in range(count)]
    result = []
    for s in sections:
        if s[1] != 2:  # SHT_SYMTAB
            continue
        strings = sections[s[6]]
        strings = elf[strings[4]:strings[4]+strings[5]]
        for p in range(s[4], s[4]+s[5], s[9]):
            name, address, size, info, other, section = struct.unpack_from("<IIIBBH", elf, p)
            if info & 15 != 2 or not section:
                continue
            name = strings[name:].split(b"\0", 1)[0].decode()
            result.append((address, address+size, name))
    return result


def main():
    OUT.mkdir(exist_ok=False)
    files = {
        "SM64_F11_normal": ROOT / "docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/app.elf",
        "SM64_F11_diagnostic": ROOT / "docs/measurements/os30-fps25-20261005/phase9/checks-state-f11/diagnostic.elf",
        "Quake2_local_package": ROOT.parent / "Quake2/build/pocket/quake2/Assets/quake2/common/quake2.elf",
        "OS_retained": ROOT / "src/firmware/os/bld/pocket/firmware.elf",
    }
    # Existing helper uses the following diagnostic basename.
    if not files["SM64_F11_diagnostic"].exists():
        files["SM64_F11_diagnostic"] = files["SM64_F11_normal"].with_name("app-perfdiag.elf")
    evidence = {}
    rows = []
    for label, p in files.items():
        evidence[str(p)] = sha(p)
        fs = functions(p.read_bytes())
        asm = subprocess.check_output(["llvm-objdump", "-d", "--mattr=+a,+f,+c,+m", str(p)], text=True)
        atomics = []
        for line in asm.splitlines():
            m = re.match(r"\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+(.+)", line)
            if not m:
                continue
            address, opcode = int(m[1], 16), int(m[2], 16)
            if opcode & 127 != 0x2f:
                continue
            owners = sorted({name for start, end, name in fs if start <= address < end})
            # Data literals may share executable sections; only function code counts.
            if not owners:
                continue
            mnemonic = m[3].split()[0]
            assert mnemonic.startswith(("lr.w", "sc.w", "amo")), (line, owners)
            atomics.append(dict(address=hex(address), opcode=hex(opcode), owners=owners, disassembly=m[3]))
        names = sorted({name for a in atomics for name in a["owners"]})
        subset = subprocess.check_output(["llvm-objdump", "-d", "--mattr=+a,+f,+c,+m", "--disassemble-symbols="+",".join(names), str(p)], text=True)
        (OUT / (label+"-atomic-functions.asm")).write_text(subset)
        attrs = subprocess.check_output(["readelf", "-A", str(p)], text=True)
        (OUT / (label+"-attributes.txt")).write_text(attrs)
        rows.append(dict(label=label, elf=str(p), sha256=sha(p), atomics=atomics,
                         lr_count=sum(a["disassembly"].startswith("lr.w") for a in atomics),
                         sc_count=sum(a["disassembly"].startswith("sc.w") for a in atomics),
                         amo_count=sum(a["disassembly"].startswith("amo") for a in atomics)))
    osbin = ROOT / "src/firmware/os/bld/pocket/os.bin"
    for p in (osbin, ROOT.parent/"SM64/runtime/pocket/os.bin", ROOT.parent/"Quake2/runtime/pocket/os.bin"):
        assert sha(p) == "054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7"
        evidence[str(p)] = sha(p)
    payload = OUT / "os-payload.bin"
    subprocess.run(["llvm-objcopy", "-O", "binary", "--only-section=.text", "--only-section=.osdata", str(files["OS_retained"]), str(payload)], check=True)
    assert osbin.read_bytes().startswith(payload.read_bytes())
    fwmif = ROOT / "src/firmware/os/bld/pocket/firmware.mif"
    frozenmif = ROOT / "docs/measurements/os30-dual-20261004/fit-single128-v1/frozen/src/fpga/targets/pocket/firmware.mif"
    assert sha(fwmif) == sha(frozenmif) == "e505071d970e68bb7672be27db82ff304e37d9cb3a9d04180c0f9cf61992b031"
    evidence[str(fwmif)] = sha(fwmif)
    evidence[str(frozenmif)] = sha(frozenmif)
    for name in ["src/fpga/vendor/vexriscv/generate_vexii.sh", "src/firmware/os/Makefile",
                 "src/fpga/vendor/vexriscv/VexiiRiscv/src/main/scala/vexiiriscv/Param.scala",
                 "src/fpga/vendor/vexriscv/VexiiRiscv/src/main/scala/vexiiriscv/execute/lsu/LsuPlugin.scala",
                 "src/fpga/vendor/vexriscv/VexiiRiscv/src/main/scala/vexiiriscv/execute/lsu/Agu.scala",
                 "src/fpga/vendor/vexriscv/VexiiRiscv/src/main/scala/vexiiriscv/riscv/Rvi.scala",
                 "src/fpga/vendor/vexriscv/VexiiRiscv/src/main/scala/vexiiriscv/misc/PrivilegedPlugin.scala"]:
        p = ROOT / name
        evidence[str(p)] = sha(p)
    result = dict(passed=True, scope="Static decoded STT_FUNC instruction audit, not a dynamic coverage proof",
                  results=rows, remove_A_safe=False, all_examined_AMO_arithmetic_counts_zero=all(r["amo_count"] == 0 for r in rows),
                  retained_os_payload_matches=True, retained_boot_mif_matches=True,
                  note="Current malloc/free paths contain unconditional LR/SC loops; single-threaded does not imply A is unused. Existing --with-rva controls AMO and LR/SC together. A custom LR/SC-only generator option would require decoder/ALU split plus truthful ISA/capability policy and new CPU qualification; no ALM saving measured.",
                  evidence_sha256=evidence)
    (OUT / "result.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps([{k:r[k] for k in ["label", "lr_count", "sc_count", "amo_count"]} for r in rows]))


if __name__ == "__main__":
    main()
