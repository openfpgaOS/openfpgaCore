#!/usr/bin/env python3
"""Compose two qualified cache timing cuts and equivalent GPU queue storage.

This revises compact LOD5 at the same seed2; CPU, clocks and constraints stay
unchanged. A separate independent review must pass before the one fit starts.
"""
import difflib
import json
import shutil
import sys
from pathlib import Path
import phase10_compact_lod5_fit as fit

ROOT, P = fit.ROOT, fit.P
BASE = P / 'compact-lod5-core-v1'
OUT = P / 'compact-lod5-core-v2'
GPU = 'src/fpga/common/gpu_core.v'
CACHE = 'src/fpga/common/gpu_color_depth_cache.sv'
TOP = 'src/fpga/targets/pocket/core_top.v'
GPU_SOURCE = P / 'gpu-queue-pack-v8/gpu_core.v'
CACHE_SOURCE = P / 'cache-read-stage-v2/gpu_color_depth_cache.sv'


def prepare():
    parent = fit.checked_stage(BASE)
    assert json.loads((BASE / 'result.json').read_text())['whole_core_fit_successful']
    assert json.loads((P / 'cache-read-stage-v2/protocol/result.json').read_text())['passed']
    gpu_review = json.loads((P / 'gpu-queue-review-v8/manifest.json').read_text())
    assert gpu_review['passed']
    assert gpu_review['candidate_sha256'] == fit.sha(GPU_SOURCE)
    evidence = [BASE / 'final-manifest.json',
                P / 'gpu-queue-review-v8/manifest.json',
                P / 'gpu-queue-map-v8/manifest.json',
                P / 'cache-read-stage-v2/manifest.json',
                P / 'cache-stage-lod5-coupled-v2/manifest.json',
                Path(__file__), Path(fit.__file__)]
    evidence_hashes = {str(p.relative_to(ROOT)): fit.sha(p) for p in evidence}
    top = (BASE / 'frozen' / TOP).read_text()
    old = '    .ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)'
    assert top.count(old) == 1
    top = top.replace(old, old + ',\n    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1)')
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    shutil.copyfile(GPU_SOURCE, OUT / 'frozen' / GPU)
    shutil.copyfile(CACHE_SOURCE, OUT / 'frozen' / CACHE)
    (OUT / 'frozen' / TOP).write_text(top)
    sources = {str(p.relative_to(OUT / 'frozen')): fit.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = [n for n, h in sources.items() if parent['source_sha256'][n] != h]
    assert sorted(changed) == sorted([GPU, CACHE, TOP])
    prep = dict(parent, scope=__doc__, source_sha256=sources,
                only_changed_files=changed, parent_prepared_sha256=fit.sha(BASE / 'prepared.json'),
                evidence_sha256=evidence_hashes,
                recipe_sha256=fit.sha(Path(fit.__file__)), stage_recipe_sha256=fit.sha(Path(__file__)))
    fit.save(OUT / 'prepared.json', prep)
    shutil.copyfile(__file__, OUT / 'stage-recipe.py')
    shutil.copyfile(fit.__file__, OUT / 'recipe.py')
    patch = ''.join(''.join(difflib.unified_diff(
        (BASE / 'frozen' / n).read_text().splitlines(True),
        (OUT / 'frozen' / n).read_text().splitlines(True),
        fromfile='compact-v1/' + n, tofile='compact-v2/' + n)) for n in changed)
    (OUT / 'integration.patch').write_text(patch)
    print(OUT)


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['fit']), 'prepare|fit'
    if sys.argv[1] == 'prepare':
        prepare()
    else:
        fit.OUT = OUT
        fit.fitrun()
