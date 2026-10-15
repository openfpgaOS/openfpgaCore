#!/usr/bin/env python3
"""Package the immutable QGF1 experiment sources; never build or deploy them."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P = ROOT / 'docs/measurements/os30-psram32-20261009'
Q = P / 'queued-front-v1'
QUALIFIED = P / 'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'
APP_BASE = P / 'work/sm64'
APP_BASE_BINDING = HERE.parent / 'os30_triangle_front_20261010/manifest.json'
APP_PATHS = ['src/sdk/include/of_gpu.h', 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
             'src/sm64/sm64/src/pc/gfx/gfx_gpu.h', 'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
             'src/sm64/pocket/perf_pocket.c']
TEST_RECEIPTS = ['tests/metadata-v1/manifest.json', 'tests/wrapper-v1/manifest.json',
                 'tests/worker-v4/manifest.json', 'tests/repair-cpu-v1/manifest.json',
                 'tests/admission-v1/manifest.json', 'tests/lazy-v1/report.json',
                 'tests/worker-large-v1/manifest.json',
                 'review/app-v2-liveness.json']
RECIPE_SOURCES = ['build_model.py', 'prepare_physical.py', 'prepare_fixture.py',
                  'prepare_fixture_v2.py', 'replay.py', 'replay_v2.py', 'replay_v3.py',
                  'analyze.py', 'check_replays.py', 'build_large_fifo.py',
                  'observer-v2/gpu_observer.hpp', 'review/audit_native_v1.py',
                  'review/analyze-queue-pressure-v2.py',
                  'app-build/build_app.py', 'app-build-v2/build_app.py',
                  'tests/run_worker.py', 'tests/run_wrapper.py', 'tests/run_metadata.py',
                  'tests/run_repair_cpu.py', 'tests/check_admission.py', 'tests/check_lazy.py',
                  'tests/tb_queued_front.v', 'tests/test_queued_front.cpp',
                  'tests/tb_metadata.v', 'tests/test_metadata.cpp',
                  'tests/test_queue_wrapper.cpp', 'tests/raw_transaction_stub_body.vh',
                  'tests/admission_tail.c', 'tests/run_worker_large.py',
                  'tests/tb_queued_front_large.v', 'tests/test_queued_front_large.cpp']
EXCLUDED_INVENTORY = {'manifest.json', 'package-build.json', 'preflight.json'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def inventory():
    return {str(p.relative_to(HERE)): digest(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and p.name not in EXCLUDED_INVENTORY and '__pycache__' not in p.parts}


def bind(mapping, root=None):
    result = {}
    for name, expected in mapping.items():
        path = Path(name)
        if not path.is_absolute():
            assert root is not None, name
            path = root / path
        assert digest(path) == expected, str(path)
        result[str(path)] = expected
    return result


def patch(destination, name, baseline, postimages):
    pieces, files = [], {}
    for relative, source in sorted(postimages.items()):
        before = baseline / relative
        old = before.read_text() if before.exists() else ''
        new = source.read_text()
        if old == new:
            continue
        pieces.append(''.join(difflib.unified_diff(
            old.splitlines(True), new.splitlines(True),
            fromfile='a/' + relative if before.exists() else '/dev/null',
            tofile='b/' + relative)))
        recorded = ('$PACKAGE/' + str(source.relative_to(destination))
                    if source.is_relative_to(destination) else str(source))
        files[relative] = dict(before_sha256=digest(before) if before.exists() else None,
                               after_sha256=digest(source), postimage_source=recorded)
    (destination / name).write_text(''.join(pieces))
    return dict(base=str(baseline), files=files,
                roundtrip_contract='Zero-fuzz, zero-offset forward apply and exact reverse restoration')


def source_inputs(physical):
    baseline = read(QUALIFIED / 'inputs.json')
    candidate = read(physical / 'inputs.json')
    assert candidate['base_inputs_sha256'] == digest(QUALIFIED / 'inputs.json')
    assert candidate['base_qsf_sha256'] == baseline['qsf_sha256']
    evidence = bind(baseline['sources'], QUALIFIED / 'frozen')
    evidence.update(bind(candidate['sources'], physical / 'frozen'))
    for stage, item in [(QUALIFIED, baseline), (physical, candidate)]:
        assert digest(stage / 'build/ap_core.qsf') == item['qsf_sha256']
        if 'qpf_sha256' in item:
            assert digest(stage / 'build/ap_core.qpf') == item['qpf_sha256']
        for name in ['inputs.json', 'build/ap_core.qsf', 'build/ap_core.qpf']:
            evidence[str(stage / name)] = digest(stage / name)
    assert candidate['configured_mhz'] == 90 and candidate['seed'] == 3
    assert candidate['placement_effort_multiplier'] == 3.0
    assert not candidate['inherited_checkpoint']
    assert len(candidate['sources']) == 361
    changed = sorted(n for n in baseline['sources']
                     if candidate['sources'].get(n) != baseline['sources'][n])
    added = sorted(set(candidate['sources']) - set(baseline['sources']))
    assert changed == sorted(candidate['changed_sources'])
    assert added == sorted(candidate['added_sources'])
    assert len(changed) == 4 and len(added) == 4
    native = Path(candidate['native_model'])
    prepared = read(native / 'prepared.json')
    assert digest(native / 'prepared.json') == candidate['native_prepared_sha256']
    evidence.update(bind(prepared['source_sha256'], native))
    for name, record in candidate['native_copies'].items():
        source = Path(record['source'])
        assert digest(source) == record['sha256'] == candidate['sources'][name]
        evidence[str(source)] = record['sha256']
    physical_manifest = read(physical / 'manifest.json')
    evidence.update(bind(physical_manifest['inputs_sha256'], physical))
    evidence.update(bind(physical_manifest.get('external_inputs_sha256', {})))
    previous = read(APP_BASE_BINDING)
    base_hashes = previous['patches']['sm64.patch']['files']
    assert Path(previous['patches']['sm64.patch']['base']) == APP_BASE
    assert set(APP_PATHS) == set(base_hashes)
    for name in APP_PATHS:
        assert digest(APP_BASE / name) == base_hashes[name]['before_sha256']
        evidence[str(APP_BASE / name)] = digest(APP_BASE / name)
    evidence[str(APP_BASE_BINDING)] = digest(APP_BASE_BINDING)
    apps = {}
    for revision, app_name, build_name, patch_name in [
            ('v1', 'sm64', 'app-build', 'sm64-v1.patch'),
            ('v2', 'sm64-v2', 'app-build-v2', 'sm64.patch')]:
        app = Q / 'work' / app_name
        result = Q / build_name / 'build-result.json'
        item = read(result)
        assert item['pass_build']
        evidence.update(bind(item['source_sha256'], app))
        elf = Path(item['elf'])
        assert digest(elf) == item['elf_sha256']
        evidence[str(elf)] = item['elf_sha256']
        evidence[str(result)] = digest(result)
        apps[revision] = dict(source=str(app), build_result=str(result),
                              elf_sha256=item['elf_sha256'], patch=patch_name,
                              build_command=item['command'])
    return baseline, candidate, native, prepared, apps, evidence


def make_patches(destination, physical, baseline, candidate, apps):
    postimages = {'frozen/' + n: physical / 'frozen' / n
                  for n in candidate['changed_sources'] + candidate['added_sources']}
    original = (physical / 'build/ap_core.qsf').read_text()
    qsf = original.replace(str(physical / 'frozen'), '../frozen')
    assert qsf != original
    assert qsf.replace('../frozen', str(physical / 'frozen')) == original
    project = destination / 'project/ap_core.qsf'
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(qsf)
    postimages['build/ap_core.qsf'] = project
    patches = {'core.patch': patch(destination, 'core.patch', QUALIFIED, postimages)}
    post_sources = dict(baseline['sources'])
    for name in candidate['changed_sources'] + candidate['added_sources']:
        post_sources[name] = digest(physical / 'frozen' / name)
    assert post_sources == candidate['sources']
    for app in apps.values():
        source = Path(app['source'])
        patches[app['patch']] = patch(destination, app['patch'], APP_BASE,
                                      {name: source / name for name in APP_PATHS})
    return patches


def collect_report_bindings(value, evidence):
    """Validate explicit path/hash maps recursively, without guessing outcomes."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith('_sha256') and isinstance(item, dict):
                absolute = {n: h for n, h in item.items()
                            if isinstance(n, str) and Path(n).is_absolute()
                            and isinstance(h, str) and len(h) == 64}
                evidence.update(bind(absolute))
            collect_report_bindings(item, evidence)
    elif isinstance(value, list):
        for item in value:
            collect_report_bindings(item, evidence)


