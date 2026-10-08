#!/usr/bin/env python3
"""Synthesis-only resource screen of the exact SDRAM address selector."""
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
import phase10_compact_lod5_fit as f
from phase10_lod5_queues_map import entities

ROOT, P = f.ROOT, f.P
BASE = P / 'compact-lod5-core-v3'
OUT = P / 'sdram-address-map-v1'
COMP = P / 'sdram-address-mux-v1'
REL = 'src/fpga/targets/pocket/io_sdram.v'


def main():
    parent = f.checked_stage(BASE)
    for proof in [COMP / 'manifest.json', COMP / 'bank-test/manifest.json']:
        for n, h in json.loads(proof.read_text()).items():
            assert f.sha(proof.parent / n) == h, n
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    shutil.copy2(COMP / 'io_sdram.v', OUT / 'frozen' / REL)
    shutil.copy2(__file__, OUT / 'recipe.py')
    sources = {str(p.relative_to(OUT / 'frozen')): f.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    assert [n for n, h in sources.items() if parent['source_sha256'][n] != h] == [REL]
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as sweep
    row = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text())[0])
    job, project = sweep.prepare(OUT / 'frozen', OUT / 'job', row, 2, 4)
    cmd = sweep.docker(OUT, 'pocket', job, ['quartus_map', project])
    f.save(OUT / 'invocation.json', dict(command=cmd, source_sha256=sources,
        only_changed_file=REL, fitter_invoked=False,
        evidence_sha256={str(p.relative_to(ROOT)): f.sha(p) for p in
            [COMP / 'manifest.json', COMP / 'bank-test/manifest.json', BASE / 'final-manifest.json']}))
    with (OUT / 'map.log').open('x') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    baseline = next((BASE / 'fit-seed2/jobs').rglob('ap_core.map.rpt'))
    candidate = job / 'output_files/ap_core.map.rpt'
    a, b = entities(baseline), entities(candidate)
    delta = {n: {k: int(b[n][k].split()[0].replace(',', '')) -
                   int(a[n][k].split()[0].replace(',', ''))
                 for k in ['aluts', 'registers', 'memory_bits', 'dsp']}
             for n in sorted(a.keys() & b.keys())}
    f.save(OUT / 'result.json', dict(passed_map=True, full_fit_attempted=False,
        baseline=a, candidate=b, delta=delta,
        baseline_report_sha256=f.sha(baseline), candidate_report_sha256=f.sha(candidate)))
    f.save(OUT / 'manifest.json', {str(p.relative_to(ROOT)): f.sha(p) for p in
        [OUT / 'invocation.json', OUT / 'result.json', OUT / 'recipe.py', OUT / 'map.log', candidate]})
    print(json.dumps(delta, indent=2))


if __name__ == '__main__':
    main()
