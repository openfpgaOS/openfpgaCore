#!/usr/bin/env python3
"""Read installed ECO implementation and existing capability evidence; no Quartus run."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'placement-preservation-readonly-v1'
OUT = P / 'v7-eco-api-review-v1'
INSTALL = Path('/home/alberto/altera_lite/25.1std/quartus')
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert not OUT.exists()
    old = json.loads((BASE / 'manifest.json').read_text())
    for name, digest in old['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
    planner = INSTALL / 'common/tcl/internal/init/chip_planner.tcl'
    source = planner.read_text()
    update = source.split('proc update_node_loc {} {', 1)[1].split('proc is_carry', 1)[0]
    assert 'set_node_info -node $id -info "Location Index" "N/A"' in update
    assert '\n\t\t\tcheck_netlist_and_save\n' in update
    assert 'return 0' in update and 'ECO fitter will try to assign a valid location' in update
    help_path = INSTALL / 'common/tcl/internal/init/chip_planner.cmds.hlp'
    help_text = help_path.read_text()
    assert 'Returns 1, if the netlist is legal, and saves the netlist' in help_text
    assert 'Returns 0, if the netlist is illegal, and restores' in help_text
    assert 'Creates a Tcl script showing the ECO changes for\nHardCopy II' in help_text
    paths = [BASE / 'manifest.json', BASE / 'eco-help.log', BASE / 'flow-help.txt',
             planner, help_path, INSTALL / 'common/tcl/internal/init/chip_editor.tcl',
             INSTALL / 'common/tcl/internal/init/flow.cmds.hlp',
             INSTALL / 'common/tcl/internal/qfit2_default_script.tcl',
             INSTALL / 'common/tcl/internal/qfit2_legacy_fmain_fitter_flow.tcl',
             INSTALL / 'common/tcl/internal/eco_flow_prepare.tcl',
             INSTALL / 'common/tcl/internal/eco_flow_finalize.tcl',
             INSTALL / 'common/misc/msg_index.xml', Path(__file__)]
    evidence = {str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p): sha(p) for p in paths}
    result = dict(
        passed_read_only_api_review=True,
        candidate_move_reviewed=False,
        passed_for_eco_execution=False,
        deployment_ready=False,
        quartus_or_netlist_mutations_performed=False,
        existing_evidence_hashes_verified=len(old['evidence_sha256']),
        findings=[
            'update_node_loc calls check_netlist_and_save internally, discards its Boolean result, and returns 0.',
            'It first clears every listed non-carry location and then reapplies all listed locations; a full export is not a minimal one-cell mutation.',
            'Invalid or conflicting destinations can be delegated to ECO fitter relocation rather than rejected by update_node_loc.',
            'Check/save has separate Tcl error status and numeric legal/save result; both need checking.',
            'The installed API does not promise preservation of every untouched route.',
            'get_node_loc exports lcell locations only; it does not establish logical netlist equivalence.',
            'The migration-script API is documented for Stratix II to HardCopy II, not a Cyclone V replay recipe.',
            'Fresh assembler output and all-corner timing are required after a saved physical edit; unchanged old output files are not candidate evidence.'
        ],
        required_candidate_gates=[
            'Exact baseline 677-file job and 115-file source inventories plus compiler identity, device, seed, QSF and every SDC input.',
            'One explicit original node/site and selected legal destination, checking clock/control compatibility and occupancy.',
            'Canonical before/after logical signatures for every atom: type, configuration/equation, initialization and named-port connectivity, including FF clocks/enables/reset and RAM/DSP/PLL/IO.',
            'Fail on illegal save, warning-driven relocation, unexpected atom changes, or unexpected location changes.',
            'Canonical destination-port route comparison; report changed nets and do not claim preservation beyond measured evidence.',
            'Fresh setup, hold, recovery, removal, minimum pulse width, clocks and unconstrained-path checks at both slow and the fast operating conditions.',
            'Strict greater-than-90 MHz comparative target at both slow corners, while retaining configured 100 MHz and reporting its negative slack honestly.',
            'Fresh assembly bound to the modified database and a replayable physical-edit recipe; unchanged RTL alone does not reproduce an ECO bitstream.'
        ],
        evidence_sha256=evidence,
    )
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    (OUT / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text(
        'Installed Cyclone V ECO API review only; no edit has been approved or executed by this review.\n\n'
        + '\n'.join(result['findings']) + '\n\nCandidate requirements:\n'
        + '\n'.join(result['required_candidate_gates']) + '\n')
    for p in OUT.iterdir():
        if p.is_file():
            evidence[str(p.relative_to(ROOT))] = sha(p)
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        passed_read_only_api_review=True, passed_for_eco_execution=False,
        deployment_ready=False, evidence_sha256=evidence), indent=2) + '\n')
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
