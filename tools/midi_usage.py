#!/usr/bin/env python3
"""Build an OFSF usage mask from Standard MIDI files or Duke/Build GRP files."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


def read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    while True:
        b = data[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)
        if not (b & 0x80):
            return value, pos


class Usage:
    def __init__(self) -> None:
        self.program_notes: dict[int, set[int]] = {}
        self.drum_notes: dict[int, set[int]] = {}
        self.bank_selects: set[tuple[int, int, int]] = set()
        self.files: list[str] = []

    def merge(self, other: "Usage") -> None:
        for prog, notes in other.program_notes.items():
            self.program_notes.setdefault(prog, set()).update(notes)
        for prog, notes in other.drum_notes.items():
            self.drum_notes.setdefault(prog, set()).update(notes)
        self.bank_selects.update(other.bank_selects)
        self.files.extend(other.files)


def parse_midi(name: str, data: bytes) -> Usage:
    if data[:4] != b"MThd":
        raise ValueError(f"{name}: not a Standard MIDI file")

    header_len = struct.unpack_from(">I", data, 4)[0]
    _fmt, tracks, _division = struct.unpack_from(">HHH", data, 8)
    pos = 8 + header_len

    usage = Usage()
    usage.files.append(name)

    program = [0] * 16
    bank_msb = [0] * 16
    bank_lsb = [0] * 16

    for _track in range(tracks):
        if data[pos:pos + 4] != b"MTrk":
            raise ValueError(f"{name}: missing MTrk at offset {pos}")

        length = struct.unpack_from(">I", data, pos + 4)[0]
        p = pos + 8
        end = p + length
        pos = end
        running_status: int | None = None

        while p < end:
            _delta, p = read_vlq(data, p)
            status = data[p]
            if status < 0x80:
                if running_status is None:
                    raise ValueError(f"{name}: running status without prior status")
                status = running_status
            else:
                p += 1
                if status < 0xF0:
                    running_status = status

            if status == 0xFF:
                _meta_type = data[p]
                p += 1
                size, p = read_vlq(data, p)
                p += size
                continue

            if status in (0xF0, 0xF7):
                size, p = read_vlq(data, p)
                p += size
                continue

            event = status & 0xF0
            channel = status & 0x0F

            if event in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                a = data[p]
                b = data[p + 1]
                p += 2

                if event == 0x90 and b != 0:
                    if channel == 9:
                        usage.drum_notes.setdefault(program[channel], set()).add(a)
                    else:
                        usage.program_notes.setdefault(program[channel], set()).add(a)

                if event == 0xB0:
                    if a == 0:
                        bank_msb[channel] = b
                    elif a == 32:
                        bank_lsb[channel] = b
                    if a in (0, 32):
                        usage.bank_selects.add((channel, bank_msb[channel], bank_lsb[channel]))
            elif event in (0xC0, 0xD0):
                a = data[p]
                p += 1
                if event == 0xC0:
                    program[channel] = a
            else:
                raise ValueError(f"{name}: unsupported status 0x{status:02x}")

    return usage


def read_grp(path: Path) -> list[tuple[str, bytes]]:
    with path.open("rb") as f:
        header = f.read(16)
        if header[:12] != b"KenSilverman":
            raise ValueError(f"{path}: not a Build GRP file")

        count = struct.unpack_from("<I", header, 12)[0]
        entries: list[tuple[str, int, int]] = []
        offset = 16 + count * 16
        for _ in range(count):
            rec = f.read(16)
            name = rec[:12].split(b"\0", 1)[0].decode("ascii", "replace")
            size = struct.unpack_from("<I", rec, 12)[0]
            entries.append((name, size, offset))
            offset += size

        out = []
        for name, size, offset in entries:
            if not name.lower().endswith((".mid", ".midi")):
                continue
            f.seek(offset)
            out.append((name, f.read(size)))
        return out


def collect_path(path: Path) -> Usage:
    usage = Usage()
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.suffix.lower() in (".mid", ".midi"):
                usage.merge(parse_midi(str(child), child.read_bytes()))
        return usage

    suffix = path.suffix.lower()
    if suffix in (".mid", ".midi"):
        usage.merge(parse_midi(str(path), path.read_bytes()))
    elif suffix == ".grp":
        for name, data in read_grp(path):
            usage.merge(parse_midi(name, data))
    else:
        raise ValueError(f"{path}: expected .mid, .midi, .grp, or directory")
    return usage


def fmt_notes(notes: set[int]) -> str:
    ordered = sorted(notes)
    if not ordered:
        return ""

    ranges: list[str] = []
    start = prev = ordered[0]
    for note in ordered[1:]:
        if note == prev + 1:
            prev = note
            continue
        ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
        start = prev = note
    ranges.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ",".join(ranges)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an sf2_to_ofsf --usage file from MIDI/GRP inputs."
    )
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()

    usage = Usage()
    for path in args.inputs:
        usage.merge(collect_path(path))

    lines: list[str] = []
    lines.append("# OFSF usage mask generated by tools/midi_usage.py")
    lines.append(f"# files: {len(usage.files)}")
    lines.append(
        f"# melodic programs: {len(usage.program_notes)}, drum kits: {len(usage.drum_notes)}"
    )
    if usage.bank_selects:
        lines.append(
            "# bank selects seen but OFSF runtime currently uses bank 0 melodic "
            "and bank 128 drums:"
        )
        lines.append("# " + " ".join(
            f"ch{ch}=msb{msb}/lsb{lsb}"
            for ch, msb, lsb in sorted(usage.bank_selects)
        ))

    for prog in sorted(usage.program_notes):
        lines.append(f"program {prog} notes {fmt_notes(usage.program_notes[prog])}")
    for prog in sorted(usage.drum_notes):
        lines.append(f"drum {prog} notes {fmt_notes(usage.drum_notes[prog])}")

    text = "\n".join(lines) + "\n"
    if args.output:
        args.output.write_text(text, encoding="ascii")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
