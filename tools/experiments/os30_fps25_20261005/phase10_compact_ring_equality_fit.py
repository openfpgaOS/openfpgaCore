#!/usr/bin/env python3
"""Compact v11: balance the GPU ring-pointer equality with parallel leaf comparisons."""
import difflib
import json
import shutil
import sys
from pathlib import Path
import phase10_compact_datapath_fit as m

ROOT, P, f = m.ROOT, m.P, m.fit
BASE = P / 'compact-lod5-core-v10'
OUT = P / 'compact-lod5-core-v11'
SOURCES = {'src/fpga/common/gpu_core.v': P / 'gpu-ring-equality-v16/gpu_core.v'}
PROOFS = [P / 'gpu-ring-equality-v16/prepared.json',
          P / 'gpu-ring-equality-v16/literal/result.json']


def prepare():
    literal = json.loads(PROOFS[1].read_text())
    assert literal['passed'] and literal['all_pointer_pairs'] == 16777216
    assert literal['candidate_sha256'] == f.sha(next(iter(SOURCES.values())))
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
                P / 'compact-timing-audit-v10/manifest.json', *PROOFS,
                P / 'load-submit-coupled-v1/manifest.json',
                P / 'load-submit-delivery-v1/manifest.json',
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
        fromfile='compact-v10/' + n, tofile='compact-v11/' + n)) for n in changed))
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
