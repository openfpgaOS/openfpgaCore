#!/usr/bin/env python3
"""Static/hash review of conditional v10 promotion; never import its recipes."""
import ast
import difflib
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
T = ROOT / 'tools/experiments/os30_fps25_20261005'
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'promotion-recipe-review-v10'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    assert not OUT.exists()
    evidence = {}
    checked = 0

    def bind(path):
        evidence[str(path.relative_to(ROOT))] = sha(path)

    def verify(base, values):
        nonlocal checked
        for name, digest in values.items():
            path = base / name
            assert path.is_file() and not path.is_symlink() and sha(path) == digest, path
            checked += 1

    texts, funcs = {}, {}
    for version in (9, 10):
        for kind in ('plan', 'rollback', 'transaction'):
            path = T / f'phase10_promotion_{kind}_v{version}.py'
            text = path.read_text()
            texts[kind, version] = text
            funcs[kind, version] = {n.name: ast.get_source_segment(text, n)
                                    for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}
            bind(path)
    parent, candidate = funcs['transaction', 9], funcs['transaction', 10]
    same = {name: parent[name] == value for name, value in candidate.items()}
    changed = {name for name, exact in same.items() if not exact}
    assert changed == {'qualify_fit', 'check_closure', 'prepare', 'load_payload', 'apply', 'rollback'}
    count_only = {}
    for name in ('load_payload', 'apply', 'rollback'):
        count_only[name] = parent[name].replace('38', '36') == candidate[name]
        assert count_only[name], name
    assert texts['transaction', 9].split("if __name__=='__main__':", 1)[1] == texts['transaction', 10].split("if __name__=='__main__':", 1)[1]
    assert funcs['rollback', 9]['main'].replace('38', '36').replace('31 hardware', '29 hardware') == funcs['rollback', 10]['main']

    normal = P / 'normal-build-integration-v12'
    verify(normal, read(normal / 'manifest.json'))
    prep = read(normal / 'prepared.json')
    result = read(normal / 'result.json')
    assert result['passed'] and result['selected_map_source_exact']
    assert result['other_variants_unchanged'] and result['normal_and_frozen_generators_agree']
    assert result['prepared_sha256'] == sha(normal / 'prepared.json')
    assert len(prep['overrides']) == 6 and len(prep['proposed_sha256']) == 29
    assert not any('/seeds/' in name for name in prep['proposed_sha256'])
    verify(normal / 'proposed', prep['proposed_sha256'])
    selected = P / 'compact-lod5-core-v14'
    fit_prep = read(selected / 'prepared.json')
    assert prep['selected_prepared_sha256'] == sha(selected / 'prepared.json')
    assert fit_prep['seed'] == 2 and not fit_prep['seed_sweep'] and fit_prep['configured_mhz'] == 100
    for row in prep['inventory']:
        assert row['selected_sha256'] == fit_prep['source_sha256'][row['selected_path']]
    verify(selected / 'frozen', fit_prep['source_sha256'])
    cpu_path = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    cpu_sha = 'ca6a2cb822978bf3ad6223397bc5622f0149a550237fb8096178a5087bc76d40'
    assert prep['proposed_sha256'][cpu_path] == cpu_sha
    binding = read(normal / 'checks/selected-binding.json')
    qsf = selected / 'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
    assert binding['mapped_qsf_sha256'] == sha(qsf)
    assert binding['selected_prepared_sha256'] == sha(selected / 'prepared.json')
    assert binding['seed'] == 2
    closure = candidate['check_closure']
    assert "binding['mapped_qsf_sha256']" in closure and 'sha(actual_qsf)' in closure
    assert "binding['selected_prepared_sha256']" in closure
    assert 'Saved-seed metadata must remain unchanged' in closure

    repro = P / 'cpu-os30-repro-v10'
    regen = P / 'cpu-os30-regenerate-v10'
    verify(repro, read(repro / 'manifest.json')['files'])
    verify(regen, read(regen / 'manifest.json'))
    assert read(regen / 'result.json')['output_sha256'] == cpu_sha
    delivery = P / 'load-submit-delivery-v1'
    app_manifest = read(delivery / 'manifest.json')
    verify(delivery, app_manifest['files'])
    verify(ROOT, app_manifest['evidence_sha256'])
    app_sources = read(delivery / 'sources.json')
    assert len(app_sources['changed_files']) == 3
    app = delivery / 'payload/Assets/sm64/common/sm64.elf'
    app_sha = 'bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
    assert sha(app) == app_sha
    for name in app_sources['changed_files']:
        assert sha(delivery / 'sources' / name) == app_sources['inputs'][name]['after_sha256']

    actions = ['promotion-plan-v10', 'promotion-rollback-v10', 'promotion-payload-v10', 'promotion-transaction-v10']
    assert all(not (P / name).exists() and not (P / name).is_symlink() for name in actions)
    for base, names in (
        (normal, ['manifest.json', 'prepared.json', 'result.json', 'production-before.json', 'checks/selected-binding.json']),
        (selected, ['prepared.json', 'independent-review.json', 'stage-recipe.py']),
        (delivery, ['manifest.json', 'sources.json', 'rebuild-proof.json']),
        (repro, ['manifest.json']), (regen, ['manifest.json', 'result.json']),
        (P / 'cpu-fetch-v7-source-review-v12', ['manifest.json', 'review.json']),
    ):
        for name in names:
            bind(base / name)
    bind(app)
    bind(qsf)
    bind(Path(__file__))
    OUT.mkdir()
    shutil.copyfile(__file__, OUT / 'recipe.py')
    for kind in ('plan', 'rollback', 'transaction'):
        for version in (9, 10):
            name = f'phase10_promotion_{kind}_v{version}.py'
            shutil.copyfile(T / name, OUT / name)
    patch = ''.join(''.join(difflib.unified_diff(texts[kind, 9].splitlines(True), texts[kind, 10].splitlines(True), fromfile=f'{kind}-v9', tofile=f'{kind}-v10')) for kind in ('plan', 'rollback', 'transaction'))
    (OUT / 'mechanical.patch').write_text(patch)
    review = dict(
        passed=True, mechanical_guard_review_passed=True,
        passed_for_conditional_recipe_use=True, deployment_ready=False,
        promotion_ready_after_timing_pass_alone=False,
        saved_seed_fingerprint_revision_required_before_promotion=True,
        timing_qualification_evaluated=False, qualifying_fit_required_before_payload=True,
        promotion_actions_executed=[], simulations_or_quartus_invoked=False,
        production_or_sd_writes=0, fresh_production_guard_snapshot_taken=False,
        target='compact-v14: exact v7 plus only the final-fetch CPU factor; original seed2.',
        cpu_sha256=cpu_sha, app_sha256=app_sha,
        destinations=dict(hardware=29, renderer=3, artifacts=4, total=36, os30_overrides=6, seed_metadata=0, sd=0, quake=0),
        transaction_functions_byte_identical=same,
        mutation_functions_identical_except_38_to36=count_only,
        cli_byte_identical=True, rollback_preparation_identical_except_count_and_description=True,
        exact_map_normal_actual_job_qsf_binding=True,
        reviewed_guards=[
            'Exact source allowlist, selected CPU and normal-build closure; mapped QSF checked against actual fit job.',
            'No saved-seed metadata destination; actual os30 fit must use seed2 and retained100MHz configuration.',
            'Both slow corners strictly above90MHz, positive hold, successful map/fit/asm/sta and selected assembler/STA evidence.',
            'Fresh132core/6747SM64 source guards and retained artifact/Quake hashes before every mutation phase.',
            'Exact36-destination shape with preserved path/symlink, mode, durable backup, intent journal, atomic replacement and fsync checks.',
            'Rollback restores only recorded output or accepts already-restored bytes, stopping on intervening edits; created directories require same inode and emptiness.',
        ],
        limitations=[
            'Review is static plus evidence hashing; no action, crash-recovery test, fit qualification or fresh production snapshot was performed.',
            'The unchanged saved .seed.src fingerprint is stale for the new selected source. Root requires a separately reviewed fingerprint-only normal-build/recipe revision before promotion; v10 is not ready on timing success alone.',
            'The selected fit must complete and pass the runtime qualification before payload creation/application. This review does not select or qualify a bitstream.',
            'The comparative90MHz gate does not close configured100MHz timing; negative configured setup remains explicitly experimental.',
            'Preserve sealed dependencies/journal/backups and exclude concurrent destination editing; atomic rename is not filesystem compare-and-swap.',
        ], action_directories_absent=actions, evidence_files_rehashed=checked, evidence_sha256=evidence)
    (OUT / 'review.json').write_text(json.dumps(review, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text('''Conditional promotion-v10 mechanical review: PASS

The selected payload is29 hardware/build files,3 SM64 renderer sources and4
local app/core artifacts. It targets compact-v14: unchanged v7 plus the sole
final-fetch CPU ca6a2cb8 factor. Normal-build-v12, checked repro10 and actual
Scala regen10 reproduce the exact source. The tested normal app remains
bbb1c31a. The saved seed and fingerprint are not destinations; seed2 remains.
There are six os30-only source overrides. Quake2, SD, OS and loader are excluded.

All path/guard/journal/atomic replacement functions and the CLI are byte-identical
to v9. Load-payload, apply and rollback differ only by38-to36 destination counts
and the matching postcheck label. Rollback preparation changes only that count
and its inventory description. The exact allowlists, backup modes, intent-before-
replace fsync, readback verification and intervening-edit stop rules remain.

Qualification now selects actual fit-seed2/results and os30-s2 QSF/STA/assembler
outputs. Both slow corners must exceed90MHz and hold must be positive. Normal
selected-binding is checked against the actual fitter QSF and prepared hash;
the currently staged mapped and actual job QSF hashes agree. Reproduction,
normal/source and app-delivery evidence were rehashed without invoking recipes.

This seal does not qualify the running fit or choose a bitstream. No action was
imported or invoked, no simulation/Quartus process ran, no fresh production
snapshot is claimed, and no production or SD destination was written. All four
v10 action output directories are absent. Runtime fit and fresh destination
guards remain mandatory. The inherited no-concurrent-edit and preserved-
dependency assumptions remain; configured100MHz closure is distinct from the
comparative90MHz acceptance threshold.

Known delivery limitation: this version deliberately excludes both saved-seed
metadata files. The existing .seed.src fingerprint would be stale for the new
selected source. The numerical saved seed remains2 and the generated project
is exact, but a separately reviewed fingerprint-only normal/recipe revision is
required before promotion. Timing success alone must not trigger these v10
actions. This review does not alter the frozen recipes or seed metadata.
''')
    (OUT / 'manifest.json').write_text(json.dumps({str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}, indent=2) + '\n')
    print(json.dumps(dict(manifest_sha256=sha(OUT / 'manifest.json'), review_sha256=sha(OUT / 'review.json'), evidence_files_rehashed=checked), indent=2))


if __name__ == '__main__':
    main()
