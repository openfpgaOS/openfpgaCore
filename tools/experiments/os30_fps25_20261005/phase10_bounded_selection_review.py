#!/usr/bin/env python3
"""Static review seal for the two-placement selector; never execute selection."""
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
T = ROOT / 'tools/experiments/os30_fps25_20261005'
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'bounded-v7-selection-review-v1'
SELECTOR = T / 'phase10_bounded_placement_selection.py'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not OUT.exists()
    source = SELECTOR.read_text()
    tree = ast.parse(source)
    imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports == {'hashlib', 'json', 'pathlib', 'shutil'}
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    loop = next(n for n in fn.body if isinstance(n, ast.For))
    assert isinstance(loop.target, ast.Name) and loop.target.id == 'seed'
    assert ast.literal_eval(loop.iter) == (3, 4)
    for token in (
        "result['prepared_sha256']", "complete.json", "final['evidence_sha256']",
        "('map', 'fit', 'asm', 'sta')", "set(fmax) == {'0', '85'}",
        'min(fmax.values()) > 90', "('hold', 'recovery', 'removal', 'pulse_width')",
        "bool(qualifying) == result['meets_requested_comparative_timing']",
        "(r['minimum_slow_fmax_mhz'], -r['seed'])", 'if passing else None',
        'OUT.mkdir(exist_ok=False)', 'deployment_ready=False',
        'further_seed_trials_selected=False', 'saved_production_seed_modified=False',
    ):
        assert token in source, token
    # The authoring recipe is inspected, not imported, run or synthesized.
    assert not (P / 'bounded-v7-placement-selection-v1').exists()
    evidence = [SELECTOR, Path(__file__)]
    for seed in (3, 4):
        stage = P / f'compact-lod5-v7-seed{seed}-v1'
        for name in ('prepared.json', 'independent-review.json', 'recipe.py', 'report-recipe.py'):
            evidence.append(stage / name)
        review = json.loads((stage / 'independent-review.json').read_text())
        assert review['passed_for_resource_fit'] and review['exact_source_binding_passed']
        assert review['effective_seed'] == seed and review['saved_seed'] == 2
        assert review['authorized_effective_seeds'] == [3, 4] and review['maximum_new_fits'] == 2
        assert not review['further_seed_trials_authorized']
        for name, digest in review['evidence_sha256'].items():
            assert sha(ROOT / name) == digest, name
    OUT.mkdir()
    shutil.copyfile(SELECTOR, OUT / 'reviewed-selector.py')
    shutil.copyfile(__file__, OUT / 'recipe.py')
    record = dict(passed_static_review=True, selector_sha256=sha(SELECTOR),
        selection_executed=False, selected_seed=None, fit_qualification_evaluated=False,
        deployment_ready=False, fitter_or_tests_invoked=False, production_or_sd_writes=0,
        exact_trial_domain=[3, 4], maximum_new_fits=2,
        rules_reviewed=[
            'Both stage manifests must exist and every recorded artifact is rehashed before output creation.',
            'Each actual result and completion record is required in the corresponding final manifest; result binds the prepared source hash.',
            'Effective seed3/4, saved/parent seed2, exact unchanged source, configured100MHz and fixed two-trial cap are checked.',
            'Qualification requires map/fit/asm/STA success, exactly both slow-corner Fmax values greater than90MHz, and positive hold/recovery/removal/pulse width.',
            'The recomputed qualification must equal the report sealer result; full100MHz setup closure remains a separate recorded field.',
            'Highest minimum slow-corner Fmax wins; equal minima select the lower seed. With no qualifier, selected_seed is null.',
            'Writes are confined to a new private assessment directory; no build, promotion, new seed, package or device action is invoked.',
        ], limits=[
            'This is a static source review while fits run, not a result selection or timing qualification.',
            'The later executed assessment must be independently compared with both final actual reports.',
            'Any selected fit still needs matching normal-build seed and saved-fingerprint qualification; comparative90MHz is not configured100MHz closure or a Pocket FPS result.',
        ], evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence})
    (OUT / 'review.json').write_text(json.dumps(record, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''Bounded v7 placement selector: static review PASS

The loop domain is exactly seeds 3 and 4. Both final manifests and their bound
result/completion records are required before a private output directory can
be created. Prepared source hashes, seed provenance and the two-trial limit
are checked. Missing or changed evidence fails closed.

Eligibility is recomputed from successful map/fit/assembly/STA, both slow
corners strictly above 90 MHz and positive hold/recovery/removal/pulse width.
It must equal the independently sealed result flag. Configured 100 MHz setup
closure is retained as a separate result rather than implied by this threshold.
Ranking maximizes the lower of the two corner estimates; the lower seed wins
an exact tie. No passing result produces a null selection, with no additional
trial authorized. Both passing and failed placement records remain visible.

The selector performs no fitting, CPU generation, promotion, package copying
or device operation. Its only writes are a fresh private assessment and recipe
snapshot. This review did not import or invoke the selector, simulate cases,
or assess the still-running fit results. Later selection and any normal-build
seed/fingerprint revision remain separate guarded steps.
''')
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        files={p.name: sha(p) for p in OUT.iterdir() if p.is_file()},
        evidence_sha256=record['evidence_sha256']), indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), review_sha256=sha(OUT / 'review.json'))))


if __name__ == '__main__':
    main()