def counterfactual_patch(destination, evidence):
    baseline = Q / 'native-v1'
    large = Q / 'native-large-fifo-v1'
    receipt = read(large / 'counterfactual-manifest.json')
    assert receipt['status'] == 'NATIVE_BUILD_PASS'
    assert digest(baseline / 'prepared.json') == receipt['baseline_prepared_sha256']
    assert digest(large / 'prepared.json') == receipt['prepared_sha256']
    assert digest(large / 'capacity-only.patch') == receipt['patch_sha256']
    evidence.update(bind(read(large / 'prepared.json')['source_sha256'], large))
    relative = receipt['changed_source']
    assert digest(baseline / relative) == receipt['before_sha256']
    assert digest(large / relative) == receipt['after_sha256']
    for name in ['prepared.json', 'build.json', 'counterfactual-manifest.json', 'capacity-only.patch']:
        evidence[str(large / name)] = digest(large / name)
    name = 'counterfactual-2048.patch'
    item = patch(destination, name, baseline, {relative: large / relative})
    assert digest(destination / name) == receipt['patch_sha256']
    item['scope'] = 'Native capacity counterfactual only; not applied to packaged physical core.patch.'
    return name, item


def build(physical, preflight=False):
    baseline, candidate, native, prepared, apps, evidence = source_inputs(physical)
    if preflight:
        from verify import roundtrip
        with tempfile.TemporaryDirectory(prefix='qgf-package-preflight-') as temp:
            destination = Path(temp)
            patches = make_patches(destination, physical, baseline, candidate, apps)
            name, item = counterfactual_patch(destination, evidence)
            patches[name] = item
            for name, item in patches.items():
                roundtrip(destination, name, item)
        result = dict(status='PASS_SOURCE_INPUTS_AND_EXACT_PATCH_ROUNDTRIPS',
                      physical_stage=str(physical), sources=len(candidate['sources']),
                      changed_sources=candidate['changed_sources'], added_sources=candidate['added_sources'],
                      final_package_built=False, inputs_sha256=evidence,
                      tooling_sha256={n: digest(HERE / n) for n in ['package.py', 'verify.py', 'README.txt']})
        write(HERE / 'preflight.json', result)
        print(result['status'])
        return
    # Final reports are root-owned. Their absence must not produce a partial
    # final package or an inferred performance/qualification conclusion.
    required_reports = [Q / 'summary.json', Q / 'REPORT.txt']
    assert all(p.is_file() for p in required_reports), 'Root final summary.json and REPORT.txt are required'
    results = read(Q / 'summary.json')
    assert results.get('status'), 'Final summary must state its own outcome'
    patches = make_patches(HERE, physical, baseline, candidate, apps)
    name, item = counterfactual_patch(HERE, evidence)
    patches[name] = item
    shutil.copyfile(Q / 'summary.json', HERE / 'results.json')
    shutil.copyfile(Q / 'REPORT.txt', HERE / 'REPORT.txt')
    shutil.copyfile(Q / 'abi.json', HERE / 'abi.json')
    shutil.copyfile(P / 'bram-import-v1/snapshot-model/abi.json', HERE / 'bram-abi.json')
    shutil.copyfile(Q / 'native-large-fifo-v1/counterfactual-manifest.json', HERE / 'counterfactual-2048.json')
    reports = list(required_reports) + [Q / 'abi.json', P / 'bram-import-v1/snapshot-model/abi.json']
    tests = {}
    for name in TEST_RECEIPTS:
        source = Q / name
        item = read(source)
        assert str(item.get('status', '')).startswith('PASS'), name
        collect_report_bindings(item, evidence)
        target = HERE / 'receipts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        reports.append(source)
        tests[name] = dict(status=item['status'], scope=item.get('scope', item.get('limits')),
                           result=item.get('result'), sha256=digest(source))
    # Preserve all completed review receipts and their explicitly bound inputs;
    # they remain evidence, not independent promises of physical qualification.
    for source in sorted((Q / 'review').glob('*.json')):
        item = read(source)
        collect_report_bindings(item, evidence)
        target = HERE / 'receipts/review' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        reports.append(source)
    recipes = {}
    recipe_sources = RECIPE_SOURCES + [str(physical.relative_to(Q) / 'run_candidate.py')]
    for name in recipe_sources:
        source = Q / name
        target = HERE / 'recipes' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        recipes[name] = dict(source=str(source), sha256=digest(source))
        reports.append(source)
    # These source-only stubs are generated/extracted by the recipes and make
    # their limited test scope inspectable without packaging executables.
    for name in ['tests/metadata-v1/geometry_transform_light_stub.v',
                 'tests/wrapper-v1/geometry_raw_load_stub.v',
                 'tests/repair-cpu-v1/kernel.c', 'tests/admission-v1/test.c',
                 'tests/lazy-v1/test.c']:
        target = HERE / 'recipes' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Q / name, target)
        reports.append(Q / name)
    collect_report_bindings(results, evidence)
    for source in reports + [native / 'prepared.json', native / 'build.json',
                             physical / 'provenance.json', physical / 'preflight.json',
                             physical / 'static-port-audit.json', physical / 'manifest.json']:
        evidence[str(source)] = digest(source)
    write(HERE / 'recipes.json', dict(
        scope='Exact archived source recipes; original layout/toolchains/baselines/user game data required.',
        evidence_root=str(Q), recipes=recipes,
        app_build_commands={v: a['build_command'] for v, a in apps.items()},
        model_build_command=prepared['command'], physical_stage=str(physical),
        physical_launch_is_separate=True, no_binaries_or_game_assets=True))
    write(HERE / 'test-evidence.json', dict(
        scope='Native simulation and source tests; final physical/deployment status is recorded in results.json.',
        tests=tests))
    manifest = dict(status=results['status'],
                    scope='Source-only os30 QGF1/BIM2 experiment; packaging does not launch Quartus or deploy.',
                    patches=patches, apps=apps, physical_baseline=str(QUALIFIED),
                    physical_stage=str(physical), native_model=str(native),
                    qualified_baseline_sources_sha256=baseline['sources'],
                    physical_post_sources_sha256=candidate['sources'],
                    physical_qsf_sha256=candidate['qsf_sha256'],
                    qsf_changes='Exact staged QSF, with only its frozen-source prefix relocated to ../frozen.',
                    physical_settings_preserved=dict(configured_MHz=90, seed=3, placement_effort=3.0),
                    test_manifests=[str(Q / n) for n in TEST_RECEIPTS],
                    external_evidence_sha256=evidence, excluded_inventory=sorted(EXCLUDED_INVENTORY),
                    files=inventory())
    write(HERE / 'manifest.json', manifest)
    run = subprocess.run([sys.executable, str(HERE / 'verify.py'), '--patches', '--evidence'],
                         capture_output=True, text=True)
    write(HERE / 'package-build.json', dict(passed=run.returncode == 0,
        output=run.stdout, stderr=run.stderr, manifest_sha256=digest(HERE / 'manifest.json'),
        sources=len(candidate['sources']), core_patch_files=len(patches['core.patch']['files']),
        app_patch_files={v: len(patches[a['patch']]['files']) for v, a in apps.items()},
        no_assets_or_binaries_packaged=True, quartus_launched=False, deployed=False))
    print(run.stdout, end='')
    if run.returncode:
        print(run.stderr, file=sys.stderr)
        raise SystemExit(run.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', help='Validate inputs and temporary patch round trips only.')
    parser.add_argument('--physical-stage', type=Path, help='Use root-selected staged physical source; default summary selection or physical-v1.')
    args = parser.parse_args()
    selection = read(Q / 'summary.json').get('package_physical_stage') if (Q / 'summary.json').exists() else None
    physical = args.physical_stage or (Path(selection) if selection else Q / 'physical-v1')
    build(physical.resolve(), preflight=args.preflight)


if __name__ == '__main__':
    main()
