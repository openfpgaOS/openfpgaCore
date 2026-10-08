#!/usr/bin/env python3
"""Independent source-composition gate for floor + cold report + clamp."""
from pathlib import Path
import hashlib
import importlib.util
import json

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/quake2-optimize-pass2-20261008'
TOOLS = Path(__file__).resolve().parent
REL = Path('src/quake2/openfpga/of_gl1.c')
BASE_SHA = 'a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'
TRIO_SHA = '29818e61c88a358f30cde8a4282676c6904a71b12b77c98934844233ea84fbdc'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name):
    path = TOOLS / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    target = OUT / 'source-review-trio-v1'
    assert not target.exists()
    base = OUT / 'work/base'
    work = OUT / 'work/trio'
    assert sha(base / REL) == BASE_SHA and sha(work / REL) == TRIO_SHA
    original = (base / REL).read_text()
    candidate = (work / REL).read_text()
    cold = load('cold_diagnostic_v1')
    floor = load('floor_prepare')
    clamp = load('clamp_prepare')
    expected = clamp.apply(floor.apply(cold.extract(original)[0]))
    assert candidate == expected
    assert candidate == (OUT / 'components/trio-v1/of_gl1.c').read_text()
    # Bind the whole copied input set, not just the changed renderer file.
    before = json.loads((OUT / 'before.json').read_text())['source_sha256']
    changed = []
    for rel, expected_hash in before.items():
        assert sha(base / rel) == expected_hash, rel
        if sha(work / rel) != expected_hash:
            changed.append(rel)
    assert changed == [str(REL)], changed
    # The rejected worker and negligible vertex-cache split are absent.
    assert 'static int ofgl_world_geometry;' not in candidate
    assert candidate[candidate.index('static unsigned ofgl_world_prepared;'):
                     candidate.index('\nstatic int\nofgl_geometry_tri(')] == \
        original[original.index('static unsigned ofgl_world_prepared;'):
                 original.index('\nstatic int\nofgl_geometry_tri(')]
    assert candidate[candidate.index('static ofgl_cvert_t *\nofgl_cvert('):
                     candidate.index('/* Sutherland-Hodgman')] == \
        original[original.index('static ofgl_cvert_t *\nofgl_cvert('):
                 original.index('/* Sutherland-Hodgman')]
    test_paths = [OUT / 'components/floor-v1/tests.json',
                  OUT / 'components/clamp-v1/tests.json',
                  OUT / 'components/cold-v1/report-checks-v2/result.json',
                  OUT / 'components/cold-v1/result.json']
    for path in test_paths:
        assert json.loads(path.read_text())['passed'], path
    result = dict(passed=True, baseline_sha256=BASE_SHA, candidate_sha256=TRIO_SHA,
                  exact_component_order=['cold-v1', 'floor-v1', 'clamp-v1'],
                  changed_paths=changed, unchanged_source_guards=len(before) - 1,
                  worker_guard_included=False, vertex_split_included=False,
                  review=[
                      'UV helper is used only by two emitter rebase floors; finite |v|<2^23 excludes integer overflow, preserves exact float return, explicitly rounds down, and retains libm fallback.',
                      'Projection finite guard remains ahead of all new min/max operations. Depth scale assignments stay1 or16; finite iw product can overflow to infinity but cannot becomeNaN.',
                      'Optional diagnostic condition remains at the original point after emitter finite validation. Reporter computes the same screen extrema, text and quota updates.',
                      'No command order, clipping rule, surface state, texture lifecycle, build flag, world cache or vertex cache changes.',
                  ],
                  differential_evidence={'floor_cases':1592335, 'projection_cases':909460,
                                         'report_cases':100000},
                  limitation='Source/component gate only. Final combined model, render replays and regression suite remain separate.')
    target.mkdir()
    (target / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    files = [Path(__file__), OUT / 'before.json', base / REL, work / REL,
             OUT / 'components/trio-v1/manifest.json', OUT / 'components/trio-v1/change.patch',
             target / 'result.json', *test_paths,
             TOOLS / 'cold_diagnostic_v1.py', TOOLS / 'floor_prepare.py', TOOLS / 'clamp_prepare.py']
    (target / 'manifest.json').write_text(json.dumps(dict(passed=True, evidence_sha256={
        str(path.relative_to(ROOT)):sha(path) for path in files}), indent=2) + '\n')
    print(json.dumps(dict(output=str(target), manifest_sha256=sha(target / 'manifest.json'),
                          source_sha256=TRIO_SHA, unchanged_guards=len(before)-1), indent=2))


if __name__ == '__main__':
    main()
