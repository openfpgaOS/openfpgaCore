#!/usr/bin/env python3
"""Check that PERF_DIAG=0 changes only assertion source-line immediates."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-vertex-perf-20261004'
old = ROOT / 'docs/measurements/sm64-triangle-cache-20261004/capture-cache-v6-fixed/sm64.elf'
new = OUT / 'SM64/.obj/sm64/app.elf'

def load_segments(path):
    data = path.read_bytes()
    assert data[:6] == b'\x7fELF\x01\x01'
    phoff, = struct.unpack_from('<I', data, 28)
    phsize, count = struct.unpack_from('<HH', data, 42)
    segments = []
    for i in range(count):
        kind, off, va, pa, size, memsize, flags, align = struct.unpack_from('<8I', data, phoff+i*phsize)
        if kind == 1:
            segments.append(((va, pa, size, memsize, flags, align), data[off:off+size]))
    return segments

a, b = load_segments(old), load_segments(new)
assert len(a) == len(b) == 2
assert a[1] == b[1]
assert a[0][0] == b[0][0]
base = a[0][0][0]
before, after = a[0][1], bytearray(b[0][1])
addresses = (0x104d7192,0x104d71bc,0x104d720a,0x104d7248,0x104d727a,
             0x104d72b4,0x104d72de,0x104d72fe,0x104d731e,0x104d733e,0x104d735e)
increments=[]
for addr in addresses:
    offset = addr-base
    old_word, = struct.unpack_from('<I', before, offset)
    new_word, = struct.unpack_from('<I', after, offset)
    assert old_word & 0xfffff == 0x60613  # addi a2,a2,imm: __assert_fail line argument
    assert new_word & 0xfffff == old_word & 0xfffff
    increment=(new_word-old_word)>>20
    assert 0<increment<64, (hex(addr),increment)
    increments.append(increment)
    after[offset:offset+4] = before[offset:offset+4]
assert bytes(after) == before, 'Changes beyond the 11 assertion line arguments'
for name, elf in (('baseline', old), ('disabled', new)):
    disassembly = subprocess.check_output(['llvm-objdump', '--disassemble',
        '--start-address=0x104d7180', '--stop-address=0x104d7380', str(elf)], text=True)
    assert '<__assert_fail>' in disassembly
    (OUT / ('disabled-check-' + name + '.asm')).write_text(disassembly)
result = dict(loadable_byte_identical=False, only_assertion_line_numbers_changed=True,
    changed_bytes=sum(x != y for x, y in zip(before, b[0][1])), assertion_arguments=11,
    source_line_increments=increments, same_segment_layout=True,
    baseline_sha256=hashlib.sha256(old.read_bytes()).hexdigest(),
    disabled_sha256=hashlib.sha256(new.read_bytes()).hexdigest())
(OUT / 'disabled-loadable-check.json').write_text(json.dumps(result, indent=2) + '\n')
print('PASS: all loadable bytes match except 11 assertion line-number arguments:',increments)
