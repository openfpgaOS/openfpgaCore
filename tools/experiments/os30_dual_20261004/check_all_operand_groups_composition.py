#!/usr/bin/env python3
"""Independently check all three local forwarding groups and six compositions."""
from pathlib import Path
import difflib
import hashlib
import itertools
import json
import re

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
OUT = ART / 'all-operand-groups-composition-audit-v1'
FILES = {
 'base': ('pointer-packer-combined-v1/immediate/VexiiRiscv.v', '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'),
 'integer': ('integer-rs1-priority-groups-v2/immediate/VexiiRiscv.v', 'b182970b0f7a7e24dbafc5a6f7dc4c4008b14b62daf215dcb8b0e67578481dbf'),
 'rs2': ('fp-rs2-priority-2plus8-v1/immediate/VexiiRiscv.v', '6c3560a5211bd184e7ca52f50cb6d9ab79c2f0eb54db0a7aa834fd961b70b3f5'),
 'rs3': ('pointer-packer-rs3-combined-v1/immediate/VexiiRiscv.v', 'a083bdb084b9dfd7b8944287ef3411b4dc739bf366f7dfa3f42599d5edd0528b'),
 'previous': ('integer-rs1-fp-rs2-combined-v1/immediate/VexiiRiscv.v', 'fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb'),
 'final': ('all-operand-groups-v1/immediate/VexiiRiscv.v', 'ffed6f6e299b8e7d3275b781d55d7094ae4d6c086b9f57d6553f794cd545082c'),
 'rs3_original_base': ('fp-last-combined-v1/immediate/VexiiRiscv.v', '9faabaeecba4c514cc7f76739218ff36a402d016617fb49c859ad57c6eaeb377'),
 'rs3_original': ('fp-rs3-priority-groups-v1/immediate/VexiiRiscv.v', '186a99f36f0c7e3afaa1f6bb832f58b95c4e2cc6373725c5f4b6669d370de704')}


def changes(before, after):
    a, b = before.splitlines(True), after.splitlines(True)
    return [(''.join(a[g[0][1]:g[-1][2]]), ''.join(b[g[0][3]:g[-1][4]]))
            for g in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(2)]


def apply(source, edits):
    for before, after in edits:
        assert before and source.count(before) == 1
        source = source.replace(before, after, 1)
    return source


def main():
    sources = {}
    for name, (path, sha) in FILES.items():
        data = (ART / path).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha
        sources[name] = data.decode()
    edits = {name: changes(sources['base'], sources[name]) for name in ['integer', 'rs2', 'rs3']}
    assert all(len(v) == 1 for v in edits.values())
    assert edits['rs3'] == changes(sources['rs3_original_base'], sources['rs3_original'])
    assert apply(sources['previous'], edits['rs3']) == sources['final']
    assert apply(sources['final'], [(b, a) for a, b in edits['rs3']]) == sources['previous']
    orders = []
    for order in itertools.permutations(edits):
        source = sources['base']
        for component in order:
            source = apply(source, edits[component])
        assert source == sources['final']
        for component in reversed(order):
            source = apply(source, [(b, a) for a, b in edits[component]])
        assert source == sources['base']
        orders.append(order)
    seq = lambda s: re.findall(r'^  always @\(posedge clk.*?^  end', s, re.M | re.S)
    assert seq(sources['base']) == seq(sources['final'])
    audits = {}
    for name, rel, expected in [
        ('integer', 'integer-rs1-priority-emitted-audit-v2', FILES['integer'][1]),
        ('rs2', 'fp-rs2-priority-2plus8-emitted-audit-v1', FILES['rs2'][1]),
        ('rs3', 'fp-rs3-priority-emitted-audit-v1', FILES['rs3_original'][1])]:
        path = ART / rel / 'manifest.json'
        result = json.loads(path.read_text())
        assert result['candidate_sha256'] == expected and result['actual_cones_freshly_match_artifacts']
        audits[name] = dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    OUT.mkdir(exist_ok=False)
    result = dict(source_sha256={name: sha for name, (_, sha) in FILES.items()},
        independent_component_audits=audits, application_and_reverse_orders=orders,
        exact_rs3_reuse_from_independently_proved_186a=True,
        only_rs3_data_block_added_to_fb18=True, all_six_orders_and_reversals_exact=True,
        no_other_source_changes=True, all_sequential_blocks_byte_identical=True,
        existing_integer_output_keep_and_all_live_gates_preserved=True,
        new_registers=0, new_cycles=0,
        qualification='Final retained CPU suite and complete-core area/timing remain separate.')
    (OUT / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
