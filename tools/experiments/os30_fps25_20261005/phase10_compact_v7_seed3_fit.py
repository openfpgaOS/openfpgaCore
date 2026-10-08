#!/usr/bin/env python3
"""One private seed-3 placement of exact compact-v7 sources; no seed sweep.

The source inventory and its saved seed remain identical to compact-v7.
Only the generated job's effective fitter seed changes from 2 to 3. Existing
functional, native, map and coupled proofs are reused by exact source binding.
This is one of exactly two newly authorized placements, seeds 3 and 4.
No additional seed trials are authorized by this recipe.
This helper never publishes a bitstream or modifies production/saved seeds.
"""
import copy
import json
import shutil
import sys
from pathlib import Path

import phase10_compact_lod5_fit as common

ROOT, P = common.ROOT, common.P
BASE = P / 'compact-lod5-core-v7'
OUT = P / 'compact-lod5-v7-seed3-v1'
FIT = OUT / 'fit-seed3'
PARENT_SEED, EFFECTIVE_SEED = 2, 3
SEED_FILE = 'src/fpga/targets/pocket/seeds/os30.seed'
REPORT_RECIPE = Path(__file__).with_name('phase10_compact_v7_seed3_report.py')
sha, save = common.sha, common.save


def check_evidence(data):
    for name, digest in data.get('evidence_sha256', {}).items():
        assert sha(ROOT / name) == digest, name


def source_inventory(directory):
    return {str(p.relative_to(directory)): sha(p)
            for p in sorted(directory.rglob('*')) if p.is_file()}


def checked_parent():
    parent = common.checked_stage(BASE)
    assert source_inventory(BASE / 'frozen') == parent['source_sha256']
    assert parent['seed'] == PARENT_SEED and not parent['seed_sweep']
    assert parent['cpu_cache_kib'] == 128 and parent['instruction_cache_kib'] == 32
    assert parent['configured_mhz'] == 100
    assert int((BASE / 'frozen' / SEED_FILE).read_text()) == PARENT_SEED
    review = json.loads((BASE / 'independent-review.json').read_text())
    assert review['prepared_sha256'] == sha(BASE / 'prepared.json')
    assert review['passed_for_resource_fit']
    assert review['component_and_coupled_proofs_complete']
    assert not review['deployment_ready']
    assert json.loads((BASE / 'map-core/result.json').read_text())['passed_map']
    result = json.loads((BASE / 'result.json').read_text())
    assert result['whole_core_fit_successful'] and result['seed'] == PARENT_SEED
    assert result['main_clock_slow_fmax_mhz'] == {'0': 91.2, '85': 89.65}
    for record in (parent, review, json.loads((BASE / 'final-manifest.json').read_text())):
        check_evidence(record)
    return parent


def prepare():
    parent = checked_parent()
    evidence = [BASE / 'prepared.json', BASE / 'independent-review.json',
                BASE / 'final-manifest.json', BASE / 'result.json',
                BASE / 'map-core/result.json', BASE / 'fit-seed2/inventory.json',
                P / 'compact-timing-audit-v7/manifest.json',
                P / 'normal-build-integration-v4/manifest.json',
                P / 'load-submit-coupled-v1/manifest.json',
                Path(__file__), REPORT_RECIPE, Path(common.__file__),
                ROOT / 'tools/sweep_target_matrix.py']
    evidence_hashes = {str(p.relative_to(ROOT)): sha(p) for p in evidence}
    OUT.mkdir(exist_ok=False)
    shutil.copytree(BASE / 'frozen', OUT / 'frozen')
    sources = source_inventory(OUT / 'frozen')
    assert sources == parent['source_sha256']
    save(OUT / 'prepared.json', dict(
        scope=__doc__, source_sha256=sources, only_changed_files=[],
        parent_stage=str(BASE.relative_to(ROOT)),
        parent_prepared_sha256=sha(BASE / 'prepared.json'),
        parent_final_manifest_sha256=sha(BASE / 'final-manifest.json'),
        parent_seed=PARENT_SEED, saved_seed=PARENT_SEED, seed=EFFECTIVE_SEED,
        effective_seed=EFFECTIVE_SEED, seed_offsets=[1], seed_sweep=False,
        exactly_one_fit=True, authorized_effective_seeds=[3, 4], maximum_new_fits=2,
        further_seed_trials_authorized=False, rtl_byte_identical_to_parent=True,
        all_frozen_files_byte_identical_to_parent=True,
        cpu_cache_kib=128, instruction_cache_kib=32, configured_mhz=100,
        arithmetic_isa_predictors_unchanged=True, physical_options_unchanged=True,
        inherited_proof_scope='Exact compact-v7 source closure; no new native-game measurement.',
        normal_build_seed3_closure_required_before_promotion=True,
        deployment_ready=False, recipe_sha256=sha(Path(__file__)),
        report_recipe_sha256=sha(REPORT_RECIPE), evidence_sha256=evidence_hashes))
    shutil.copyfile(__file__, OUT / 'recipe.py')
    shutil.copyfile(REPORT_RECIPE, OUT / 'report-recipe.py')
    (OUT / 'PREPARATION.txt').write_text(
        'Exact compact-v7 sources, one private alternate placement.\n'
        'Saved/source seed remains 2; only effective generated-job seed is 3.\n'
        'No RTL, physical-option, clock, ISA, cache or cycle-count change.\n'
        'Functional/map/native/coupled proofs require independent exact-source binding.\n'
        'No fitter has been invoked by preparation. No full seed sweep is scheduled.\n'
        'Only the two fixed seed-3 and seed-4 trials are authorized; no further seeds.\n'
        'Both slow-corner estimates must exceed 90 MHz, with positive hold.\n'
        'Configured 100 MHz timing remains a separate requirement and is reported honestly.\n'
        'A qualifying fit still requires normal-build seed-3 closure before promotion.\n')
    print(OUT)


