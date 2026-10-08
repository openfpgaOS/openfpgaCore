#!/usr/bin/env python3
"""One settings-only seed2 fit of compact v5 with physical synthesis enabled."""
import difflib
import json
import shutil
import sys
from pathlib import Path
import phase10_compact_lod5_fit as f

ROOT, P = f.ROOT, f.P
BASE = P / 'compact-lod5-core-v5'
OUT = P / 'compact-lod5-physical-v1'
QSF = 'src/fpga/targets/pocket/ap_core.qsf'


def prepare():
    parent = f.checked_stage(BASE)
    review = json.loads((BASE / 'independent-review.json').read_text())
    assert review['passed_for_resource_fit']
    assert review['component_and_coupled_proofs_complete']
    for name, digest in review['evidence_sha256'].items():
        assert f.sha(ROOT / name) == digest, name
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    qsf = OUT / 'frozen' / QSF
    text = qsf.read_text()
    changes = {
        'set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC OFF':
        'set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC ON',
        'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF':
        'set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION ON',
    }
    for old, new in changes.items():
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    qsf.write_text(text)
    sources = {str(p.relative_to(OUT / 'frozen')): f.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = sorted(n for n, h in sources.items() if parent['source_sha256'][n] != h)
    assert changed == [QSF], changed
    evidence = [BASE / 'final-manifest.json', BASE / 'independent-review.json',
                P / 'compact-timing-audit-v5/manifest.json',
                P / 'compact-v5-read-port-options-audit/manifest.json',
                Path(__file__), Path(f.__file__)]
    f.save(OUT / 'prepared.json', dict(parent, scope=__doc__,
        source_sha256=sources, only_changed_files=changed,
        parent_prepared_sha256=f.sha(BASE / 'prepared.json'),
        evidence_sha256={str(p.relative_to(ROOT)): f.sha(p) for p in evidence},
        recipe_sha256=f.sha(Path(f.__file__)), stage_recipe_sha256=f.sha(Path(__file__)),
        rtl_byte_identical_to_parent=True,
        optimization_changes=changes, requires_final_component_and_coupled_review_before_fit=False))
    shutil.copyfile(__file__, OUT / 'stage-recipe.py')
    shutil.copyfile(f.__file__, OUT / 'recipe.py')
    (OUT / 'integration.patch').write_text(''.join(difflib.unified_diff(
        (BASE / 'frozen' / QSF).read_text().splitlines(True), text.splitlines(True),
        fromfile='compact-v5/' + QSF, tofile='physical-v1/' + QSF)))
    print(OUT)


def fitrun():
    f.OUT = OUT
    f.fitrun()


if __name__ == '__main__':
    {'prepare': prepare, 'fit': fitrun}[sys.argv[1]]()
