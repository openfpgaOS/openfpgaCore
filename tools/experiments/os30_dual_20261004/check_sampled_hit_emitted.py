#!/usr/bin/env python3
"""Independent source and induction audit of the same-stage LSU hit shadow."""
from pathlib import Path
from itertools import product
import hashlib
import json
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
BASE = ART / 'decode-pingpong-v1/immediate/VexiiRiscv.v'
CAND = ART / 'lsu-hit-sampled-v1/immediate'
OUT = ART / 'lsu-hit-emitted-audit-v1'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    before, after = BASE.read_text(), (CAND / 'VexiiRiscv.v').read_text()
    assert digest(BASE) == 'd471c5da61b3f5f67ce3c78eb0bf34d98cb828407ba0da15b63ac1044ed50f86'
    assert digest(CAND / 'VexiiRiscv.v') == '4b01fdaa39fce594a8e0bb46cb756cd55d6613b8775c72207b5c7be3c77ecf09'
    vector = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HITS_lane0'
    incoming = 'execute_ctrl3_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
    shadow = 'execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HIT_lane0'
    down_vector = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HITS_lane0'
    aggregate = 'execute_ctrl4_down_LsuL1Plugin_logic_WAYS_HIT_lane0'
    decl = '  reg                 ' + shadow + ';\n'
    capture = '      ' + shadow + ' <= (|' + incoming + ');\n'
    old_alias = '  assign ' + aggregate + ' = (|' + down_vector + ');'
    new_alias = '  assign ' + aggregate + ' = ' + shadow + ';'
    for added in (decl, capture, new_alias):
        assert after.count(added) == 1
    assert after.replace(decl, '', 1).replace(capture, '', 1).replace(new_alias, old_alias, 1) == before
    assert re.findall(r'\b' + shadow + r'\s*<=\s*([^;]+);', after) == ['(|' + incoming + ')']
    original_capture = '      ' + vector + ' <= ' + incoming + ';'
    assert capture + original_capture in after
    assert before.count(original_capture) == after.count(original_capture) == 1
    assert '  assign ' + down_vector + ' = ' + vector + ';' in before
    # A flat block of nonblocking assignments under the same enable captures
    # both vector and shadow; neither is in the asynchronous reset block.
    blocks = re.findall(r'    if\(execute_ctrl3_down_isReady\) begin\n((?:      [^\n]*\n)+)    end', after)
    block = [b for b in blocks if original_capture in b]
    assert len(block) == 1 and capture in block[0]
    assert all(re.fullmatch(r'      \w+ <= [^;]+;', line) for line in block[0].splitlines())
    pos = after.index(capture)
    sync = after.rfind('  always @(posedge clk) begin\n', 0, pos)
    async_reset = after.rfind('  always @(posedge clk or posedge reset) begin\n', 0, pos)
    assert sync > async_reset and 'if(reset)' not in after[sync:pos]
    assert "      execute_ctrl4_up_LsuL1_SEL_lane0 <= 1'b0;" in after
    assert "      execute_ctrl4_up_LANE_SEL_lane0 <= 1'b0;" in after
    assert '''      if(execute_ctrl3_down_isReady) begin
        execute_ctrl4_up_LANE_SEL_lane0 <= execute_ctrl3_down_LANE_SEL_lane0;
        execute_ctrl4_up_LANE_SEL_lane1 <= execute_ctrl3_down_LANE_SEL_lane1;
        execute_ctrl4_up_LsuL1_SEL_lane0 <= execute_ctrl3_down_LsuL1_SEL_lane0;
      end''' in after
    # Unknown startup values match in four-state RTL. For arbitrary physical
    # payload startup, the first enabled capture establishes exact equality;
    # it is also the only way reset-cleared stage4 lane/LSU selects can become
    # active. No independent shadow reset can desynchronize the two payloads.
    def reduce_or(bits):
        return '1' if '1' in bits else '0' if bits == ('0', '0') else 'X'
    cases = 0
    for previous in product('01XZ', repeat=2):
        sampled = reduce_or(previous)
        for data in product('01XZ', repeat=2):
            for enable in '01XZ':
                nv = data if enable == '1' else previous
                ns = reduce_or(data) if enable == '1' else sampled
                assert ns == reduce_or(nv)
                cases += 1
    assert cases == 1024 and reduce_or(('X', 'X')) == 'X'
    tb = (CAND / 'tb.sv').read_text()
    assert capture.rstrip() in tb and original_capture in tb
    assert 'always @(posedge clk) if(execute_ctrl3_down_isReady) begin' in tb
    # Every pre-existing use of the aggregate remains identical after its
    # alias. The two-bit hit vector and tag compare equations are unchanged.
    refs = [line for line in before.splitlines() if re.search(r'\b' + aggregate + r'\b', line)]
    assert len(refs) == 7  # declaration, alias, five consumers
    assert all(line in after for line in refs if line != old_alias)
    OUT.mkdir(exist_ok=False)
    with (OUT / 'proof-rerun.log').open('w') as log:
        subprocess.run([str(CAND / 'obj/Vtb')], check=True, stdout=log, stderr=subprocess.STDOUT)
    assert 'PASS: all32 Boolean induction transitions' in (OUT / 'proof-rerun.log').read_text()
    report = dict(reference_sha256=digest(BASE), candidate_sha256=digest(CAND / 'VexiiRiscv.v'),
                  exactly_one_declaration_one_capture_one_alias_changed=True,
                  original_vector_and_all_five_consumers_exact=True,
                  unreset_payload_and_identical_advance_enable_verified=True,
                  four_state_induction_cases=cases, actual_emitted_capture_rerun_cases=32,
                  no_added_pipeline_stage_or_cycle=True, added_register_bits=1,
                  stage4_selects_reset_clear_and_capture_with_same_enable=True,
                  guarantee='Shadow equals OR(vector) inductively; first common capture establishes correspondence independently of arbitrary prior physical payload values.',
                  qualification='Whole CPU regression and fitted timing are separate.')
    (OUT / 'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
