#!/usr/bin/env python3
"""Source-only mechanical promotion-v7 review. Never run a promotion action."""
import ast
import difflib
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
H = Path(__file__).parent
OUT = P / 'promotion-transaction-review-v7'
CPU = '482e13475702808f40d5bad7f6b6e6af9331ed5c895d99d7bf4630a3b047a303'
APP = 'bbb1c31adce29de9f33be6df1cfd2c126714b8017e3d232b1cb9c77c12d9c405'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
read = lambda p: json.loads(p.read_text())


def main():
    previous = P / 'promotion-transaction-review-v6'
    for name, digest in read(previous / 'manifest.json')['files'].items():
        assert sha(previous / name) == digest, name
    for name, digest in read(previous / 'manifest.json')['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
    old_tx = H / 'phase10_promotion_transaction_v6.py'
    assert sha(old_tx) == sha(previous / old_tx.name)
    replacements = [
        ('promotion_plan_v6', 'promotion_plan_v7'),
        ('promotion-plan-v6', 'promotion-plan-v7'),
        ('promotion-rollback-v6', 'promotion-rollback-v7'),
        ('promotion-payload-v6', 'promotion-payload-v7'),
        ('promotion-transaction-v6', 'promotion-transaction-v7'),
        ('sm64-incremental-local-v6.zip', 'sm64-incremental-local-v7.zip'),
        ('normal-build-integration-v8', 'normal-build-integration-v9'),
        ('compact-lod5-core-v11', 'compact-lod5-core-v12'),
        ('compact-v11', 'compact-v12'),
        ('cpu-os30-regenerate-v7', 'cpu-os30-regenerate-v8'),
        ('cpu-os30-repro-v7', 'cpu-os30-repro-v8'),
        ('cpu-aligner-enable-v9', 'cpu-writeback-plane-v10'),
        ('b511dba179677c9f2b3fe433caeb0c5399a4fe1dc688e5d85587f6888d9cbeab', CPU),
        ('generated CPU b511dba1', 'generated CPU 482e1347'),
        ('== 29', '== 31'), ('only29', 'only31'), (':29 hardware', ':31 hardware'),
        ('is29 openfpga', 'is31 openfpga'),
        ('== 36', '== 38'), ('==36', '==38'), ('=36', '=38'),
        ('==32 and len(backup', '==34 and len(backup'),
        ('Exactly36', 'Exactly38'), ('all36_outputs', 'all38_outputs'),
    ]
    sources = []
    patch = ''
    for kind in ('plan', 'rollback', 'transaction'):
        old = H / f'phase10_promotion_{kind}_v6.py'
        new = H / f'phase10_promotion_{kind}_v7.py'
        assert sha(old) == sha(previous / old.name), kind
        original = old.read_text()
        normalized = original
        for before, after in replacements:
            normalized = normalized.replace(before, after)
        if kind == 'plan':
            anchor = "    prep = read(NORMAL / 'prepared.json')\n"
            added = "    assert len(prep['overrides']) == 8 and len(prep['proposed_sha256']) == 31\n"
            assert normalized.count(anchor) == 1
            normalized = normalized.replace(anchor, anchor + added)
            anchor = "                P / 'cache-write-lod5-coupled-v2/manifest.json',"
            added = ("                P / 'cpu-writeback-plane-review-v10/manifest.json',\n"
                     "                P / 'texture-read-address-v1/manifest.json', P / 'texture-read-address-source-review-v1/manifest.json',\n"
                     "                P / 'audio-accum-carry-v1/manifest.json', P / 'audio-accum-carry-review-v1/manifest.json',\n")
            assert normalized.count(anchor) == 1
            normalized = normalized.replace(anchor, added + anchor)
        if kind == 'transaction':
            anchor = "    require(n['selected_prepared_sha256']==sha(FIT/'prepared.json'),'Normal build targets another fit')\n"
            added = "    require(len(n['overrides'])==8 and len(n['proposed_sha256'])==31,'Normal source allowlist shape differs')\n"
            assert normalized.count(anchor) == 1
            normalized = normalized.replace(anchor, anchor + added)
        assert normalized == new.read_text(), kind
        ast.parse(new.read_text(), filename=str(new))
        patch += ''.join(difflib.unified_diff(original.splitlines(True), new.read_text().splitlines(True),
                                            fromfile=old.name, tofile=new.name))
        sources += [old, new]

    normal = P / 'normal-build-integration-v9'
    core = P / 'compact-lod5-core-v12'
    n = read(normal / 'prepared.json')
    c = read(core / 'prepared.json')
    cpu_path = 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
    assert n['selected_prepared_sha256'] == sha(core / 'prepared.json')
    assert c['source_sha256'][cpu_path] == n['proposed_sha256'][cpu_path] == CPU
    assert read(normal / 'result.json')['passed']
    assert read(core / 'independent-review.json')['prepared_sha256'] == sha(core / 'prepared.json')
    assert read(P / 'cpu-os30-regenerate-v8/result.json')['output_sha256'] == CPU
    assert len(n['proposed_sha256']) == 31 and len(n['overrides']) == 8
    delivery = P / 'load-submit-delivery-v1'
    assert sha(delivery / 'payload/Assets/sm64/common/sm64.elf') == APP
    assert len(read(delivery / 'sources.json')['changed_files']) == 3
    for label in ('promotion-plan-v7', 'promotion-rollback-v7', 'promotion-payload-v7', 'promotion-transaction-v7'):
        assert not (P / label).exists(), label

    OUT.mkdir(exist_ok=False)
    shutil.copy2(__file__, OUT / 'recipe.py')
    for source in sources:
        shutil.copy2(source, OUT / source.name)
    (OUT / 'mechanical.patch').write_text(patch)
    report = dict(
        passed=True, passed_source_review=True, mechanical_changes_only=True,
        candidate_cpu_sha256=CPU, selected_app_sha256=APP,
        preparation_or_apply_executed=False, routed_qualification_pending=True,
        destination_count=38, hardware_source_count=31, os30_override_count=8,
        inherited_fixes=['Selected raw bytes must match hashes.rbf in final-manifest-bound results.json.',
                         'Durable intent and interrupted-journal rollback reconciliation are unchanged.'],
        findings=[
            'All three recipes exactly equal the reviewed v6 sources after enumerated version/source/hash/count changes, added evidence bindings, and strengthened normal-build allowlist shape checks.',
            'Selected compact-v12/normal-build-v9 binds CPU482e, its checked repro8 and real Scala regenerate8; the selected bbb1c31a app is unchanged.',
            'The only added destination paths are the reviewed os30 audio_mixer and gpu_tex_cache shadows. The eight explicit source overrides remain os30-only.',
            'The allowlist is31 hardware/build files plus3 SM64 renderer files plus2 app artifacts plus2 core artifacts, with38 unique destinations required.',
            'File ancestry/regular-file guards,132 core and6747 SM64 input guards, exact raw assembler provenance, atomic replace/journaling and rollback hash protection are unchanged.',
            'No new SD/Quake2 target, build, fit, seed or clock operation is introduced. Both slow corners >90MHz and positive hold remain mandatory before payload preparation.'
        ],
        limits=[
            'Mechanical/source review only. No prepare, apply, rollback, bit reversal, build, simulation or extra test ran.',
            'The fit result and later frozen payload require their separate exact bindings; this review does not claim routed qualification.',
            'Prior transaction limitations remain: quiescent destinations and recovery evidence availability; harmless orphan temporary/empty-directory residues can remain after interruption.',
            'Configured100MHz timing closure remains distinct from comparative >90MHz qualification.'
        ])
    (OUT / 'review.json').write_text(json.dumps(report, indent=2) + '\n')
    (OUT / 'REPORT.txt').write_text(
        'Promotion v7 mechanical binding review: PASS\n\n'
        'Exact normalization against the reviewed v6 recipes permits only target/version/hash/count changes, additional CPU/texture/audio proof bindings, '
        'and31-file/eight-override allowlist checks. All mutation, provenance, journal and rollback logic is inherited unchanged.\n\n'
        'The source payload is compact-v12/normal-build-v9 with CPU482e and the unchanged bbb1c31a app. '
        'The31 hardware/build,3 renderer and4 artifact destinations total38. No action was run, and all four v7 action output directories remain absent. '
        'The selected fit is still a separate gate, as is any later payload preparation or install.\n')
    dependencies = sources + [
        previous / 'manifest.json', normal / 'manifest.json', normal / 'prepared.json',
        core / 'prepared.json', core / 'independent-review.json',
        P / 'cpu-os30-repro-v8/manifest.json', P / 'cpu-os30-regenerate-v8/manifest.json',
        P / 'cpu-writeback-plane-v10/manifest.json', P / 'geometry-clip-write-v1/manifest.json',
        P / 'geometry-clip-write-review-v1/manifest.json', P / 'gpu-ring-equality-v16/manifest.json',
        P / 'gpu-ring-equality-source-review-v16/manifest.json', delivery / 'manifest.json',
        delivery / 'sources.json', P / 'cpu-writeback-plane-review-v10/manifest.json',
        P / 'texture-read-address-v1/manifest.json', P / 'texture-read-address-source-review-v1/manifest.json',
        P / 'audio-accum-carry-v1/manifest.json', P / 'audio-accum-carry-review-v1/manifest.json',
        previous / 'binding-manifest.json', Path(__file__),
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
