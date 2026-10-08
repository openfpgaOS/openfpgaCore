#!/usr/bin/env python3
"""One synthesis-only screen of LOD8 over compact-v5; no fitter entry point."""
from pathlib import Path
import json
import shutil
import sys
import phase10_compact_datapath_fit as m

ROOT, P, f = m.ROOT, m.P, m.fit
BASE = P / 'compact-lod5-core-v5'
OUT = P / 'lod8-canonical-core-v1'
COMP = P / 'lod8-canonical-alias-v1'
SOURCES = {
    'src/fpga/common/phase10_load_alias.v': COMP / 'frozen/phase10_load_alias.v',
    'src/fpga/common/phase10_bram_alias.v': COMP / 'frozen/phase10_bram_alias.v',
    'src/fpga/targets/pocket/core_top.v': COMP / 'core_top.v',
}


def prepare():
    parent = f.checked_stage(BASE)
    for name in ['test/result.json', 'numeric/result.json']:
        assert json.loads((COMP / name).read_text())['passed']
    for name, digest in json.loads((COMP / 'sources.json').read_text()).items():
        assert f.sha(COMP / 'frozen' / name) == digest, name
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    for name, source in SOURCES.items():
        shutil.copy2(source, OUT / 'frozen' / name)
    actual = {str(p.relative_to(OUT / 'frozen')): f.sha(p)
              for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = sorted(n for n, h in actual.items() if parent['source_sha256'][n] != h)
    assert changed == sorted(SOURCES)
    evidence = [BASE / 'final-manifest.json', COMP / 'sources.json', COMP / 'registers.json',
                COMP / 'test/result.json', COMP / 'numeric/result.json', Path(__file__),
                Path(m.__file__), Path(f.__file__)]
    f.save(OUT / 'prepared.json', dict(parent, source_sha256=actual,
        scope=__doc__, only_changed_files=changed, parent_prepared_sha256=f.sha(BASE / 'prepared.json'),
        deployment_ready=False, full_fit_authorized=False,
        evidence_sha256={str(p.relative_to(ROOT)): f.sha(p) for p in evidence}))
    shutil.copy2(__file__, OUT / 'recipe.py')
    print(OUT)


def entities(path):
    text = path.read_text(errors='replace')
    out = {}
    for line in text.split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
        row = [v.strip() for v in line.split(';')][1:-1]
        if len(row) >= 10 and row[-2] in ['apf_top', 'VexiiRiscv', 'gpu_core', 'cpu_system',
                'geometry_bram_alias', 'geometry_load_alias', 'geometry_raw_load', 'geometry_transform_light']:
            out[row[-2]] = dict(aluts=row[1], registers=row[2], memory_bits=row[3], dsp=row[4], path=row[-3])
    return out


def map_only():
    m.BASE, m.OUT, m.entities = BASE, OUT, entities
    m.map_only()


if __name__ == '__main__':
    {'prepare': prepare, 'map_only': map_only}[sys.argv[1]]()
