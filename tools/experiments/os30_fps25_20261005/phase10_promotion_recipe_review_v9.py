#!/usr/bin/env python3
"""Static/hash review only. Never import or run a promotion recipe."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
T = ROOT / 'tools/experiments/os30_fps25_20261005'
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'promotion-recipe-review-v9'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    assert not OUT.exists()
    evidence = {}
    rehashed = 0

    def bind(p):
        evidence[str(p.relative_to(ROOT))] = sha(p)

    def verify_map(base, values):
        nonlocal rehashed
        for name, digest in values.items():
            p = base / name
            assert p.is_file() and not p.is_symlink() and sha(p) == digest, p
            rehashed += 1

    texts = {}
    functions = {}
    for version in (8, 9):
        for kind in ('plan', 'rollback', 'transaction'):
            p = T / f'phase10_promotion_{kind}_v{version}.py'
            bind(p)
            source = p.read_text()
            tree = ast.parse(source)
            texts[kind, version] = source
            functions[kind, version] = {
                n.name: ast.get_source_segment(source, n)
                for n in tree.body if isinstance(n, ast.FunctionDef)
            }
    old = functions['transaction', 8]
    new = functions['transaction', 9]
    same = {name: old[name] == value for name, value in new.items()}
    changed = {name for name, equal in same.items() if not equal}
    assert changed == {'qualify_fit', 'check_closure', 'prepare'}
    assert texts['transaction', 8].split("if __name__=='__main__':", 1)[1] == texts['transaction', 9].split("if __name__=='__main__':", 1)[1]
    assert functions['rollback', 8] == functions['rollback', 9]
    assert texts['rollback', 8].replace('_v8', '_v9').replace('-v8', '-v9') == texts['rollback', 9]

    normal = P / 'normal-build-integration-v11'
    manifest = read(normal / 'manifest.json')
    verify_map(normal, manifest)
    prep = read(normal / 'prepared.json')
    result = read(normal / 'result.json')
    assert len(prep['proposed_sha256']) == 31 and len(prep['overrides']) == 6
    verify_map(normal / 'proposed', prep['proposed_sha256'])
    assert result['passed'] and result['selected_job_source_exact']
    assert result['other_variants_unchanged'] and result['normal_and_frozen_generators_agree']
    assert result['effective_seed'] == 1 and result['inherited_frozen_seed'] == 2
    assert result['prepared_sha256'] == sha(normal / 'prepared.json')
    selected = P / 'compact-lod5-v7-seed1-v1'
    trial = read(selected / 'prepared.json')
    assert prep['selected_prepared_sha256'] == sha(selected / 'prepared.json')
    for row in prep['inventory']:
        assert row['selected_sha256'] == trial['source_sha256'][row['selected_path']]
    binding = read(normal / 'checks/selected-binding.json')
    qsf = selected / 'fit-seed1/jobs/pocket-os30-s1/src/fpga/targets/pocket/ap_core.qsf'
    assert binding['actual_job_qsf_sha256'] == sha(qsf)
    seed = 'src/fpga/targets/pocket/seeds/os30.seed'
    assert (normal / 'proposed' / seed).read_text() == '1\n'
    fingerprint = (normal / 'proposed' / (seed + '.src')).read_text().strip()
    for name in ('os30-seed-pair.json', 'os30-frozen-seed-pair.json'):
        value = read(normal / 'checks' / name)
        assert value['passed'] and not value['stale_warning'] and value['effective_seed'] == 1
        assert value['fingerprint'] == value['saved_fingerprint'] == fingerprint

    cpu = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    assert prep['proposed_sha256'][cpu] == '8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'
    regen = P / 'cpu-os30-regenerate-v5'
    verify_map(regen, read(regen / 'manifest.json'))
    repro = P / 'cpu-os30-repro-v5'
    verify_map(repro, read(repro / 'manifest.json')['files'])
    delivery = P / 'load-submit-delivery-v1'
    dm = read(delivery / 'manifest.json')
    verify_map(delivery, dm['files'])
    verify_map(ROOT, dm['evidence_sha256'])
    sources = read(delivery / 'sources.json')
    assert len(sources['changed_files']) == 3
    for name in sources['changed_files']:
        assert sha(delivery / 'sources' / name) == sources['inputs'][name]['after_sha256']
    app = delivery / 'payload/Assets/sm64/common/sm64.elf'
    assert sha(app) == 'bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'

    fit = read(selected / 'result.json')
    # Evaluate only the plain record predicate; never call qualify_fit or any recipe.
    timing_pass = min(fit['main_clock_slow_fmax_mhz'].values()) > 90 and fit['worst_timing']['hold'] > 0
    assert not timing_pass and not fit['meets_requested_comparative_timing']
    action_dirs = ['promotion-plan-v9', 'promotion-rollback-v9', 'promotion-payload-v9', 'promotion-transaction-v9']
    assert all(not (P / name).exists() and not (P / name).is_symlink() for name in action_dirs)

    for base, names in (
        (normal, ['manifest.json', 'prepared.json', 'result.json', 'production-before.json', 'checks/selected-binding.json', 'checks/os30-seed-pair.json', 'checks/os30-frozen-seed-pair.json']),
        (selected, ['prepared.json', 'independent-review.json', 'result.json', 'final-manifest.json']),
        (delivery, ['manifest.json', 'sources.json', 'rebuild-proof.json']),
        (repro, ['manifest.json']),
        (regen, ['manifest.json', 'result.json']),
    ):
        for name in names:
            bind(base / name)
    bind(app)
    bind(qsf)
    bind(Path(__file__))
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    for kind in ('plan', 'rollback', 'transaction'):
        for version in (8, 9):
            name = f'phase10_promotion_{kind}_v{version}.py'
            shutil.copyfile(T / name, OUT / name)
    patch = ''.join(''.join(difflib.unified_diff(texts[kind, 8].splitlines(True), texts[kind, 9].splitlines(True), fromfile=f'{kind}-v8', tofile=f'{kind}-v9')) for kind in ('plan', 'rollback', 'transaction'))
    (OUT / 'mechanical.patch').write_text(patch)
    review = dict(
        passed=True, passed_for_conditional_recipe_use=True,
        promotion_allowed_for_current_fit=False, deployment_ready=False,
        recipe_actions_executed=[], native_tests_executed=False, quartus_invoked=False,
        production_or_sd_writes=0, fresh_production_guard_snapshot_taken=False,
        current_fit=dict(main_clock_slow_fmax_mhz=fit['main_clock_slow_fmax_mhz'], hold=fit['worst_timing']['hold'], comparative_timing_gate_passed=timing_pass),
        selected_hardware='Unchanged compact-v7 RTL, effective seed1; inherited frozen seed2.',
        cpu_sha256=prep['proposed_sha256'][cpu], app_sha256=sha(app),
        destinations=dict(hardware=31, renderer=3, artifacts=4, total=38, os30_overrides=6, sd=0, quake=0),
        transaction_functions_byte_identical=same, transaction_cli_byte_identical=True,
        rollback_body_byte_identical=True,
        reviewed_guards=[
            'Exact selected source/QSF closure and ordinary/frozen saved-seed fingerprints.',
            'Both slow corners strictly above90MHz, positive hold, selected assembler/STA hashes.',
            'Fresh132core and6747SM64 source guards plus retained app/core/OS/loader and Quake hashes before mutation.',
            'Exact38-output allowlist; no SD or Quake destination; only selected normal app/core installed.',
            'Private backups with bytes/modes, journal intent before replacement, same-directory atomic replace and fsync.',
            'Rollback only for exact transaction bytes or already-restored bytes, stopping on intervening edits.',
        ],
        limitations=[
            'Static/hash review only; parent mutation implementation reused without new runtime crash or rollback tests.',
            'The current seed1 fit fails the timing guard and must not be promoted using these recipes.',
            'No fresh production guard snapshot was generated; execution would rerun all destination/source guards.',
            'Configured100MHz is not closed by a comparative90MHz gate. Concurrent destination editing is excluded; filesystem replacement is not compare-and-swap.',
            'Rollback also revalidates the sealed payload dependencies and fit; preserve them with the journal/backups.',
        ],
        action_directories_absent=action_dirs, evidence_files_rehashed=rehashed, evidence_sha256=evidence)
    (OUT / 'review.json').write_text(json.dumps(review, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text(
        'Conditional promotion-v9 mechanical review: PASS; current fit NOT QUALIFIED.\n\n'
        'The three recipes select exactly31 hardware/build files,3 SM64 renderer files and4 local app/core artifacts. '
        'The selected source is unchanged compact-v7, using effective seed1 and the normal-v11 saved seed/fingerprint. '
        'The tested normal ELF is bbb1c31a..., and the CPU is8fca2871.... Quake2, SD, OS and loader are excluded.\n\n'
        'All transaction safety, path, guard, journal, atomic replacement, apply, rollback and CLI functions are byte-identical to v8. '
        'Only qualifier, closure and private payload preparation references change. The rollback preparation body is also identical. '
        'Normal source closure, source hashes, both seed/fingerprint checks, real CPU generation and app-delivery bindings were independently rehashed.\n\n'
        'The completed seed1 result is85.62MHz at85C and86.18MHz at0C, with+0.014ns hold. '
        'It fails the strict both-corners>90MHz guard before payload creation. Review approval is conditional recipe correctness, not permission or evidence to promote this failed fit.\n\n'
        'No recipe was imported or invoked, no simulation or Quartus process was run, and no production or SD path was changed. '
        'All four action output directories remain absent. No fresh production guard snapshot is claimed; the recipes must rerun their guards if ever selected. '
        'The inherited no-concurrent-edit and preserved sealed-dependency assumptions remain.\n')
    (OUT / 'manifest.json').write_text(json.dumps({str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}, indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), review_sha256=sha(OUT / 'review.json'), evidence_files_rehashed=rehashed, promotion_allowed=False), indent=2))


if __name__ == '__main__':
    main()
