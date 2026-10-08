#!/usr/bin/env python3
"""Map-only LOD5 span-resume area screen. No fitter, timing run, or deployment."""
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'lod5-cram-span-core-v1'
VARIANT = P / 'span-lookahead-v6'
DEST = VARIANT / 'map-core'
GPU = 'src/fpga/common/gpu_core.v'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    prep = json.loads((BASE / 'prepared.json').read_text())
    for name, digest in prep['source_sha256'].items():
        assert sha(BASE / 'frozen' / name) == digest, name
    variant = json.loads((VARIANT / 'prepared.json').read_text())
    assert sha(VARIANT / 'gpu_core.v') == variant['source_sha256']
    assert prep['source_sha256'][GPU] == json.loads(
        (P / 'span-lookahead-v5/prepared.json').read_text())['parent_sha256']
    assert variant['parent_sha256'] == json.loads(
        (P / 'span-lookahead-v5/prepared.json').read_text())['source_sha256']
    DEST.mkdir(exist_ok=False)
    (DEST / 'recipe.py').write_bytes(Path(__file__).read_bytes())
    shutil.copytree(BASE / 'frozen', DEST / 'frozen')
    shutil.copy2(VARIANT / 'gpu_core.v', DEST / 'frozen' / GPU)
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as fit
    row = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text())[0])
    assert row['seed'] == 2 and row['variant'] == 'os30'
    directory, project = fit.prepare(DEST / 'frozen', DEST / 'job', row, 2, 4)
    cmd = fit.docker(DEST, 'pocket', directory, ['quartus_map', project])
    with (DEST / 'invocation.json').open('x') as f:
        json.dump(dict(command=cmd, parent_prepared_sha256=sha(BASE / 'prepared.json'),
                       variant_prepared_sha256=sha(VARIANT / 'prepared.json'),
                       recipe_sha256=sha(Path(__file__))), f, indent=2)
        f.write('\n')
    with (DEST / 'map.log').open('x') as f:
        subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
    report = directory / 'output_files/ap_core.map.rpt'
    report_text = report.read_text(errors='replace')
    entities = []
    for line in report_text.split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
        r = [x.strip() for x in line.split(';')][1:-1]
        if len(r) >= 10 and r[-2] in ['gpu_core', 'apf_top', 'geometry_load_alias', 'VexiiRiscv']:
            entities.append(dict(entity=r[-2], aluts=r[1], registers=r[2], memory_bits=r[3],
                                 dsp=r[4], path=r[-3]))
    result = dict(passed_map=True, private_only=True, fitted=False, deployed=False,
                  only_changed=GPU, source_sha256=variant['source_sha256'],
                  entities=entities, report_sha256=sha(report),
                  invocation_sha256=sha(DEST / 'invocation.json'))
    (DEST / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
