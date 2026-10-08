#!/usr/bin/env python3
"""Audit the two-trap-only derivative independently against both parents."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = ART / 'decode-pingpong-v1/immediate/VexiiRiscv.v'
    with_hit = ART / 'aligner-trap-direct-v1/immediate/VexiiRiscv.v'
    candidate = ART / 'aligner-trap-no-sampled-hit-v1/immediate/VexiiRiscv.v'
    assert sha(baseline) == 'd471c5da61b3f5f67ce3c78eb0bf34d98cb828407ba0da15b63ac1044ed50f86'
    assert sha(with_hit) == 'ec69d8b6b09f60fe60f03227bb1bad4c19ea841a28e874d8025b6970b1d639c3'
    assert sha(candidate) == '1aaeb857d1ceec36b89d8b459365c0a2ee2b76ab0c9b7afc865a81c1aae8e616'
    original, full, actual = baseline.read_text(), with_hit.read_text(), candidate.read_text()
    expected = original
    for lane in range(2):
        name = f'AlignerPlugin_logic_extractors_{lane}_ctx_trap'
        old = re.findall(r'^  assign '+name+r' = [^;]+;', original, re.M)
        new = re.findall(r'^  // Classify the first usable instruction directly, before its one-hot mux\.\n  // A boundary instruction touches fetch only when it needs a second slice\.\n  assign '+name+r' =[^;]+;', full, re.M)
        assert len(old) == len(new) == 1
        expected = expected.replace(old[0], new[0], 1)
    assert expected == actual
    scalar = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HIT_lane0'
    incoming = 'execute_ctrl3_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
    aggregate = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HIT_lane0'
    vector = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
    reverse = full.replace('  reg                 '+scalar+';\n','',1)
    reverse = reverse.replace('      '+scalar+' <= (|'+incoming+');\n','',1)
    reverse = reverse.replace('  assign '+aggregate+' = '+scalar+';',
                              '  assign '+aggregate+' = (|'+vector+');',1)
    assert reverse == actual
    assert not re.search(r'\b'+scalar+r'\b', actual)
    out = ART / 'aligner-trap-no-hit-emitted-audit-v1'
    out.mkdir(exist_ok=False)
    report = dict(baseline_sha256=sha(baseline), with_hit_sha256=sha(with_hit), candidate_sha256=sha(candidate),
                  exactly_pingpong_plus_two_qualified_trap_equations=True,
                  exactly_full_trap_candidate_minus_three_sampled_hit_edits=True,
                  all_baseline_sequential_source_and_hit_vector_consumers_exact=True,
                  zero_added_state_or_cycles=True,
                  qualified_trap_equations_identical_to_exhaustively_proved_ec69=True,
                  whole_cpu_tests='Generator owns14-case comparison; not duplicated by this source audit.',
                  physical_timing='Measured separately; no conclusion from source identity.')
    (out/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
