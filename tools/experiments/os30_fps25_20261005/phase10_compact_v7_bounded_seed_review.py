#!/usr/bin/env python3
"""Review only the two authorized unchanged-v7 placements; never invoke Quartus."""
import ast
import copy
import importlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


def main():
    assert len(sys.argv) == 2 and sys.argv[1] in ('3', '4')
    seed = int(sys.argv[1])
    c = importlib.import_module(f'phase10_compact_v7_seed{seed}_fit')
    ROOT, BASE, OUT = c.ROOT, c.BASE, c.OUT
    sha, save = c.sha, c.save
    assert not (OUT / 'independent-review.json').exists()
    assert not c.FIT.exists()
    prep = c.checked_candidate(require_review=False)
    assert prep['effective_seed'] == seed and prep['saved_seed'] == 2
    assert prep['authorized_effective_seeds'] == [3, 4]
    assert prep['maximum_new_fits'] == 2 and not prep['further_seed_trials_authorized']
    assert prep['exactly_one_fit'] and not prep['seed_sweep']
    assert prep['rtl_byte_identical_to_parent'] and prep['all_frozen_files_byte_identical_to_parent']
    parent = json.loads((BASE / 'prepared.json').read_text())
    assert prep['source_sha256'] == parent['source_sha256']
    assert len(prep['source_sha256']) == 115
    for name, digest in prep['source_sha256'].items():
        assert sha(OUT / 'frozen' / name) == sha(BASE / 'frozen' / name) == digest, name
    parent_review = json.loads((BASE / 'independent-review.json').read_text())
    hashes_checked = 0
    for name, digest in parent_review['evidence_sha256'].items():
        path = ROOT / name
        assert sha(path) == digest, name
        hashes_checked += 1
        if path.name.endswith('manifest.json'):
            record = json.loads(path.read_text())
            if 'files' in record:
                entries = record['files']
            elif record and all(isinstance(x, str) and len(x) == 64 for x in record.values()):
                entries = record
            else:
                entries = {}
            for rel, h in entries.items():
                assert sha(path.parent / rel) == h, rel
                hashes_checked += 1
            for rel, h in record.get('evidence_sha256', {}).items():
                assert sha(ROOT / rel) == h, rel
                hashes_checked += 1
    sys.path.insert(0, str(ROOT / 'tools'))
    import sweep_target_matrix as sweep
    rows = copy.deepcopy(json.loads((BASE / 'fit-seed2/inventory.json').read_text()))
    assert len(rows) == 1 and rows[0]['target'] == 'pocket' and rows[0]['variant'] == 'os30' and rows[0]['seed'] == 2
    tasks = sweep.seed_candidates(rows, [seed - 2])
    assert len(tasks) == 1 and tasks[0][1] == seed
    directory = OUT / 'independent-review-proof'
    directory.mkdir(exist_ok=False)
    with tempfile.TemporaryDirectory(prefix=f'v7-seed{seed}-review-') as temporary:
        destination = Path(temporary) / 'job'
        sweep.prepare(OUT / 'frozen', destination, rows[0], seed, 4)
        qsf = destination / 'src/fpga/targets/pocket/ap_core.qsf'
        parent_qsf = BASE / 'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
        assert re.findall(r'^set_global_assignment -name SEED (\d+)$', qsf.read_text(), re.M) == [str(seed)]
        expected = parent_qsf.read_text().replace('set_global_assignment -name SEED 2\n', f'set_global_assignment -name SEED {seed}\n')
        assert qsf.read_text() == expected
        shutil.copy2(qsf, directory / f'effective-seed{seed}.qsf')
        for name, digest in prep['source_sha256'].items():
            if name != 'src/fpga/targets/pocket/ap_core.qsf':
                assert sha(destination / name) == digest, name
    tree = ast.parse(Path(c.__file__).read_text())
    fitrun = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'fitrun')
    argv = next(n.value for n in ast.walk(fitrun) if isinstance(n, ast.Assign) and any(
        isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
        and t.value.id == 'sys' and t.attr == 'argv' for t in n.targets))
    strings = [n.value for n in ast.walk(argv) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert strings == ['--target', 'pocket', '--variant', 'os30', '--seed-offsets', str(seed - 2), '--jobs', '1', '--processors', '4', '--output']
    assert not c.FIT.exists()
    findings = [
        'All 115 frozen files equal the already-approved compact-v7 source closure byte for byte. CPU 128 KiB D/32 KiB I, arithmetic, ISA, clocks, constraints, geometry, GPU, memory interfaces and physical options are unchanged.',
        'The parent independent component/native/coupled gate and its directly bound manifests/results were rehashed. Exact-source reuse needs no new functional simulation; it makes no new native-game or Pocket FPS claim.',
        f'Dry shared job preparation produces a QSF exactly equal to routed v7 except SEED 2 becomes {seed}. Every other generated source remains identical; the frozen saved-seed file remains 2.',
        f'This adapter selects exactly one os30 job with offset {seed - 2} from saved 2, one job and four processors. Existing fit output is rejected, preserving attempts. It does not launch the other authorized seed or any automatic retry.',
        'The complete selected placement allowance is exactly seeds 3 and 4, at most two new fits. Neither passing does not authorize another seed. Both results, including failures, must be retained; if both qualify, root selects by the higher minimum slow-corner frequency.',
        'The report helper checks actual effective QSF/source identity, retains both slow-corner and configured 100 MHz timing checks, and does not turn synthesis success into deployment qualification.',
        'No production source, saved seed/fingerprint, SDK/application, SD, clock or physical option is changed. A qualifying selected seed still needs an exact ordinary-build seed/fingerprint revision before promotion.',
    ]
    save(directory / 'result.json', dict(passed=True, quartus_invoked=False, fitter_invoked=False,
         frozen_files=115, hash_bindings_checked=hashes_checked,
         tasks=[dict(target=r['target'], variant=r['variant'], seed=s) for r, s in tasks],
         actual_dry_qsf_only_change=f'SEED2 to{seed}', source_saved_seed=2,
         authorized_effective_seeds=[3, 4], maximum_new_fits=2,
         further_seed_trials_authorized=False))
    (directory / 'REPORT.txt').write_text('Independent bounded unchanged-v7 placement review\n\n' + '\n\n'.join(findings) + '\n\nPassed only for the declared private single placement. No Quartus or fitter was invoked. Deployment remains false.\n')
    shutil.copy2(__file__, directory / 'recipe.py')
    evidence = [OUT / 'prepared.json', BASE / 'prepared.json', BASE / 'independent-review.json',
                BASE / 'final-manifest.json', BASE / 'fit-seed2/inventory.json',
                BASE / 'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf',
                Path(c.__file__), c.REPORT_RECIPE, Path(c.common.__file__), Path(sweep.__file__),
                Path(__file__), *directory.iterdir()]
    evidence += [ROOT / name for name in parent_review['evidence_sha256'] if name.endswith('manifest.json')]
    save(OUT / 'independent-review.json', dict(
        passed_for_resource_fit=True, deployment_ready=False,
        component_and_coupled_proofs_complete=True, exact_source_binding_passed=True,
        prepared_sha256=sha(OUT / 'prepared.json'), parent_review_sha256=sha(BASE / 'independent-review.json'),
        effective_seed=seed, saved_seed=2, seed_sweep=False, exactly_one_fit=True,
        authorized_effective_seeds=[3, 4], maximum_new_fits=2,
        further_seed_trials_authorized=False, findings=findings,
        limits=['Only this declared placement within the two-fit total cap is reviewed.',
                'Comparative >90 MHz and configured 100 MHz closure remain distinct; neither is inferred here.',
                'No production promotion or device write is authorized.'],
        evidence_sha256={str(path.relative_to(ROOT)): sha(path) for path in sorted(set(evidence))}))
    c.checked_candidate(require_review=True)
    print(json.dumps(dict(seed=seed, independent_review_sha256=sha(OUT / 'independent-review.json'),
                          hash_bindings_checked=hashes_checked, frozen_files=115)))


if __name__ == '__main__':
    main()
