#!/usr/bin/env python3
"""Independent carry-split source/proof binding review; no simulations or fit."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
C = P / 'audio-accum-carry-v1'
O = P / 'audio-accum-carry-review-v1'
BASE = P / 'compact-lod5-core-v11/frozen/src/fpga/common/audio_mixer.v'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def main():
    manifest = read(C / 'manifest.json')
    for name, digest in manifest['files'].items():
        require(sha(C / name) == digest, 'Component changed: ' + name)
    for name, digest in manifest['evidence_sha256'].items():
        require(sha(ROOT / name) == digest, 'Evidence changed: ' + name)
    old = BASE.read_text()
    new = (C / 'audio_mixer.v').read_text()
    require('localparam ACCUM_W = 21;' in old, 'Unsupported accumulator width')
    begin = new.index('// Sign extension contributes only -sample[15]')
    end = new.index('function [7:0] ramp_step;', begin)
    helper = new[begin:end]
    body = helper[helper.index('function [ACCUM_W-1:0] accum_add_s16;'):].strip()
    expected = '''function [ACCUM_W-1:0] accum_add_s16;
    input [ACCUM_W-1:0] accumulator;
    input [15:0] sample;
    (* keep *) reg [16:0] low_sum;
    (* keep *) reg [4:0] upper_plus, upper_minus;
    reg [4:0] upper_result;
    begin
        low_sum = {1'b0, accumulator[15:0]} + {1'b0, sample};
        upper_plus = accumulator[20:16] + 5'd1;
        upper_minus = accumulator[20:16] - 5'd1;
        case ({sample[15], low_sum[16]})
            2'b01: upper_result = upper_plus;
            2'b10: upper_result = upper_minus;
            default: upper_result = accumulator[20:16];
        endcase
        accum_add_s16 = {upper_result, low_sum[15:0]};
    end
endfunction'''
    require(body == expected, 'Unexpected carry helper')
    reconstructed = new[:begin] + new[end:]
    for channel in ('l', 'r'):
        after = f'accum_{channel} <= accum_add_s16(accum_{channel}, samp_{channel}_tmp);'
        before = f'accum_{channel} <= accum_{channel} + $signed({{{{(ACCUM_W-16){{samp_{channel}_tmp[15]}}}}, samp_{channel}_tmp}});'
        require(reconstructed.count(after) == old.count(before) == 1, 'Call site topology differs')
        reconstructed = reconstructed.replace(after, before)
    require(reconstructed == old, 'Additional source change')
    require((C / 'baseline.v').read_bytes() == BASE.read_bytes(), 'Reference parent differs')
    require((C / 'native/frozen/audio_mixer.v').read_text() == new, 'Native candidate differs')
    reference = (C / 'native/frozen/audio_mixer_reference.v').read_text()
    require(reference == old.replace('module audio_mixer (', 'module audio_mixer_reference ('), 'Native reference differs')
    require(body in (C / 'literal/probe.v').read_text(), 'Literal probe does not test selected helper')
    result = read(C / 'result.json')
    literal = read(C / 'literal/result.json')
    native = read(C / 'native/result.json')
    require(result['passed'] and result['candidate_sha256'] == sha(C / 'audio_mixer.v'), 'Candidate gate failed')
    require(literal['passed'] and literal['literal_cases'] == 17777216 and literal['upper_control_cases'] == 128, 'Literal proof coverage differs')
    require(literal['negative_unsigned_sample_mismatches'] > 0, 'Missing negative sensitivity')
    require(native['passed'] and native['assertions_passed'] == 177 and native['samples'] == 14223 and native['throughput_passed'], 'Native proof failed')
    require('--assert' in read(C / 'native/command.json'), 'Native assertions disabled')
    tb = (C / 'native/frozen/tb_audio_mixer.v').read_text()
    outputs = re.findall(r'output\s+(?:reg|wire)\s+(?:\[[^\]]+\]\s*)?(\w+)', old[:old.index(');')])
    for name in outputs:
        check = f'assert({name} == reference_{name});'
        if name == 'voice_end_irq':
            check = 'assert(dut.voice_end_irq==reference.voice_end_irq);'
        require(check in tb, 'Missing actual output equality: ' + name)
    for check in ('assert(dut.state==reference.state);', 'assert(dut.cur_voice==reference.cur_voice);',
                  'assert(dut.accum_l==reference.accum_l && dut.accum_r==reference.accum_r);'):
        require(check in tb, 'Missing schedule/state check')
    require('always @(negedge clk) if(reset_n)' in tb, 'Live comparison ownership differs')
    O.mkdir(exist_ok=False)
    review = dict(passed=True, source_review_only=True, no_duplicate_simulations=True,
                  candidate_sha256=sha(C / 'audio_mixer.v'), component_manifest_sha256=sha(C / 'manifest.json'),
                  exact_helper_and_two_call_sites_only=True, added_registers=0, added_cycles=0,
                  unchanged_ports_state_reset_volume_saturation_and_memory_protocol=True,
                  reasoning=[
                      'Write a=65536*H+L and signed sample=S-65536*s. If L+S=65536*c+R, the original 21-bit result is {((H+c-s) mod32),R}.',
                      'The four {s,c} branches select H, H+1, H-1, H respectively. Five-bit upper arithmetic and the 16-bit result slice exactly implement the original modulo2^21 wrap.',
                      'All helper temporaries are assigned on every evaluation; function-local reg declarations introduce no sequential storage.',
                      'The selected source replaces only the two final accumulation expressions. Existing saturated signed16 samples, state transition, mixer gating and reset priorities are unchanged.',
                      'The full-domain binary identity does not require reachable accumulator ranges, sample signs, bounded voices or one-hot controls.'
                  ], author_literal=literal, author_native=native,
                  map_or_fit_performed=False, timing_gain_not_established=True, deployment_ready=False)
    (O / 'review.json').write_text(json.dumps(review, indent=2) + '\n')
    (O / 'recipe.py').write_bytes(Path(__file__).read_bytes())
    evidence = [BASE, C / 'manifest.json', C / 'prepared.json', C / 'audio_mixer.v', C / 'result.json',
                C / 'literal/probe.v', C / 'literal/result.json', C / 'native/result.json',
                C / 'native/frozen/tb_audio_mixer.v', C / 'native/command.json', Path(__file__)]
    sealed = dict(files={x.name: sha(x) for x in O.iterdir() if x.is_file()},
                  evidence_sha256={str(x.relative_to(ROOT)): sha(x) for x in evidence})
    (O / 'manifest.json').write_text(json.dumps(sealed, indent=2) + '\n')
    print(sha(O / 'manifest.json'))


if __name__ == '__main__':
    main()
