#!/usr/bin/env python3
"""Assemble the pinned native-v7 / F18 MESH3 and F19 MESH4 source review bundle."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P = ROOT / 'docs/measurements/os30-psram32-20261009'
I = P / 'mesh-integrated-v1'
N = I / 'native-v7'
BASE = P / 'cached-cpu-v1/native-sdram-v2'
PHYSICAL = P / 'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'
APP = I / 'work-app-v3'
APP_BASE = P / 'queued-front-v1/work/sm64-v2'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
manifest = {'status': 'UNQUALIFIED_SOURCE_REVIEW_NOT_DEPLOYED',
            'performance_status': 'NO_NET_BENEFIT_FOR_SELECTED_512_WORD_IMPLEMENTATION',
            'native_revision': str(N), 'app_revision': str(APP),
            'native_baseline': str(BASE), 'physical_baseline': str(PHYSICAL),
            'patches': {}, 'copied_inputs': {}, 'external_evidence_sha256': {},
            'receipt_input_relocations': []}

def put(source, name):
    source = Path(source).resolve()
    target = HERE / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    manifest['copied_inputs'][name] = {'source': str(source), 'sha256': sha(source)}

def pin(path):
    path = Path(path).resolve()
    manifest['external_evidence_sha256'][str(path)] = sha(path)

def patch(name, base, pairs):
    records, text = {}, ''
    for relative, post in sorted(pairs.items()):
        pre = base / relative
        before = pre.read_text() if pre.exists() else ''
        after = post.read_text()
        if before == after:
            continue
        text += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                    fromfile='a/' + relative if pre.exists() else '/dev/null', tofile='b/' + relative))
        records[relative] = {'before_sha256': sha(pre) if pre.exists() else None,
                             'after_sha256': sha(post), 'postimage_source': str(post)}
        pin(post)
    (HERE / name).write_text(text)
    manifest['patches'][name] = {'base': str(base), 'files': records}

prepared = json.loads((N / 'prepared.json').read_text())
for name, expected in prepared['source_sha256'].items():
    assert sha(N / name) == expected, name
native_pairs = {name: N / name for name in prepared['changed_inputs']}
patch('native-v7.patch', BASE, native_pairs)
for name, source in native_pairs.items():
    if '/src/fpga/common/' in name:
        put(source, 'rtl/' + source.name)

top = 'src/fpga/targets/pocket/os30/core_top.v'
patch('physical-top.patch', PHYSICAL / 'frozen', {top: I / 'physical-top/core_top.v'})
app_files = ['src/sdk/include/of_gpu.h', 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
             'src/sm64/sm64/src/pc/gfx/gfx_gpu.h', 'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
             'src/sm64/pocket/perf_pocket.c']
pairs = {name: APP / name for name in app_files}
patch('sm64-integration.patch', APP_BASE, pairs)
patch('sm64-v1-to-v3.patch', I / 'work-app', pairs)
patch('sm64-v2-to-v3.patch', I / 'work-app-v2', pairs)
for name in app_files:
    pin(APP_BASE / name)
    pin(APP / name)

copy_map = {
    'abi-v2.json': 'abi.json',
    'build_native.py': 'recipes/build_native.py',
    'build_pocket.py': 'recipes/build_pocket.py',
    'native-v7/prepared.json': 'receipts/native-v7/prepared.json',
    'native-v7/build.json': 'receipts/native-v7/build.json',
    'work-core/new-sources.json': 'recipes/new-sources.json',
    'work-core/rtl-build-notes-v7.json': 'reviews/rtl-build-notes-v7.json',
    'work-core/tests/pre-large-v8-sources/run_wrapper.py': 'recipes/tests/run_wrapper.py',
    'work-core/tests/pre-large-v8-sources/test_wrapper.cpp': 'tests/test_wrapper.cpp',
    'work-core/tests/run_scene_split.py': 'recipes/tests/run_scene_split.py',
    'work-core/tests/test_scene_split.cpp': 'tests/test_scene_split.cpp',
    'work-core/tests/wrapper-v7b/manifest.json': 'receipts/tests/wrapper-v7b.json',
    'work-core/tests/scene-split-v7/manifest.json': 'receipts/tests/scene-split-v7.json',
    'validation/queue-test-v3/run.py': 'recipes/tests/run_queue.py',
    'validation/queue-test-v3/test.cpp': 'tests/test_queue.cpp',
    'validation/queue-test-v3/manifest.json': 'receipts/tests/queue-v3.json',
    'validation/prepare_fixture.py': 'recipes/prepare_fixture.py',
    'validation/replay.py': 'recipes/replay.py',
    'validation/check_replays_v2.py': 'recipes/check_replays_v2.py',
    'validation/mesh3_hooks_v3.py': 'recipes/mesh3_hooks_v3.py',
    'validation/mesh3_lifetime_appv3.py': 'recipes/mesh3_lifetime_appv3.py',
    'validation/appv3-publication-review.json': 'reviews/appv3-publication-review.json',
    'validation/app-source-review-v1.json': 'reviews/app-source-review-v1.json',
    'validation/packet-domain-proof-v1.json': 'reviews/packet-domain-proof-v1.json',
    'analysis/alm-audit-v7.json': 'reviews/alm-audit-v7.json',
    'analysis/qualified-firmware-ring-consumers.json': 'reviews/qualified-firmware-ring-consumers.json',
    'analysis/physical-v5-source-review.json': 'reviews/physical-v5-source-review.json',
    'work-app-v3/evidence/app-build-v3/build_app.py': 'recipes/app-build-v3/build_app.py',
    'work-app-v3/evidence/app-build-v3/build-result.json': 'receipts/app-build-v3.json',
    'work-app-v3/evidence/batching-liveness-v1/test_wait.py': 'recipes/tests/test_wait.py',
    'work-app-v3/evidence/batching-liveness-v1/actual_wait_test.c': 'tests/actual_wait_test.c',
    'work-app-v3/evidence/batching-liveness-v1/result.json': 'receipts/tests/app-batching.json',
    'work-app-v3/evidence/batching-liveness-v1/blocking-paths.json': 'reviews/blocking-paths.json',
    'validation/candidate-v7-appv3/check-independent-v2.json': 'receipts/native/appv3-strict.json',
    'validation/candidate-v7-appv3/checker-supersession.json': 'receipts/native/appv3-checker-supersession.json',
    'validation/control-v4/check-independent.json': 'receipts/native/appv4-control-strict.json',
    'validation/candidate-v7-appv4/check-independent-v3.json': 'receipts/native/appv4-candidate-strict.json',
    'validation/candidate-v7-appv4/checker-supersession.json': 'receipts/native/appv4-checker-supersession.json',
    'validation/source-proof-v4.json': 'receipts/native/appv4-source-proof.json',
    'validation/check_source_proof_v1.py': 'recipes/check_source_proof_v1.py',
    'validation/source_proof_hooks_v1.py': 'recipes/source_proof_hooks_v1.py',
    'analysis/adaptive-v4-conclusion.json': 'reviews/adaptive-v4-conclusion.json',
    'analysis/adaptive-v4-conclusion.txt': 'reviews/adaptive-v4-conclusion.txt',
    'analysis/queue-credit-v7-appv4.json': 'reviews/queue-credit-v7-appv4.json',
    'analysis/ceiling-comparison-v9.json': 'reviews/ceiling-comparison-v9.json',
    'analysis/summarize_adaptive_v4.py': 'recipes/summarize_adaptive_v4.py',
    'analysis/summarize_adaptive_v4_final.py': 'recipes/summarize_adaptive_v4_final.py',
    'analysis/analyze_mtf3.py': 'recipes/analyze_mtf3.py',
}
for source, target in copy_map.items():
    put(I / source, target)

def bind_receipt_hashes(value):
    """Bind external evidence without copying large traces or game assets."""
    if isinstance(value, dict):
        for key, child in value.items():
            if (isinstance(child, str) and len(child) == 64 and
                    all(c in '0123456789abcdef' for c in child) and
                    isinstance(key, str) and
                    (key.startswith('/') or key.startswith(('docs/', 'tools/')))):
                path = Path(key) if key.startswith('/') else ROOT / key
                assert sha(path) == child, key
                pin(path)
            else:
                bind_receipt_hashes(child)
    elif isinstance(value, list):
        for child in value:
            bind_receipt_hashes(child)

for target in ['receipts/native/appv3-strict.json',
               'receipts/native/appv4-control-strict.json',
               'receipts/native/appv4-candidate-strict.json',
               'receipts/native/appv4-source-proof.json',
               'reviews/ceiling-comparison-v9.json',
               'reviews/adaptive-v4-conclusion.json']:
    receipt = json.loads((HERE / target).read_text())
    bind_receipt_hashes(receipt)
    for source in receipt.get('checker_sources_sha256', {}):
        relative = Path(source).relative_to(P)
        put(Path(source), 'recipes/checker-dependencies/' + str(relative))
    if target.startswith('receipts/native/'):
        assert receipt['status'].startswith('PASS'), target

manifest['measured_comparisons'] = {}
for name in ['paired-v5', 'paired-v6', 'paired-v7', 'paired-v8', 'paired-v9',
             'paired-v7-appv3', 'paired-v7-appv4']:
    path = I / 'analysis' / (name + '.json')
    pair = json.loads(path.read_text())
    assert pair['control']['elf_sha256'] == pair['candidate']['elf_sha256']
    pin(path)
    manifest['measured_comparisons'][name] = {
        'source': str(path), 'sha256': sha(path),
        'elf_sha256': pair['control']['elf_sha256'],
        'control_intervals': [x['elapsed_cycles'] for x in pair['control']['phases'].values()],
        'candidate_intervals': [x['elapsed_cycles'] for x in pair['candidate']['phases'].values()],
        'scope': 'Two measured drained native intervals; not device FPS or physical qualification.'}

# App v4 is an addition: preserve every v3 source/receipt and keep its base
# identity explicit. It does not replace the selected native-v7 RTL.
V4 = I / 'work-app-v4'
v4_dir = HERE / 'additions/app-v4'
v4_dir.mkdir(parents=True, exist_ok=True)
patch('additions/app-v4/sm64-v3-to-v4.patch', APP,
      {name: V4 / name for name in app_files})
v4_files = {
    V4 / 'evidence/app-build-v4/build_app.py': 'build_app.py',
    V4 / 'evidence/app-build-v4/build-result.json': 'build-result.json',
    V4 / 'evidence/adaptive-feed-v1/test_feed.py': 'test_feed.py',
    V4 / 'evidence/adaptive-feed-v1/actual_feed_test.c': 'actual_feed_test.c',
    V4 / 'evidence/adaptive-feed-v1/result.json': 'test-result.json',
    I / 'validation/appv4-adaptive-review.json': 'independent-review.json',
    I / 'validation/mesh3_lifetime_appv4.py': 'mesh3_lifetime_appv4.py',
}
for source, target in v4_files.items():
    put(source, 'additions/app-v4/' + target)
v4_build = json.loads((V4 / 'evidence/app-build-v4/build-result.json').read_text())
for name, expected in v4_build['source_sha256'].items():
    assert sha(V4 / name) == expected, name
    pin(V4 / name)
pin(Path(v4_build['elf']))
manifest['app_additions'] = [{
    'name': 'F19 MESH4 adaptive feed', 'path': 'additions/app-v4',
    'source': str(V4), 'elf_sha256': v4_build['elf_sha256'],
    'exact_base': str(APP), 'native_revision_unchanged': str(N),
    'status': 'NATIVE_CORRECT_NO_NET_SPEEDUP_UNQUALIFIED_NOT_DEPLOYED',
    'changes': ['Publish small CPU-ring batches when published occupancy is below 128 words',
                'Keep 512-word/sync override and force publication on DMA backend',
                'V3 ownership-wait helper unchanged', 'F19 MESH4 label'],
    'not_deployed': True,
    'results': '../../results.txt',
    'strict_candidate_receipt': '../../receipts/native/appv4-candidate-strict.json',
    'strict_control_receipt': '../../receipts/native/appv4-control-strict.json',
}]
addition_manifest = dict(manifest['app_additions'][0])
addition_manifest['patch'] = manifest['patches']['additions/app-v4/sm64-v3-to-v4.patch']
addition_manifest['files_sha256'] = {
    str(p.relative_to(v4_dir)): sha(p) for p in sorted(v4_dir.rglob('*'))
    if p.is_file() and p.name != 'manifest.json'}
(v4_dir / 'manifest.json').write_text(json.dumps(addition_manifest, indent=2) + '\n')
for path in [BASE / 'prepared.json', PHYSICAL / 'inputs.json', PHYSICAL / 'timing-qualification.json',
             I / 'work-app/evidence/app-build-v1/build-result.json',
             I / 'work-app-v2/evidence/app-build-v2/build-result.json',
             APP / 'evidence/app-build-v3/build-result.json']:
    pin(path)

# Preserve historical receipts unchanged. Resolve renamed/staging inputs by
# exact digest to their immutable frozen copy, and explicitly record that fact.
candidates = [p for p in (N / 'frozen').rglob('*') if p.is_file()]
candidates += [Path(v['source']) for v in manifest['copied_inputs'].values()]
by_hash = {sha(p): p for p in candidates}
for name in ['receipts/tests/wrapper-v7b.json', 'receipts/tests/scene-split-v7.json',
             'receipts/tests/queue-v3.json', 'receipts/tests/app-batching.json',
             'additions/app-v4/test-result.json']:
    receipt = json.loads((HERE / name).read_text())
    for original, expected in receipt.get('inputs_sha256', receipt.get('input_sha256', {})).items():
        current = Path(original)
        if not current.is_file() or sha(current) != expected:
            assert expected in by_hash, (name, original, expected)
            current = by_hash[expected]
            manifest['receipt_input_relocations'].append({
                'receipt': name, 'historical_path': original, 'sha256': expected,
                'immutable_replacement': str(current),
                'reason': 'Historical mutable staging path advanced; identical tested bytes retained in frozen/archive copy.'})
        pin(current)

manifest['native_source_sha256'] = prepared['source_sha256']
manifest['excluded_inventory'] = ['manifest.json', 'verification.json', 'verification-v4.json']
manifest['files'] = {str(p.relative_to(HERE)): sha(p) for p in sorted(HERE.rglob('*'))
                     if p.is_file() and str(p.relative_to(HERE)) not in manifest['excluded_inventory'] and '__pycache__' not in p.parts}
(HERE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'path': str(HERE), 'files': sum(p.is_file() for p in HERE.rglob('*')),
                  'bytes': sum(p.stat().st_size for p in HERE.rglob('*') if p.is_file()),
                  'manifest_sha256': sha(HERE / 'manifest.json')}))