def checked_candidate(require_review=True):
    parent = checked_parent()
    prep = common.checked_stage(OUT)
    assert source_inventory(OUT / 'frozen') == parent['source_sha256'] == prep['source_sha256']
    assert prep['only_changed_files'] == []
    assert prep['recipe_sha256'] == sha(Path(__file__)) == sha(OUT / 'recipe.py')
    assert prep['report_recipe_sha256'] == sha(REPORT_RECIPE) == sha(OUT / 'report-recipe.py')
    assert prep['parent_prepared_sha256'] == sha(BASE / 'prepared.json')
    assert prep['parent_final_manifest_sha256'] == sha(BASE / 'final-manifest.json')
    assert prep['seed'] == prep['effective_seed'] == EFFECTIVE_SEED
    assert prep['parent_seed'] == prep['saved_seed'] == PARENT_SEED
    assert prep['seed_offsets'] == [1] and not prep['seed_sweep']
    assert prep['exactly_one_fit'] and prep['configured_mhz'] == 100
    assert prep['authorized_effective_seeds'] == [3, 4]
    assert prep['maximum_new_fits'] == 2 and not prep['further_seed_trials_authorized']
    check_evidence(prep)
    if require_review:
        review = json.loads((OUT / 'independent-review.json').read_text())
        assert review['passed_for_resource_fit'] and not review['deployment_ready']
        assert review['component_and_coupled_proofs_complete']
        assert review['exact_source_binding_passed']
        assert review['prepared_sha256'] == sha(OUT / 'prepared.json')
        assert review['parent_review_sha256'] == sha(BASE / 'independent-review.json')
        assert review['effective_seed'] == EFFECTIVE_SEED
        assert review['saved_seed'] == PARENT_SEED and not review['seed_sweep']
        assert review['exactly_one_fit']
        assert review['authorized_effective_seeds'] == [3, 4]
        assert review['maximum_new_fits'] == 2 and not review['further_seed_trials_authorized']
        check_evidence(review)
    return prep


def fitrun():
    prep = checked_candidate()
    assert not FIT.exists(), 'This fixed one-fit stage already exists; do not overwrite or rerun it.'
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as fit

    def inventory(**kwargs):
        rows = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text()))
        assert len(rows) == 1 and rows[0]['target'] == 'pocket' and rows[0]['variant'] == 'os30'
        assert rows[0]['seed'] == PARENT_SEED
        assert rows[0]['cpu_sha256'] == prep['source_sha256'][common.CPU]
        assert [(row['variant'], seed) for row, seed in fit.seed_candidates(rows, [1])] == [('os30', 3)]
        return rows

    def freeze(dest, rows):
        frozen = dest / 'frozen'
        shutil.copytree(OUT / 'frozen', frozen)
        assert source_inventory(frozen) == prep['source_sha256']
        fit.save(dest / 'inventory.json', rows)
        fit.save(dest / 'frozen-sources.json', prep['source_sha256'])
        fit.save(dest / 'experiment.json', dict(
            scope=__doc__, saved_seed=PARENT_SEED, effective_seed=EFFECTIVE_SEED,
            seed_offsets=[1], seed_sweep=False, exactly_one_fit=True,
            authorized_effective_seeds=[3, 4], maximum_new_fits=2,
            further_seed_trials_authorized=False,
            configured_mhz=100, cpu_cache_kib=128, instruction_cache_kib=32,
            full_frozen_source_closure_byte_identical_to_parent=True,
            whole_core_native_game_not_measured=True, deployment_ready=False,
            prepared_sha256=sha(OUT / 'prepared.json'),
            review_sha256=sha(OUT / 'independent-review.json'),
            recipe_sha256=sha(Path(__file__)), sweep_recipe_sha256=sha(Path(fit.__file__))))
        shutil.copyfile(__file__, dest / 'recipe.py')
        return frozen

    fit.inventory, fit.freeze = inventory, freeze
    sys.argv = [sys.argv[0], '--target', 'pocket', '--variant', 'os30',
                '--seed-offsets', '1', '--jobs', '1', '--processors', '4',
                '--output', str(FIT)]
    fit.main()


if __name__ == '__main__':
    assert sys.argv[1:] in (['prepare'], ['check'], ['fit']), 'prepare|check|fit'
    if sys.argv[1] == 'prepare':
        prepare()
    elif sys.argv[1] == 'check':
        checked_candidate(require_review=False)
        print('Exact source/preparation checks pass; this command invoked no fitter.')
    else:
        fitrun()
