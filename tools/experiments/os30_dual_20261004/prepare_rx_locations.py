#!/usr/bin/env python3
"""Prepare a private SDRAM receive-placement proposal from observed fit locations."""
from pathlib import Path
import collections
import csv
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[3]
CAMPAIGN = ROOT / 'docs/measurements/os30-dual-20261004'
SOURCE = CAMPAIGN / 'receive-location-audit-v1/registers.tsv'
OUTPUT = CAMPAIGN / 'receive-lab-proposal-v2'

# Separate the four enable groups. Each chosen LAB holds eight constrained
# registers with one shared clock/enable; the remaining slots stay available.
WEST = (3, 5, 6, 7, 8, 11, 12, 14)
EAST = (0, 1, 2, 4, 9, 10, 13, 15)
GROUPS = (
    ('burst_data', 0, WEST, 39, 42),
    ('burst_data', 16, WEST, 39, 43),
    ('word_q', 0, WEST, 40, 42),
    ('word_q', 16, WEST, 41, 43),
    ('burst_data', 0, EAST, 47, 42),
    ('burst_data', 16, EAST, 47, 43),
    ('word_q', 0, EAST, 48, 42),
    ('word_q', 16, EAST, 48, 43),
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rows = list(csv.DictReader(SOURCE.open(), delimiter='\t'))
    by_name = {row['name']: row['location'] for row in rows}
    observed = collections.defaultdict(list)
    for row in rows:
        match = re.fullmatch(r'FF_X(\d+)_Y(\d+)_N(\d+)', row['location'])
        if match:
            observed[(int(match[1]), int(match[2]))].append(row)
    proposal, checks, proof, destinations = [], [], [], set()
    for bus, offset, bits, x, y in GROUPS:
        assert len(bits) == 8 and observed[(x, y)]
        group = {'bus': bus, 'half': offset // 16, 'bits': [],
                 'location': f'LAB_X{x}_Y{y}', 'new_constrained_registers': 8,
                 'observed_register_count': len(observed[(x, y)]),
                 'coordinate_evidence': observed[(x, y)][0]}
        for bit in bits:
            index = bit + offset
            name = f'core_top:ic|io_sdram:isr0|{bus}[{index}]'
            assert name in by_name and name not in destinations
            destinations.add(name)
            dq = 'core_top:ic|io_sdram:isr0|altddio_in:u_dq_ddio_in|ddio_in_gbf:auto_generated|dataout_l[' + str(bit) + ']'
            assert dq in by_name
            target = f'ic|isr0|{bus}[{index}]'
            # QSF's assignment reader preserves Tcl braces literally; use the
            # canonical plain spelling when appending directly to a QSF file.
            proposal.append(f'set_location_assignment LAB_X{x}_Y{y} -to {target}')
            checks.append(f'if {{[get_location_assignment -to {{{target}}}] ne "LAB_X{x}_Y{y}"}} '
                          f'{{error "Direct-QSF read mismatch for {bus} bit {index}"}}')
            group['bits'].append({'index': index, 'source_ddio': by_name[dq],
                                  'old_receiver': by_name[name]})
        proof.append(group)
    assert len(destinations) == 64
    OUTPUT.mkdir(exist_ok=False)
    qsf = OUTPUT / 'sdram_rx_labs.qsf'
    qsf.write_text('# Private physical-placement proposal; clock, RTL and SDC unchanged.\n'
                   '# Eight registers per LAB, grouped by bus half/shared capture enable.\n'
                   + '\n'.join(proposal) + '\n')
    manifest = {'scope': 'Proposal and parser check only; full fitted applicability/timing not yet verified',
                'device': '5CEBA4F23C8', 'registers': 64, 'labs': 8,
                'source': str(SOURCE.relative_to(ROOT)), 'source_sha256': digest(SOURCE),
                'qsf_sha256': digest(qsf), 'groups': proof,
                'lab_hardware_capacity': {'alms': 10, 'registers': 40},
                'existing_ff_count_is_not_available_space': True,
                'no_reserved_region': True, 'no_logiclock_assignments': True,
                'fit_and_postfit_timing_required': True}
    (OUTPUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (OUTPUT / 'rx_location_probe.qpf').write_text('PROJECT_REVISION = "rx_location_probe"\n')
    (OUTPUT / 'rx_location_probe.qsf').write_text(
        'set_global_assignment -name FAMILY "Cyclone V"\n'
        'set_global_assignment -name DEVICE 5CEBA4F23C8\n'
        'set_global_assignment -name TOP_LEVEL_ENTITY unused\n' + qsf.read_text())
    (OUTPUT / 'probe.tcl').write_text(
        'package require ::quartus::project\n'
        'project_open rx_location_probe\n' + '\n'.join(checks) + '\n'
        'puts "PASS: all64 destinations matched exactly after direct QSF load"\n'
        'project_close\n')
    print(qsf)


if __name__ == '__main__':
    main()
