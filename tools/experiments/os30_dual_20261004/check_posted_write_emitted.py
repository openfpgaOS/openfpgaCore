#!/usr/bin/env python3
"""Audit exact posted-write cone scope and its exhaustive extracted proof."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
CAND = ART / 'posted-write-factor-v1'
OUT = ART / 'posted-write-emitted-audit-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    base = CAND / 'reference_cpu_system.v'
    candidate = CAND / 'cpu_system.v'
    assert digest(base) == '2960fe6b493b0de3de53b04f841a6667205c5141ce86a05b1b228cda9ab86833'
    assert digest(candidate) == '2eb4017111babe0b40651535025847092444930adbfa98e007eea97b2e35c214'
    assert base.read_bytes() == (ART / 'fit-rvc-skid-pingpong-v1/frozen/src/fpga/common/cpu_system.v').read_bytes()
    before, after = base.read_text(), candidate.read_text()
    spec = importlib.util.spec_from_file_location('emitter', CAND / 'posted_write_factor.py')
    emitter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(emitter)
    assert before.count(emitter.OLD_PREFIX) == before.count(emitter.OLD_LAST) == 1
    assert before.replace(emitter.OLD_PREFIX, emitter.NEW_PREFIX, 1).replace(emitter.OLD_LAST, emitter.NEW_LAST, 1) == after
    assert re.search(r'localparam\s+WR_PTR_W\s*=\s*2\s*;', before)
    # The exhaustive test substitutes every combinational cutpoint, including
    # all queue counts (not merely <=4), circular head positions, arbitrary
    # link vectors, both length qualifiers and all accepted/held/live indices.
    tb = (CAND / 'tb.sv').read_text()
    assert tb.startswith(emitter.module('reference_cone', before) + emitter.module('candidate_cone', after))
    assert 'for(integer n=0;n<262144;n=n+1)' in tb
    assert 'if(before_result!==after_result)$fatal' in tb
    # Verify prefix code independently on every abstract monotonic chain.
    for count in range(8):
        for links in range(8):
            m1 = count >= 2 and bool(links & 1)
            m2 = m1 and count >= 3 and bool(links & 2)
            m3 = m2 and count >= 4 and bool(links & 4)
            old = 3 if m3 else 2 if m2 else 1 if m1 else 0
            new = 2 * int(m2) + int(m1 and (not m2 or m3))
            assert old == new
    OUT.mkdir(exist_ok=False)
    with (OUT / 'proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND / 'proof-obj/Vtb')], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    assert 'PASS: 262144 actual coalescer/WLAST control combinations' in (OUT / 'proof-rerun.log').read_text()
    report = dict(reference_sha256=digest(base), candidate_sha256=digest(candidate),
                  original_matches_frozen_pingpong_fullfit=True,
                  exact_two_combination_replacement_scope=True,
                  sequential_source_and_fifo_accounting_unchanged=True,
                  no_extra_cycles_or_storage=True, actual_pointer_width_verified=2,
                  actual_cone_proof_rerun_cases=262144,
                  all_counts_circular_positions_masks_and_lengths_included=True,
                  accepted_over_held_over_live_priority_exact=True,
                  coalesce_false_zero_length_exact=True,
                  paired_runtime_validation='Recorded separately by validation agent; this artifact checks source and exhaustive cone proof.',
                  physical_benefit='Requires full-core fit.')
    (OUT / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
