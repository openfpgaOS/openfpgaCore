#!/usr/bin/env python3
"""Record the independently inspected normal-v2 W-predicate assembly paths.

This is a concrete-executable review, not an exhaustive RV32 emulator or a
compiler-independent source proof. Address and ELF checks bind the inspection.
"""
import json
import struct
from pathlib import Path

from phase9_codegen_review import retained_definitions
from phase9_experiment import PARENT, directory, sha, write


def elf_word(path, address):
    data = path.read_bytes()
    assert data[:7] == b'\x7fELF\x01\x01\x01'
    offset = struct.unpack_from('<I', data, 28)[0]
    size, count = struct.unpack_from('<HH', data, 42)
    for index in range(count):
        kind, start, virtual, _, length = struct.unpack_from('<IIIII', data, offset + index * size)
        if kind == 1 and virtual <= address and address + 4 <= virtual + length:
            return struct.unpack_from('<I', data, start + address - virtual)[0]
    raise AssertionError(hex(address))


def main():
    candidate = directory('w-guard-v2')
    output = candidate / 'codegen-normal'
    review = json.loads((output / 'review.json').read_text())
    assert review['passed']
    assert sha(candidate / 'app.elf') == '4e3c129896cd1ff676e8168cebad1a642b9ec55454eaeac73986a558d52aed38'
    assert sha(PARENT / 'app.elf') == '73bd7e68c87835cdca6b485277c44c38bd9fb3182aca502755d2cc55f7d7d23a'
    api, _ = retained_definitions()
    evidence = {}
    paths = {
        'F9': [(0x104d1a06, 0x104d1a70), (0x104d1b6e, 0x104d1bd2),
               (0x104d2098, 0x104d20a0)],
        'candidate': [(0x104d1a06, 0x104d1aa0), (0x104d1eee, 0x104d1efe),
                      (0x104d1b9e, 0x104d1c30), (0x104d2100, 0x104d2108),
                      (0x104d22c4, 0x104d22d8)]}
    for name, ranges in paths.items():
        asm = output / (name + '-gfx_cpu_indexed_run.asm')
        assert sha(asm) == review['results'][name]['functions']['gfx_cpu_indexed_run']['disassembly_sha256']
        rows = api['insns'](asm.read_text())
        selected = [row for row in rows if any(lo <= row[0] < hi for lo, hi in ranges)]
        # Both unrolled cold predicates contain exactly the original six ordered
        # range comparisons and two equality comparisons, including split blocks.
        assert sum(row[1] == 'fle.s' for row in selected) == 12
        assert sum(row[1] == 'feq.s' for row in selected) == 4
        evidence[name] = dict(disassembly_sha256=sha(asm), ranges=[list(map(hex, x)) for x in ranges],
                              assembly=[row[3] for row in selected])
    assert elf_word(PARENT / 'app.elf', 0x10d3b3d8) == 0x46fffe00
    assert elf_word(candidate / 'app.elf', 0x10d3b458) == 0x46fffe00
    report = dict(
        passed=True, mode='normal', candidate='w-guard-v2',
        helper_sha256=sha(Path(__file__)), graph_review_sha256=sha(output / 'review.json'),
        evidence=evidence, bound_float_bits='0x46fffe00', bound_float=32767.0,
        review_method='Manual register/control-flow inspection of the hash-bound actual ELF; assertions bind both emitted predicate copies and their constants.',
        hot_path='All three unsigned (W_bits-1) values must be <=0x46fffdfe, so exactly positive finite W below32767 reaches integer equality. All equal falls back; any unequal reaches the original live continuation.',
        cold_path='Every other encoding executes the original ordered FLE(W,0), FLE(32767,W) reject-if-true sequence for W1,W2,W3, then the original short-circuit FEQ(W1,W2) and FEQ(W1,W3) uniform-W rejection.',
        exceptional_inputs='Unlike v1, NaNs reach the same FLE/FEQ cold path as F9. The old emitted fast-math behavior (NaN range comparisons false) is preserved. Signed zeros, infinities, negative finite values and upper-bound values take that same original path.',
        rounding='All five emitted culling FMA operation graphs pass the separately bound review. This predicate adds no floating arithmetic or projected-coordinate memo.',
        flags='Positive finite hot-path comparisons cannot raise IEEE invalid and perform no arithmetic rounding. Exceptional inputs retain the ordered floating comparison sequence; full-machine fflags are not separately measured here.',
        limits='This record reviews this exact normal ELF only. Diagnostic or composed candidates need their own actual-ELF review and output/performance measurements.')
    write(output / 'predicate-review.json', report)
    for source, target in [(Path(__file__), 'predicate-review-recipe.py'),
                           (Path(__file__).with_name('phase9_codegen_review.py'), 'review-recipe.py')]:
        with (output / target).open('xb') as destination:
            destination.write(source.read_bytes())
    print(json.dumps(dict(passed=True, output=str(output / 'predicate-review.json')), indent=2))


if __name__ == '__main__':
    main()
