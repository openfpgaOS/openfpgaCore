#!/usr/bin/env python3
"""Read-only inventory of installed LC backannotation; no Quartus invocation."""
from pathlib import Path
import collections
import hashlib
import json
import shlex

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'lc-placement-feasibility-review-v1'
ORIGINAL = P / 'compact-lod5-core-v7/fit-seed2/jobs/pocket-os30-s2'
QSF = Path('src/fpga/targets/pocket/ap_core.qsf')
LC = P / 'placement-preservation-readonly-v1/lc' / QSF
NODES = P / 'eco-v7-local-placement-v1/baseline-nodes.tsv'
TARGET = 'core_top:ic|cpu_system:cpu|VexiiRiscv:cpu|WideOr109~0'
QUARTUS = Path('/home/alberto/altera_lite/25.1std/quartus')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def locations(lines):
    out = {}
    for line in lines:
        if line.startswith('set_location_assignment '):
            words = shlex.split(line)
            name, site = words[words.index('-to') + 1], words[1]
            assert name not in out
            out[name] = site
    return out


def main():
    assert not OUT.exists()
    OUT.mkdir()
    original = (ORIGINAL / QSF).read_text().splitlines()
    annotated = LC.read_text().splitlines()
    orig_other = [x for x in original if not x.startswith('set_location_assignment ')]
    anno_other = [x for x in annotated if not x.startswith('set_location_assignment ')]
    removed = [x for x in orig_other if x not in anno_other]
    assert removed == [
        'set_global_assignment -name OPTIMIZATION_MODE "HIGH PERFORMANCE EFFORT"',
        'set_global_assignment -name OPTIMIZATION_TECHNIQUE SPEED',
        'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION ON',
    ]
    assert [x for x in orig_other if x not in removed] == anno_other[:-1]
    assert anno_other[-1] == ''
    # Every removed earlier value is superseded in both QSFs.
    for key, value in [('OPTIMIZATION_MODE', 'BALANCED'),
                       ('OPTIMIZATION_TECHNIQUE', 'AREA'),
                       ('PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION', 'OFF'),
                       ('SEED', '2')]:
        prefix = 'set_global_assignment -name ' + key + ' '
        assert [x for x in original if x.startswith(prefix)][-1] == prefix + value
        assert [x for x in annotated if x.startswith(prefix)][-1] == prefix + value
    loc = locations(annotated)
    rows = [line.split('\t') for line in NODES.read_text().splitlines()]
    nodes = {row[1]: row[2] for row in rows}
    assert len(nodes) == len(rows) == 56374
    assert len(set(nodes.values())) == len(nodes)
    sites = collections.defaultdict(list)
    for name, site in loc.items():
        sites[site].append(name)
    direct = {name: site for name, site in loc.items() if name in nodes}
    assert all(nodes[name] == site for name, site in direct.items())
    assert set(sites) <= set(nodes.values())
    unpinned = {name: site for name, site in nodes.items() if site not in sites}
    assert all('feeder' in name for name in unpinned)
    assert sites[nodes[TARGET]] == [TARGET]
    assert loc[TARGET] == 'LABCELL_X50_Y32_N33'
    duplicates = {name: site for name, site in loc.items()
                  if 'duplicate' in name.lower() or 'syndup' in name.lower()}
    assert all(name in nodes for name in duplicates)
    copied = json.loads((P / 'placement-preservation-readonly-v1/copied-db.json').read_text())
    expected = copied['original_sha256']
    actual = {str(f.relative_to(ORIGINAL)): sha(f) for f in ORIGINAL.rglob('*') if f.is_file()}
    assert actual == expected and len(actual) == 677
    save('unpinned-feeders.json', unpinned)
    result = {
        'passed_readonly_inventory': True,
        'deployment_ready': False,
        'fitter_invoked': False,
        'full_placement_preservation_proven': False,
        'route_preservation_claimed': False,
        'original_job_files_unchanged': len(actual),
        'location_assignment_targets': len(loc),
        'direct_fitted_atom_matches': len(direct),
        'direct_location_mismatches': 0,
        'alias_targets': len(loc) - len(direct),
        'fitted_atoms': len(nodes),
        'assigned_physical_sites': len(sites),
        'unassigned_feeder_sites': len(unpinned),
        'unassigned_by_type': dict(collections.Counter(v.split('_')[0] for v in unpinned.values())),
        'fitted_duplicate_or_syndup_targets': len(duplicates),
        'duplicate_recreation_from_map_proven': False,
        'target': TARGET,
        'target_original_location': loc[TARGET],
        'target_has_no_colocated_alias': True,
        'removed_qsf_lines_are_overridden_earlier_values': removed,
        'effective_settings_seed_source_clock_changes': False,
        'recommended_scope': 'One LC-constrained ordinary timing-driven fit on a fresh original-job copy; no resynthesis. Post-fit census must measure actual preservation.',
        'required_postfit_checks': [
            'Reject fit errors and review every unmatched/ignored/conflicting assignment and the Ignored Assignments report.',
            'Compare full named atom/location census; separately report feeders, missing/recreated clones, and every moved constrained atom.',
            'Check exact target, original launch/sink, source/QSF/SDC, mapped database and effective seed/clock binding.',
            'Re-run complete slow/fast setup, hold, recovery/removal and clock constraints; no preserved-route claim.',
        ],
        'diagnostic_ids': [15705, 171081, 171167, 12871, 170183, 173001],
    }
    save('result.json', result)
    (OUT / 'REPORT.txt').write_text(
        'LC backannotation is a supported, successful assignment export on this installed Cyclone V flow.\n'
        'The legacy fitter consumes QSF assignments and supports timing-driven ordinary place/route without a map rerun.\n'
        'The export is not a complete physical freeze: 2,888 generated feeder sites have no location assignments.\n'
        'All directly named fitted atoms agree; generated aliases share legal baseline sites. The intended LUT has one exact assignment.\n'
        'Fitted duplicate names are present, but their recreation/assignment resolution from the mapped DB is not yet demonstrated.\n'
        'Use the original non-location QSF text, replace only the LC assignment set, then change only the selected target site.\n'
        'A run can be authorized as a bounded feasibility fit, not as already-proven placement or route preservation.\n'
        'No Quartus invocation, production mutation, or candidate job mutation was performed by this review.\n')
    evidence = [Path(__file__), ORIGINAL / QSF, LC, NODES,
                P / 'placement-preservation-readonly-v1/copied-db.json',
                P / 'placement-preservation-readonly-v1/lc-result.json',
                P / 'placement-preservation-readonly-v1/lc.log',
                P / 'placement-preservation-readonly-v1/cdb-back-annotate-help.txt',
                QUARTUS / 'common/flow/qaf/qfit2.options.hlp',
                QUARTUS / 'common/tcl/internal/qfit2_legacy_fmain_fitter_flow.tcl',
                QUARTUS / 'common/misc/msg_index.xml',
                OUT / 'result.json', OUT / 'unpinned-feeders.json', OUT / 'REPORT.txt']
    save('manifest.json', {'passed': True, 'deployment_ready': False,
                          'evidence_sha256': {str(f.relative_to(ROOT)) if f.is_relative_to(ROOT) else str(f): sha(f) for f in evidence}})
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
