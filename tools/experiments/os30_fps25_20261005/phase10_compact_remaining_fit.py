#!/usr/bin/env python3
"""Compact v4: measured decoder, cache-write, GPU and SDRAM timing cones."""
import difflib
import json
import shutil
import sys
from pathlib import Path
import phase10_compact_datapath_fit as m

ROOT, P, f = m.ROOT, m.P, m.fit
BASE = P / 'compact-lod5-core-v3'
OUT = P / 'compact-lod5-core-v4'
TOP = 'src/fpga/targets/pocket/core_top.v'
SOURCES = {
    f.CPU: P / 'cpu-rvc-bits-v2/VexiiRiscv.v',
    'src/fpga/common/gpu_core.v': P / 'gpu-paths-v11/gpu_core.v',
    'src/fpga/common/gpu_color_depth_cache.sv': P / 'cache-write-speculate-v1/gpu_color_depth_cache.sv',
    'src/fpga/targets/pocket/io_sdram.v': P / 'sdram-address-mux-v1/io_sdram.v',
}
CHECKS = [
    P / 'cpu-rvc-bits-v2/proof/result.json',
    P / 'cpu-rvc-bits-v2/native/result.json',
    P / 'gpu-paths-review-v11/result.json',
    P / 'gpu-paths-review-v11/paths-result.json',
    P / 'cache-write-speculate-v1/protocol/result.json',
    P / 'sdram-address-mux-v1/result.json',
    P / 'sdram-address-mux-v1/bank-test/result.json',
]


def prepare():
    parent = f.checked_stage(BASE)
    for p in CHECKS:
        assert json.loads(p.read_text())['passed'], p
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    for n, src in SOURCES.items():
        shutil.copyfile(src, OUT / 'frozen' / n)
    top = OUT / 'frozen' / TOP
    text = top.read_text()
    needle = '.REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1)'
    assert text.count(needle) == 1
    top.write_text(text.replace(needle, needle + ', .SPECULATE_WRITE_HOT0(1)'))
    sources = {str(p.relative_to(OUT / 'frozen')): f.sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = sorted(n for n, h in sources.items() if parent['source_sha256'][n] != h)
    assert changed == sorted([*SOURCES, TOP]), changed
    evidence = [BASE / 'final-manifest.json', P / 'compact-timing-audit-v3/manifest.json',
                P / 'sdram-address-map-v1/manifest.json', *CHECKS, Path(__file__),
                Path(m.__file__), Path(f.__file__)]
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
        fromfile='compact-v3/' + n, tofile='compact-v4/' + n)) for n in changed))
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
