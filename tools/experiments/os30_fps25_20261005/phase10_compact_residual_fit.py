#!/usr/bin/env python3
"""Compact v6: exact CPU stall/forwarding and GPU clamp/attribute path fixes."""
import difflib
import json
import shutil
import sys
from pathlib import Path
import phase10_compact_datapath_fit as m

ROOT, P, f = m.ROOT, m.P, m.fit
BASE = P / 'compact-lod5-core-v5'
OUT = P / 'compact-lod5-core-v6'
SOURCES = {
    f.CPU: P / 'cpu-final-paths-v1/VexiiRiscv.v',
    'src/fpga/common/gpu_core.v': P / 'gpu-attribute-v13/gpu_core.v',
}
PROOFS = [P / 'cpu-final-paths-v1/manifest.json',
          P / 'gpu-attribute-review-v13/manifest.json']


def prepare():
    parent = f.checked_stage(BASE)
    for proof in PROOFS:
        data = json.loads(proof.read_text())
        for n, h in data.get('files', {}).items():
            assert f.sha(proof.parent / n) == h, n
        for n, h in data.get('evidence_sha256', {}).items():
            assert f.sha(ROOT / n) == h, n
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    for n, src in SOURCES.items():
        shutil.copyfile(src, OUT / 'frozen' / n)
    sources = {str(p.relative_to(OUT / 'frozen')): f.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = sorted(n for n, h in sources.items() if parent['source_sha256'][n] != h)
    assert changed == sorted(SOURCES), changed
    evidence = [BASE / 'final-manifest.json', BASE / 'independent-review.json',
                P / 'compact-timing-audit-v5/manifest.json', *PROOFS,
                P / 'load-submit-coupled-v1/manifest.json',
                P / 'gpu-clamp-review-v12/manifest.json',
                Path(__file__), Path(m.__file__), Path(f.__file__)]
    f.save(OUT / 'prepared.json', dict(parent, scope=__doc__, source_sha256=sources,
        only_changed_files=changed, parent_prepared_sha256=f.sha(BASE / 'prepared.json'),
        evidence_sha256={str(p.relative_to(ROOT)): f.sha(p) for p in evidence},
        recipe_sha256=f.sha(Path(f.__file__)), stage_recipe_sha256=f.sha(Path(__file__)),
        requires_final_component_and_coupled_review_before_fit=True))
    shutil.copyfile(__file__, OUT / 'stage-recipe.py')
    shutil.copyfile(f.__file__, OUT / 'recipe.py')
    (OUT / 'integration.patch').write_text(''.join(''.join(difflib.unified_diff(
        (BASE / 'frozen' / n).read_text().splitlines(True),
        (OUT / 'frozen' / n).read_text().splitlines(True),
        fromfile='compact-v5/' + n, tofile='compact-v6/' + n)) for n in changed))
    print(OUT)


def map_only():
    m.BASE, m.OUT = BASE, OUT
    m.map_only()


def fitrun():
    assert json.loads((OUT / 'map-core/result.json').read_text())['passed_map']
    review = json.loads((OUT / 'independent-review.json').read_text())
    assert review['passed_for_resource_fit']
    assert review['component_and_coupled_proofs_complete']
    f.OUT = OUT
    f.fitrun()


if __name__ == '__main__':
    {'prepare': prepare, 'map_only': map_only, 'fit': fitrun}[sys.argv[1]]()
