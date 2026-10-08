#!/usr/bin/env python3
"""Independent source/QSF/map review only; no synthesis, fit or simulation."""
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'compact-lod5-core-v7'
CAND = P / 'compact-lod5-v7-lanefan4-v1'
OUT = P / 'v7-lanefan4-independent-review-v1'
QSF = 'src/fpga/targets/pocket/ap_core.qsf'
ASSIGNMENT = 'set_instance_assignment -name MAX_FANOUT 4 -to ic|cpu|cpu|execute_ctrl3_up_LANE_SEL_lane0'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    assert not OUT.exists()
    evidence = {}
    checked = 0
    records = [CAND / 'manifest.json', CAND / 'prepared.json',
               BASE / 'prepared.json', BASE / 'independent-review.json', BASE / 'final-manifest.json']
    for path in records:
        for name, digest in read(path).get('evidence_sha256', {}).items():
            assert sha(ROOT / name) == digest, name
            checked += 1
        evidence[str(path.relative_to(ROOT))] = sha(path)
    prep, parent = read(CAND / 'prepared.json'), read(BASE / 'prepared.json')
    assert prep['source_sha256'] == parent['source_sha256'] and len(prep['source_sha256']) == 115
    assert prep['seed'] == prep['saved_seed'] == 2 and not prep['seed_sweep']
    inherited = read(BASE / 'independent-review.json')
    assert inherited['passed_for_resource_fit'] and inherited['component_and_coupled_proofs_complete']
    assert inherited['prepared_sha256'] == sha(BASE / 'prepared.json')
    for name, digest in prep['source_sha256'].items():
        assert sha(CAND / 'frozen' / name) == digest, name
        if name != QSF:
            assert sha(CAND / 'map-core/job' / name) == digest, name
    oldqsf = BASE / 'fit-seed2/jobs/pocket-os30-s2' / QSF
    newqsf = CAND / 'map-core/job' / QSF
    assert newqsf.read_bytes() == oldqsf.read_bytes() + ('\n' + ASSIGNMENT + '\n').encode()
    assert re.findall(r'^set_global_assignment -name SEED (\d+)$', newqsf.read_text(), re.M) == ['2']
    inv = read(CAND / 'map-core/invocation.json')
    complete = read(CAND / 'map-core/completion.json')
    assert inv['command'][-2:] == ['quartus_map', 'ap_core']
    assert not inv['fitter_invoked'] and complete['returncode'] == 0 and not complete['fitter_invoked']
    assert inv['qsf_sha256'] == sha(newqsf) and inv['prepared_sha256'] == sha(CAND / 'prepared.json')
    assert complete['log_sha256'] == sha(CAND / 'map-core/map.log')
    newmap = newqsf.parent / 'output_files/ap_core.map.rpt'
    oldmap = oldqsf.parent / 'output_files/ap_core.map.rpt'
    text = newmap.read_text()
    duplicated = re.findall(r';\s*core_top:ic\|cpu_system:cpu\|VexiiRiscv:cpu\|execute_ctrl3_up_LANE_SEL_lane0\s*;\s*(\d+)\s*;\s*(\d+)\s*;', text)
    assert duplicated == [('4', '3')]
    assert 'Info (18058): Inserted 3 logic cells for Maximum Fan-Out assignment' in text
    assert 'Analysis & Synthesis was successful' in text
    rows = {}
    for label, report in [('parent', oldmap), ('candidate', newmap)]:
        lines = report.read_text().splitlines()
        rows[label] = {}
        for name, expression in [('top', r';\s*\|apf_top\s*;'),
                                 ('cpu', r';\s*\|VexiiRiscv:cpu\|\s*;'),
                                 ('gpu', r';\s*\|gpu_core:gpu\|\s*;'),
                                 ('geometry', r';\s*\|geometry_load_alias:geometry_load\|\s*;')]:
            line = next(l for l in lines if re.search(expression, l))
            cols = [x.strip() for x in line.split(';')]
            rows[label][name] = [int(cols[i].split()[0].replace(',', '')) for i in (2, 3, 4, 5)]
    deltas = {n: [b-a for a,b in zip(rows['parent'][n], rows['candidate'][n])] for n in rows['parent']}
    assert deltas == {'top': [-2, 3, 0, 0], 'cpu': [-2, 3, 0, 0], 'gpu': [0, 0, 0, 0], 'geometry': [0, 0, 0, 0]}
    for path in [newqsf, oldqsf, newmap, oldmap, CAND / 'map-core/result.json',
                 CAND / 'map-core/invocation.json', CAND / 'map-core/completion.json',
                 CAND / 'map-core/map.log', ROOT / 'tools/experiments/os30_fps25_20261005/phase10_v7_lane_fanout.py', Path(__file__)]:
        evidence[str(path.relative_to(ROOT))] = sha(path)
    result = dict(
        passed=True, passed_for_resource_fit=True, deployment_ready=False,
        component_and_coupled_proofs_complete=True, exact_source_binding_passed=True,
        prepared_sha256=sha(CAND / 'prepared.json'), parent_review_sha256=sha(BASE / 'independent-review.json'),
        saved_seed=2, effective_seed=2, seed_sweep=False, exactly_one_fit=True,
        source_files_checked=115, unchanged_generated_non_qsf_files=114,
        inherited_evidence_hashes_checked=checked, assigned_max_fanout=4,
        actual_created_register_copies=3, metrics_order=['ALUT', 'register', 'memory_bits', 'DSP'],
        raw_map_counts=rows, raw_map_deltas=deltas,
        functional_scope='Byte-identical RTL and inherited native/coupled proof. Synthesis duplicate registers preserve logical function; no new functional source or cycle change.',
        physical_scope='One seed-2 fit may measure placement/timing. Synthesis alone proves neither area fit nor timing gain. CPU cache, clocks, device and other options are unchanged.',
        caveat='SynDup names are absent, but the explicit Registers Duplicated to Honor Maximum Fanout Requirements table and Info 18058 prove the requested target was honored.',
        evidence_sha256=evidence)
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    (OUT / 'review.json').write_text(json.dumps(result, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text(
        'PASS for one private seed-2 resource/timing fit, not deployment.\n'
        'All 115 frozen source files and 114 generated non-QSF files match compact v7 exactly.\n'
        'The actual generated QSF adds only MAX_FANOUT 4 on the selected ctrl3 lane register.\n'
        'The raw synthesis report confirms three duplicates: CPU/top -2 ALUT, +3 registers; RAM/DSP unchanged.\n'
        'GPU and geometry mapped counts are unchanged. No simulation, synthesis, fitter or ECO was run by this reviewer.\n'
        'Timing and fitted ALM/LAB changes remain unmeasured. All original constraints and the 100 MHz configured clock remain.\n')
    for path in OUT.iterdir():
        if path.is_file(): evidence[str(path.relative_to(ROOT))] = sha(path)
    (OUT / 'manifest.json').write_text(json.dumps(dict(passed=True, passed_for_resource_fit=True,
        deployment_ready=False, evidence_sha256=evidence), indent=2) + '\n')
    print(json.dumps({'review_sha256':sha(OUT/'review.json'), 'manifest_sha256':sha(OUT/'manifest.json')}))


if __name__ == '__main__':
    main()
