#!/usr/bin/env python3
"""Compact v14: final fetch-ready factor alone over the smaller v7 core."""
import difflib
import json
import shutil
import sys
from pathlib import Path

import phase10_compact_datapath_fit as m

ROOT, P, f = m.ROOT, m.P, m.fit
BASE = P / 'compact-lod5-core-v7'
OUT = P / 'compact-lod5-core-v14'
COMPONENT = P / 'cpu-fetch-v7-v12'
SOURCES = {f.CPU: COMPONENT / 'VexiiRiscv.v'}


def prepare():
    parent = f.checked_stage(BASE)
    component = json.loads((COMPONENT / 'prepared.json').read_text())
    literal = json.loads((COMPONENT / 'proof/result.json').read_text())
    assert literal['passed']
    assert component['candidate_sha256'] == f.sha(SOURCES[f.CPU])
    assert parent['source_sha256'][f.CPU] == '8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
    # The checked recipe and literal source proof permit synthesis while the
    # native CPU and reproducible-generator checks finish. Placement requires
    # their independently reviewed manifests and the exact normal-build closure.
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    for name, source in SOURCES.items():
        shutil.copyfile(source, OUT / 'frozen' / name)
    sources = {str(p.relative_to(OUT / 'frozen')): f.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    assert set(sources) == set(parent['source_sha256'])
    changed = sorted(n for n, h in sources.items() if parent['source_sha256'][n] != h)
    assert changed == [f.CPU]
    evidence = [BASE / 'final-manifest.json', BASE / 'independent-review.json',
                P / 'compact-timing-audit-v7/manifest.json',
                COMPONENT / 'prepared.json', COMPONENT / 'proof/result.json',
                COMPONENT / 'recipe.py', COMPONENT / 'candidate.patch',
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
        fromfile='compact-v7/' + n, tofile='compact-v14/' + n)) for n in changed))
    print(OUT)


def map_only():
    m.BASE, m.OUT = BASE, OUT
    m.map_only()


def fitrun():
    assert json.loads((OUT / 'map-core/result.json').read_text())['passed_map']
    review = json.loads((OUT / 'independent-review.json').read_text())
    assert review['passed_for_resource_fit']
    assert review['component_and_coupled_proofs_complete']
    f.BASE, f.OUT = BASE, OUT
    f.fitrun()


if __name__ == '__main__':
    {'prepare': prepare, 'map_only': map_only, 'fit': fitrun}[sys.argv[1]]()
