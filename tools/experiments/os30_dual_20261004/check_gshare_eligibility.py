#!/usr/bin/env python3
"""Independent source-bound four-state review of the kept GShare predicate."""
from pathlib import Path
import hashlib
import itertools
import json
import re

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
OLD = ART / 'lane1-rdid-constant-v1/immediate/VexiiRiscv.v'
NEW = ART / 'gshare-eligibility-v1/immediate/VexiiRiscv.v'
OUT = ART / 'gshare-eligibility-emitted-audit-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def parse(expr):
    toks = re.findall(r'&&|[!|()]|\w+', expr)
    pos = 0
    def term():
        nonlocal pos
        tok = toks[pos]; pos += 1
        if tok in ('!', '|'):
            return (tok, term())
        if tok == '(':
            tree = conjunction()
            assert toks[pos] == ')'; pos += 1
            return tree
        assert re.fullmatch(r'\w+', tok)
        return tok
    def conjunction():
        nonlocal pos
        tree = term()
        while pos < len(toks) and toks[pos] == '&&':
            pos += 1
            tree = ('&&', tree, term())
        return tree
    tree = conjunction()
    assert pos == len(toks)
    return tree

def evaluate(tree, inputs, assignments):
    if isinstance(tree, str):
        return inputs[tree] if tree in inputs else evaluate(parse(assignments[tree]), inputs, assignments)
    left = evaluate(tree[1], inputs, assignments)
    if tree[0] == '!': return 1-left if left < 2 else 2
    if tree[0] == '|': return left if left < 2 else 2
    right = evaluate(tree[2], inputs, assignments)
    return 0 if 0 in (left,right) else 1 if (left,right)==(1,1) else 2

def main():
    assert sha(OLD) == 'a78ced6dffd7c75a184481b8906f3915aa063b7132325b2ee867eaf4cc9fde1a'
    assert sha(NEW) == 'fa82e3935b8d65b3443ad8ea6943243b74b4b7350b0f93e1b4adf91b96d6994d'
    old,new = OLD.read_text(),NEW.read_text()
    maps = [dict(re.findall(r'^  assign (\w+) = ([^;]+);',s,re.M)) for s in (old,new)]
    lhs = 'GSharePlugin_logic_mem_write_valid'
    old_line = '  assign '+lhs+' = '+maps[0][lhs]+';'
    new_block = '  (* keep = "true" *) wire gshare_training_eligible;\n  assign gshare_training_eligible = '+maps[1]['gshare_training_eligible']+';\n  assign '+lhs+' = '+maps[1][lhs]+';'
    assert old.count(old_line)==new.count(new_block)==1
    assert old.replace(old_line,new_block,1)==new
    module = re.search(r'^module StreamArbiter_2 \(.*?^endmodule',old,re.M|re.S)[0]
    inst = re.search(r'^  StreamArbiter_2 streamArbiter_3 \(.*?^  \);',old,re.M|re.S)[0]
    assert not re.search(r'\b(reg|always)\b',module)
    assert '  assign io_output_valid = io_inputs_0_valid;' in module
    for port,net in [('io_output_valid','streamArbiter_3_io_output_valid'),('io_inputs_0_valid','LearnPlugin_logic_buffered_0_valid')]:
        assert re.search(r'\.'+port+r'\s*\(\s*'+net+r'\s*\)',inst)
    assert len(re.findall(r'^  assign io_output_payload_(\w+) = io_inputs_0_payload_\1;',module,re.M))==14
    for a in maps:
        a['streamArbiter_3_io_output_valid']='LearnPlugin_logic_buffered_0_valid'
    leaves = ['execute_ctrl3_up_LANE_SEL_lane0','execute_ctrl3_down_isReady','execute_lane0_ctrls_3_upIsCancel','execute_ctrl3_down_early0_BranchPlugin_SEL_lane0','LearnPlugin_logic_learn_payload_isBranch','GSharePlugin_logic_onLearn_overflow']
    # Every leaf is scalar; a Z reduction becomes X as in actual Verilog.
    for name in leaves:
        assert re.search(r'^  (?:wire|reg)\s+'+name+r';$',old,re.M),name
    count=0
    for values in itertools.product(range(4),repeat=len(leaves)):
        env=dict(zip(leaves,values))
        assert evaluate(parse(maps[0][lhs]),env,maps[0])==evaluate(parse(maps[1][lhs]),env,maps[1])
        count+=1
    OUT.mkdir(exist_ok=False)
    report=dict(passed=True,reference_sha256=sha(OLD),candidate_sha256=sha(NEW),
        exact_complete_source_reconstruction=True,actual_expression_four_state_cases=count,
        actual_arbiter_instance_and_direct_valid_binding_verified=True,all_14_payloads_direct=True,
        full_cancellation_preserved=True,sequential_address_data_and_collision_bypass_unchanged=True,
        new_registers=0,new_cycles=0,physical_benefit_and_whole_cpu_qualification='Separate, not implied by this audit.')
    (OUT/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__': main()
