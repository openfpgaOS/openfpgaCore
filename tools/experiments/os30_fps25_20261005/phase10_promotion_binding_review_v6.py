#!/usr/bin/env python3
"""Source-only mechanical promotion-v6 review. Never run a promotion action."""
import ast
import difflib
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
H = Path(__file__).parent
OUT = P / 'promotion-transaction-review-v6'
CPU = 'b511dba179677c9f2b3fe433caeb0c5399a4fe1dc688e5d85587f6888d9cbeab'
APP = 'bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    previous = P / 'promotion-transaction-review-v5'
    for name, digest in read(previous / 'manifest.json')['files'].items():
        assert sha(previous / name) == digest, name
    for name, digest in read(previous / 'manifest.json')['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
    old_tx = H / 'phase10_promotion_transaction_v5.py'
    assert sha(old_tx) == sha(previous / 'phase10_promotion_transaction_v5.py')
    replacements = [
        ('promotion_plan_v5', 'promotion_plan_v6'),
        ('promotion-plan-v5', 'promotion-plan-v6'),
        ('promotion-rollback-v5', 'promotion-rollback-v6'),
        ('promotion-payload-v5', 'promotion-payload-v6'),
        ('promotion-transaction-v5', 'promotion-transaction-v6'),
        ('sm64-incremental-local-v5.zip', 'sm64-incremental-local-v6.zip'),
        ('normal-build-integration-v7', 'normal-build-integration-v8'),
        ('compact-lod5-core-v10', 'compact-lod5-core-v11'),
        ('compact-v10', 'compact-v11'),
    ]
    sources = []
    patch = ''
    for kind in ('plan', 'rollback', 'transaction'):
        old = H / f'phase10_promotion_{kind}_v5.py'
        new = H / f'phase10_promotion_{kind}_v6.py'
        assert sha(old) == sha(previous / old.name), kind
        original = old.read_text()
        normalized = original
        for before, after in replacements:
            normalized = normalized.replace(before, after)
        if kind == 'plan':
            anchor = "                P / 'cache-write-lod5-coupled-v2/manifest.json',"
            added = "                P / 'gpu-ring-equality-v16/manifest.json', P / 'gpu-ring-equality-source-review-v16/manifest.json',\n"
            assert normalized.count(anchor) == 1
            normalized = normalized.replace(anchor, added + anchor)
        assert normalized == new.read_text(), kind
        ast.parse(new.read_text(), filename=str(new))
        patch += ''.join(difflib.unified_diff(original.splitlines(True), new.read_text().splitlines(True),
                                            fromfile=old.name, tofile=new.name))
        sources += [old, new]

    normal = P / 'normal-build-integration-v8'
    core = P / 'compact-lod5-core-v11'
    n = read(normal / 'prepared.json')
    c = read(core / 'prepared.json')
    cpu_path = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    assert n['selected_prepared_sha256'] == sha(core / 'prepared.json')
    assert c['source_sha256'][cpu_path] == n['proposed_sha256'][cpu_path] == CPU
    assert read(normal / 'result.json')['passed']
    assert read(core / 'independent-review.json')['prepared_sha256'] == sha(core / 'prepared.json')
    assert read(P / 'cpu-os30-regenerate-v7/result.json')['output_sha256'] == CPU
    assert len(n['proposed_sha256']) == 29
    delivery = P / 'load-submit-delivery-v1'
    assert sha(delivery / 'payload/Assets/sm64/common/sm64.elf') == APP
    assert len(read(delivery / 'sources.json')['changed_files']) == 3
    for label in ('promotion-plan-v6', 'promotion-rollback-v6', 'promotion-payload-v6', 'promotion-transaction-v6'):
        assert not (P / label).exists(), label

    OUT.mkdir(exist_ok=False)
    shutil.copy2(__file__, OUT / 'recipe.py')
    for source in sources:
        shutil.copy2(source, OUT / source.name)
    (OUT / 'mechanical.patch').write_text(patch)
    report = dict(
        passed_source_review=True, mechanical_changes_only=True,
        candidate_cpu_sha256=CPU, selected_app_sha256=APP,
        preparation_or_apply_executed=False, routed_qualification_pending=True,
        destination_count=36, source_allowlist_unchanged=True,
        inherited_fixes=['Selected raw bytes must match hashes.rbf in final-manifest-bound results.json.',
                         'Durable intent and interrupted-journal rollback reconciliation are unchanged.'],
        findings=[
            'All three recipes equal the reviewed v5 sources after only enumerated private labels, source/evidence paths and added GPU ring-equality proof bindings.',
            'CPU b511dba, repro7/regenerate7, positive hold and all transaction predicates are unchanged.',
            'The current sealed normal build selects exact compact-v11 CPU b511dba; actual Scala regeneration-v7 matches. The selected app remains bbb1c31a.',
            'The allowlist remains 29 hardware/build files, 3 SM64 renderer files, 2 normal app paths and 2 SM64 core paths. No SD or Quake2 output is introduced.',
            'Both-corner >90 MHz, positive hold, exact source closure and raw/reversed assembler provenance still gate private payload preparation. No fit result is inferred here.',
        ],
        limits=[
            'This is mechanical/source review only. No prepare, apply, rollback, bit reversal, build or extra test was run.',
            'The prior review limitations remain: quiescent destinations, recovery evidence availability, and harmless orphan temporary/empty-directory residues after interruption.',
            'The v10 routed result and later frozen payload manifest must be inspected separately before any install. Configured 100 MHz timing closure remains distinct from the comparative >90 MHz gate.',
        ],
    )
    (OUT / 'review.json').write_text(json.dumps(report, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text(
        'Promotion v6 mechanical binding review: PASS\n\n'
        'Only candidate labels, exact CPU/source/evidence bindings and private output names change from the reviewed v5 recipes. '
        'The plan additionally binds the GPU ring-equality component and independent review. '
        'All source closure, selected assembler hash, bit-reversal, before-guard, allowlist, atomic replacement and rollback checks remain exact.\n\n'
        'The source payload is compact-v11/normal-build-v7 with CPU b511dba and the unchanged bbb1c31a app. '
        'No recipe action was run; no v6 plan, backup, payload or transaction directory exists. '
        'Routed qualification and a later frozen payload remain separate gates.\n')
    dependencies = sources + [
        previous / 'manifest.json', normal / 'manifest.json', normal / 'prepared.json',
        core / 'prepared.json', core / 'independent-review.json',
        P / 'cpu-os30-repro-v7/manifest.json', P / 'cpu-os30-regenerate-v7/manifest.json',
        P / 'cpu-aligner-enable-v9/manifest.json', P / 'geometry-clip-write-v1/manifest.json',
        P / 'geometry-clip-write-review-v1/manifest.json', P / 'gpu-ring-equality-v16/manifest.json',
        P / 'gpu-ring-equality-source-review-v16/manifest.json', delivery / 'manifest.json',
        delivery / 'sources.json', Path(__file__),
    ]
    manifest = dict(
        passed=True,
        files={str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*') if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in dependencies},
    )
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
