#!/usr/bin/env python3
"""Private LOD5 area composition: shared scheduler/RAM plus queue-only CPU.

One retained seed2 fit, only after explicit bound review. Never publishes a
bitstream or changes live RTL, configured clocks, generated CPUs or seeds.
"""
import copy
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
BASE = P / 'lod5-cram-span-core-v2'
UNIFIED = P / 'unified-load-core-v2'
QUEUES = P / 'lod5-queues-map-v1'
COMP = P / 'unified-load-components-v2'
OUT = P / 'compact-lod5-core-v1'
CPU = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
WRAPPER = 'src/fpga/common/cpu_system.v'
GEOMETRY = 'src/fpga/common/geometry_transform_light.v'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def checked_stage(path):
    data = json.loads((path / 'prepared.json').read_text())
    for name, digest in data['source_sha256'].items():
        assert sha(path / 'frozen' / name) == digest, name
    return data


def prepare():
    original = checked_stage(BASE)
    unified = checked_stage(UNIFIED)
    queues = checked_stage(QUEUES)
    assert {n for n in original['source_sha256']
            if original['source_sha256'][n] != unified['source_sha256'][n]} == {GEOMETRY}
    assert {n for n in original['source_sha256']
            if original['source_sha256'][n] != queues['source_sha256'][n]} == {CPU, WRAPPER}
    assert json.loads((UNIFIED / 'map-core/result.json').read_text())['passed_map']
    assert json.loads((QUEUES / 'result.json').read_text())['passed_map']
    assert json.loads((COMP / 'wrapper/result.json').read_text())['passed']
    assert all(v['passed'] for v in json.loads((COMP / 'arithmetic/result.json').read_text()).values())
    assert sha(COMP / 'frozen/geometry_transform_light.v') == unified['source_sha256'][GEOMETRY]
    for name, digest in json.loads((COMP / 'manifest.json').read_text()).items():
        assert sha(COMP / name) == digest, name
    native = P / 'lod5-queue-native-v1/final-manifest.json'
    native_data = json.loads(native.read_text())
    assert native_data['passed']
    for name, digest in native_data['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
    OUT.mkdir(exist_ok=False)
    shutil.copytree(UNIFIED / 'frozen', OUT / 'frozen')
    for name in (CPU, WRAPPER):
        shutil.copyfile(QUEUES / 'frozen' / name, OUT / 'frozen' / name)
    sources = {str(p.relative_to(OUT / 'frozen')): sha(p)
               for p in sorted((OUT / 'frozen').rglob('*')) if p.is_file()}
    changed = [n for n, h in sources.items() if original['source_sha256'][n] != h]
    assert sorted(changed) == sorted([CPU, WRAPPER, GEOMETRY])
    evidence = [BASE / 'final-manifest.json', UNIFIED / 'prepared.json',
                UNIFIED / 'map-core/result.json', COMP / 'manifest.json',
                QUEUES / 'final-manifest.json', native,
                P / 'span-lookahead-review-v4/manifest.json']
    save(OUT / 'prepared.json', dict(scope=__doc__, source_sha256=sources,
         only_changed_files=changed, seed=2, seed_sweep=False,
         cpu_cache_kib=128, instruction_cache_kib=32, configured_mhz=100,
         arithmetic_isa_predictors_unchanged=True, deployment_ready=False,
         recipe_sha256=sha(Path(__file__)),
         evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    shutil.copyfile(__file__, OUT / 'recipe.py')
    print(OUT)


def fitrun():
    prep = checked_stage(OUT)
    assert prep['recipe_sha256'] == sha(Path(__file__))
    review = json.loads((OUT / 'independent-review.json').read_text())
    assert review['passed_for_resource_fit'] and not review['deployment_ready']
    assert review['prepared_sha256'] == sha(OUT / 'prepared.json')
    for data in (prep, review):
        for name, digest in data['evidence_sha256'].items():
            assert sha(ROOT / name) == digest, name
    assert prep['seed'] == 2 and not prep['seed_sweep']
    assert prep['cpu_cache_kib'] == 128 and prep['configured_mhz'] == 100
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as fit

    def inventory(**kwargs):
        rows = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text()))
        assert len(rows) == 1 and rows[0]['variant'] == 'os30' and rows[0]['seed'] == 2
        rows[0]['cpu_sha256'] = prep['source_sha256'][CPU]
        return rows

    def freeze(dest, rows):
        frozen = dest / 'frozen'
        shutil.copytree(OUT / 'frozen', frozen)
        fit.save(dest / 'inventory.json', rows)
        fit.save(dest / 'frozen-sources.json', prep['source_sha256'])
        fit.save(dest / 'experiment.json', dict(scope=__doc__, seed=2,
            seed_sweep=False, configured_mhz=100, cpu_cache_kib=128,
            whole_core_native_game_not_measured=True,
            deployment_ready=False, prepared_sha256=sha(OUT / 'prepared.json'),
            review_sha256=sha(OUT / 'independent-review.json'),
            recipe_sha256=sha(Path(__file__)),
            sweep_recipe_sha256=sha(Path(fit.__file__))))
        shutil.copyfile(__file__, dest / 'recipe.py')
        return frozen

    fit.inventory = inventory
    fit.freeze = freeze
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30',
                '--seed-offsets', '0', '--jobs', '1', '--processors', '4',
                '--output', str(OUT / 'fit-seed2')]
    fit.main()


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['fit']), 'prepare|fit'
    prepare() if sys.argv[1] == 'prepare' else fitrun()
