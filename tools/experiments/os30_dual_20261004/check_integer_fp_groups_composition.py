#!/usr/bin/env python3
"""Independent exact composition audit for the selected pair forwarding groups."""
from pathlib import Path
import difflib
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[3]
ART = ROOT / 'docs/measurements/os30-dual-20261004'
OUT = ART / 'integer-rs1-fp-rs2-composition-audit-v1'
FILES = {
 'base': ('pointer-packer-combined-v1/immediate/VexiiRiscv.v', '5209f490af47c1730163b5c1b49ed475aecc116a38bdbed6c295d883e7d8bcaa'),
 'integer': ('integer-rs1-priority-groups-v2/immediate/VexiiRiscv.v', 'b182970b0f7a7e24dbafc5a6f7dc4c4008b14b62daf215dcb8b0e67578481dbf'),
 'fp': ('fp-rs2-priority-2plus8-v1/immediate/VexiiRiscv.v', '6c3560a5211bd184e7ca52f50cb6d9ab79c2f0eb54db0a7aa834fd961b70b3f5'),
 'combined': ('integer-rs1-fp-rs2-combined-v1/immediate/VexiiRiscv.v', 'fb18b1ca44bdb6ae500583c094d0c8859e3a7184457194fb40b738fe09337fdb')}


def changes(before, after):
    a, b = before.splitlines(True), after.splitlines(True)
    edits = []
    # Include two unchanged neighboring lines so a pure declaration insertion
    # remains an unambiguous independently-derived source substitution.
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(2):
        edits.append((''.join(a[group[0][1]:group[-1][2]]),
                      ''.join(b[group[0][3]:group[-1][4]])))
    return edits


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
    edits = {name: changes(sources['base'], sources[name]) for name in ['integer', 'fp']}
    assert len(edits['integer']) == len(edits['fp']) == 1
    assert apply(sources['integer'], edits['fp']) == sources['combined']
    assert apply(sources['fp'], edits['integer']) == sources['combined']
    for component, other in [('integer', 'fp'), ('fp', 'integer')]:
        assert apply(sources['combined'], [(b, a) for a, b in edits[component]]) == sources[other]
        assert apply(sources[component], [(b, a) for a, b in edits[component]]) == sources['base']
    seq = lambda s: re.findall(r'^  always @\(posedge clk.*?^  end', s, re.M | re.S)
    assert seq(sources['combined']) == seq(sources['base'])
    audits = {}
    for name, rel in [('integer', 'integer-rs1-priority-emitted-audit-v2'),
                      ('fp', 'fp-rs2-priority-2plus8-emitted-audit-v1')]:
        path = ART / rel / 'manifest.json'
        audit = json.loads(path.read_text())
        assert audit['candidate_sha256'] == FILES[name][1]
        assert audit['reference_sha256'] == FILES['base'][1]
        assert audit['actual_cones_freshly_match_artifacts']
        audits[name] = dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    OUT.mkdir(exist_ok=False)
    result = dict(source_sha256={name: sha for name, (_, sha) in FILES.items()},
        independent_component_audits=audits,
        independently_derived_contextual_diff_groups={'integer': 1, 'fp': 1},
        both_application_orders_exact=True, both_reversals_restore_parents=True,
        all_composed_equations_identical_to_independently_audited_components=True,
        no_other_source_changes=True, all_sequential_blocks_byte_identical=True,
        new_registers=0, new_cycles=0,
        qualification='Final retained CPU suite and full-core area/timing remain separate.')
    (OUT / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
