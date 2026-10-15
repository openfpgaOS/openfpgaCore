#!/usr/bin/env python3
"""Rebuild this source-only package from immutable local experiment inputs."""
import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
P = ROOT / 'docs/measurements/os30-psram32-20261009'
T = P / 'triangle-front-v1'
I = P / 'bram-import-v1'
QUALIFIED = P / 'work/core/docs/measurements/psram32-build-upload-reset-placeeffort-v1'
APP_BASE = P / 'work/sm64'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')


def inventory():
    return {str(p.relative_to(HERE)): digest(p) for p in sorted(HERE.rglob('*'))
            if p.is_file() and p.name != 'manifest.json' and '__pycache__' not in p.parts}


def patch(name, baseline, postimages):
    pieces = []
    files = {}
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
        recorded_source = ('$PACKAGE/' + str(source.relative_to(HERE))
                           if source.is_relative_to(HERE) else str(source))
        files[relative] = dict(before_sha256=digest(before) if before.exists() else None,
                               after_sha256=digest(source), postimage_source=recorded_source)
    (HERE / name).write_text(''.join(pieces))
    return dict(base=str(baseline), files=files,
                roundtrip_contract='Exact zero-fuzz forward apply and reverse restore; every byte hashed')


def build():
    baseline = read(QUALIFIED / 'inputs.json')
    model = T / 'native-v1'
    prepared = read(model / 'prepared.json')
    for name, expected in prepared['source_sha256'].items():
        assert digest(model / name) == expected, name
    for name, expected in baseline['sources'].items():
        assert digest(QUALIFIED / 'frozen' / name) == expected, name
    assert digest(QUALIFIED / 'build/ap_core.qsf') == baseline['qsf_sha256']
    snapshot = I / 'physical-snapshot-v1'
    reference = read(snapshot / 'inputs.json')
    assert reference['base_inputs_sha256'] == digest(QUALIFIED / 'inputs.json')
    top = 'src/fpga/targets/pocket/os30/core_top.v'
    assert digest(snapshot / 'frozen' / top) == reference['sources'][top]
    names = ['gpu_core.v', 'phase10_load_alias.v', 'geometry_import_view.v',
             'geometry_bram_import.v', 'geometry_import_convert.v',
             'geometry_float_cull_mem.v', 'geometry_triangle_preflight.v']
    postimages = {'frozen/src/fpga/common/os30/' + name:
                  model / 'frozen/src/fpga/common' / name for name in names}
    postimages['frozen/' + top] = snapshot / 'frozen' / top
    qsf = (QUALIFIED / 'build/ap_core.qsf').read_text()
    before_settings = qsf
    qsf = qsf.replace(str(QUALIFIED / 'frozen'), '../frozen')
    assert qsf != before_settings
    assert 'set_global_assignment -name SEED 3\n' in qsf
    assert 'set_global_assignment -name PLACEMENT_EFFORT_MULTIPLIER 3.0\n' in qsf
    extra = '\n# Isolated os30 BIM2 + TFP1 prototype; physical fit/timing unqualified.\n'
    for macro in ['INCLUDE_GPU_BRAM_IMPORT', 'INCLUDE_TRIANGLE_PREFLIGHT']:
        extra += 'set_global_assignment -name VERILOG_MACRO ' + macro + '\n'
    for name in names[2:]:
        extra += 'set_global_assignment -name VERILOG_FILE ../frozen/src/fpga/common/os30/' + name + '\n'
    assert qsf.replace('../frozen', str(QUALIFIED / 'frozen')) == before_settings
    (HERE / 'project').mkdir(exist_ok=True)
    qsf_path = HERE / 'project/ap_core.qsf'
    qsf_path.write_text(qsf + extra)
    postimages['build/ap_core.qsf'] = qsf_path
    patches = {'core.patch': patch('core.patch', QUALIFIED, postimages)}
    apps = {}
    app_paths = ['src/sdk/include/of_gpu.h', 'src/sm64/sm64/src/pc/gfx/gfx_gpu.c',
                 'src/sm64/sm64/src/pc/gfx/gfx_gpu.h', 'src/sm64/sm64/src/pc/gfx/gfx_pc.c',
                 'src/sm64/pocket/perf_pocket.c']
    for revision, patch_name in [('v3', 'sm64.patch'), ('v2', 'sm64-v2.patch')]:
        app = T / ('work/sm64-' + revision)
        result = T / ('app-build-' + revision) / 'build-result.json'
        build_result = read(result)
        assert build_result['pass_build']
        assert digest(Path(build_result['elf'])) == build_result['elf_sha256']
        for name, expected in build_result['source_sha256'].items():
            assert digest(app / name) == expected, name
        patches[patch_name] = patch(patch_name, APP_BASE, {name: app / name for name in app_paths})
        apps[revision] = dict(elf_sha256=build_result['elf_sha256'],
                              build_result=str(result), build_result_sha256=digest(result),
                              patch=patch_name, role='latest prototype' if revision == 'v3' else 'best tested variant')
    shutil.copyfile(T / 'abi.json', HERE / 'abi.json')
    shutil.copyfile(I / 'snapshot-model/abi.json', HERE / 'bram-abi.json')
    final_reports = {'REPORT.txt': 'REPORT.txt', 'summary.json': 'results.json',
                     'review/architecture-conclusion-v1.json': 'architecture-assessment.json'}
    for source, destination in final_reports.items():
        shutil.copyfile(T / source, HERE / destination)
    results = read(T / 'summary.json')
    assert results['status'] == 'CORRECT_NATIVE_PROTOTYPE_REJECTED_FOR_PERFORMANCE'
    recipe_sources = ['build_model.py', 'prepare_fixture_v3.py', 'replay.py', 'analyze.py',
                      'tests/run_controller.py', 'tests/run_wrapper.py', 'tests/run_native_cull.py',
                      'tests/check_lazy_bulk.py', 'tests/check_replays.py',
                      'tests/native_cull.c', 'tests/native_cull_host.cpp',
                      'tests/tb_triangle_preflight.v', 'tests/test_triangle_preflight.cpp',
                      'tests/test_preflight_wrapper.cpp', 'tests/geometry_raw_load_stub.v',
                      'tests/lazy_bulk_test_tail.c', 'app-build-v3/build_app.py', 'app-build-v2/build_app.py']
    recipes = {}
    for name in recipe_sources:
        source = T / name
        destination = HERE / 'recipes' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        recipes[name] = dict(source=str(source), sha256=digest(source))
    write(HERE / 'recipes.json', dict(
        scope='Archived exact recipes; use original experiment layout and bound dependencies. No ROM/assets/binaries included.',
        evidence_root=str(T), recipes=recipes,
        build_app_commands={v: read(T / ('app-build-' + v) / 'build-result.json')['command'] for v in apps},
        model_build_command=prepared['command'],
        model_build_cwd=str(model / 'frozen/src/fpga/test'),
        note='Recipes bind external qualified sources, toolchains, SDK and game data. They are not claimed standalone after copying only this directory.'))
    evidence = [QUALIFIED / n for n in ['inputs.json', 'timing-qualification.json', 'build/ap_core.qsf', 'build/ap_core.qpf']]
    evidence += [model / 'prepared.json', model / 'build.json', T / 'abi.json', T / 'rtl-build-notes.json']
    evidence += [T / ('app-build-' + v) / 'build-result.json' for v in apps]
    evidence += [Path(read(T / ('app-build-' + v) / 'build-result.json')['elf']) for v in apps]
    test_manifests = [T / n for n in ['tests/controller-v1/manifest.json', 'tests/wrapper-v3/manifest.json',
                                      'tests/cpu-arithmetic-v1/manifest.json']]
    evidence += test_manifests
    evidence += [T / n for n in ['tests/cpu-arithmetic-v1/result.json', 'tests/lazy-bulk-v3/report.json',
                                 'tests/lazy-bulk-v2/report.json', 'review/final-elf-v3-cull-dag.json',
                                 'review/final-elf-v2-cull-dag.json']]
    evidence += [T / name for name in recipe_sources]
    evidence += [T / name for name in final_reports]
    report_bindings = dict(results['evidence_sha256'])
    report_bindings.update(read(T / 'review/architecture-conclusion-v1.json')['inputs_sha256'])
    for name, expected in report_bindings.items():
        assert digest(Path(name)) == expected, name
        evidence.append(Path(name))
    test_summary = {}
    for name in ['tests/controller-v1/manifest.json', 'tests/wrapper-v3/manifest.json',
                 'tests/cpu-arithmetic-v1/result.json', 'tests/lazy-bulk-v3/report.json',
                 'tests/lazy-bulk-v2/report.json']:
        item = read(T / name)
        test_summary[name] = dict(sha256=digest(T / name), result=item.get('result', item))
    write(HERE / 'test-evidence.json', dict(scope='Native simulation and source-level tests only; no FPGA qualification.',
                                           evidence_root=str(T), tests=test_summary))
    post_sources = dict(baseline['sources'])
    for relative, source in postimages.items():
        if relative.startswith('frozen/'):
            post_sources[relative[len('frozen/'):]] = digest(source)
    assert len(post_sources) == 362
    manifest = dict(status=results['status'],
                    scope='os30 source-only TFP1+BIM2 prototype. No production mutation, FPGA build, timing qualification or deployment.',
                    patches=patches, apps=apps, physical_baseline=str(QUALIFIED), native_model=str(model),
                    qualified_baseline_sources_sha256=baseline['sources'],
                    physical_post_sources_sha256=post_sources,
                    qsf_changes=['Relocate qualified source paths to ../frozen', 'Add only BIM2 and TFP1 feature macros',
                                 'Register five new os30 RTL modules'],
                    physical_settings_preserved=dict(configured_MHz=90, seed=3, placement_effort=3.0,
                                                     clocks_constraints_pins_firmware_unchanged=True),
                    test_manifests=[str(p) for p in test_manifests],
                    external_evidence_sha256={str(p): digest(p) for p in evidence}, files=inventory())
    write(HERE / 'manifest.json', manifest)
    run = subprocess.run([sys.executable, str(HERE / 'verify.py'), '--patches', '--evidence'],
                         capture_output=True, text=True)
    report = dict(passed=run.returncode == 0, output=run.stdout, stderr=run.stderr,
                  package=str(HERE), manifest_sha256=digest(HERE / 'manifest.json'),
                  core_patch_files=len(patches['core.patch']['files']),
                  sm64_patch_files={v: len(patches[apps[v]['patch']]['files']) for v in apps},
                  source_files=362, no_assets_or_binaries_packaged=True,
                  quartus_launched=False, deployed=False)
    write(T / 'package-build.json', report)
    print(run.stdout, end='')
    if run.returncode:
        print(run.stderr, file=sys.stderr)
        raise SystemExit(run.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-manifest-files', action='store_true',
                        help='Rebind package files after adding final reports; preserve source/evidence bindings.')
    args = parser.parse_args()
    if args.refresh_manifest_files:
        manifest = read(HERE / 'manifest.json')
        manifest['files'] = inventory()
        write(HERE / 'manifest.json', manifest)
    else:
        build()


if __name__ == '__main__':
    main()
