#!/usr/bin/env python3
"""Independently check the emitted audio ramp rewrite and exhaustive proof."""
from pathlib import Path
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
CAND = ART / 'audio-ramp-balance-v1'
OUT = ART / 'audio-ramp-emitted-audit-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest = json.loads((CAND / 'manifest.json').read_text())
    base = ROOT / manifest['reference']
    candidate = CAND / 'rtl/audio_mixer.v'
    assert digest(base) == 'b530d3c7bba3c1e0e809008131bdaade03af8f2ecf9b5375822d6bb273d779f3'
    assert digest(candidate) == '7dd77c3e2ed727b931a30b6e716d7b918c801c8e4c60bd08a246fb656884ea97'
    old, new = base.read_text(), candidate.read_text()
    pattern = r'function \[7:0\] ramp_step;.*?endfunction'
    old_functions, new_functions = re.findall(pattern, old, re.S), re.findall(pattern, new, re.S)
    assert len(old_functions) == len(new_functions) == 1
    before, after = old_functions[0], new_functions[0]
    assert old.replace(before, after, 1) == new
    assert before + '\n' == (CAND / 'original-function.v').read_text()
    assert after + '\n' == (CAND / 'candidate-function.v').read_text()
    proof = (CAND / 'ramp_proof.v').read_text()
    assert proof.count(before) == proof.count(after) == 1
    assert "take_up = (step != 8'd0) && !up[8] && (up[7:0] < tgt);" in after
    assert "take_dn = (step != 8'd0) && !dn[8] && (dn[7:0] > tgt);" in after
    assert "ramp_step = ({8{take_up}} & up[7:0]) |\n                    ({8{take_dn}} & dn[7:0]) |\n                    ({8{!(take_up || take_dn)}} & tgt);" in after
    # Both predicates include nonzero step. Thus a nonoverflowed upward sum
    # below target implies cur<target, and a nonborrowed downward sum above
    # target implies cur>target. They cannot both hold. All remaining cases
    # saturate/snap to target, including cur==target and step==0.
    for line in ["up = {1'b0, cur} + {1'b0, step};", "dn = {1'b0, cur} - {1'b0, step};"]:
        assert before.count(line) == after.count(line) == 1
    assert (CAND / 'reference/run.log').read_bytes() == (CAND / 'candidate/run.log').read_bytes()
    assert (CAND / 'reference/throughput.csv').read_bytes() == (CAND / 'candidate/throughput.csv').read_bytes()
    assert 'Results: ' in (CAND / 'reference/run.log').read_text()
    assert '0 failed' in (CAND / 'reference/run.log').read_text()
    assert digest(CAND / 'reference/audio_mixer.v') == digest(base)
    assert digest(CAND / 'candidate/audio_mixer.v') == digest(candidate)
    OUT.mkdir(exist_ok=False)
    with (OUT / 'proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND / 'proof-obj/Vramp_proof')], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    assert 'PASS all16,777,216 triples' in (OUT / 'proof-rerun.log').read_text()
    result = dict(reference_sha256=digest(base), candidate_sha256=digest(candidate),
                  only_ramp_function_changed=True, all_sequential_logic_unchanged=True,
                  exact_9bit_sum_difference_unchanged=True,
                  predicates_imply_original_direction=True, predicates_mutually_exclusive=True,
                  zero_step_and_equal_input_target_preserved=True,
                  actual_extracted_functions_match_emitted_source=True,
                  exhaustive_proof_rerun_inputs=16777216,
                  independent_distance_oracle_in_proof=True,
                  original_mixer_test_logs_and_36_throughput_cases_identical=True,
                  original_manifest_sha256=digest(CAND / 'manifest.json'),
                  timing_or_area_benefit='Requires full-core fit; no physical claim from source proof.')
    (OUT / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
