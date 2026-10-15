#!/usr/bin/env python3
"""Assemble a NEW Quake2 source release and separate deployment; never install."""
import argparse
import json
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / 'release-package-templates'
sys.path.insert(0, str(TEMPLATES))
from release_checks import (COMPATIBILITY, INSTALLER, NORMAL, PROFILE, check_deployment,
                            inventory, read, require, safe_name, select, sha, verify_child)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def main():
    repo = HERE.parents[2]
    bundle = repo / 'tools/experiments/quake2_gpu_draws_20261013'
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core', type=Path, default=bundle / 'core')
    p.add_argument('--app', type=Path, default=bundle / 'app')
    p.add_argument('--physical', type=Path, default=HERE / 'physical-particles-quake2-v1/result.json')
    p.add_argument('--validation', type=Path, default=HERE / 'validation/final-q2-descriptor-validation.json')
    p.add_argument('--evidence-root', type=Path, default=HERE)
    p.add_argument('--normal', type=Path, default=HERE / 'app-candidate-v5/obj-final/app.elf')
    p.add_argument('--profile', type=Path, default=HERE / 'app-candidate-v5/obj-profile/app.elf')
    p.add_argument('--native5-deployment', type=Path,
                   default=HERE.parent / 'quake2-native-batching-20261012/deployment-native-v5')
    p.add_argument('--output', type=Path, default=bundle)
    p.add_argument('--deployment-output', type=Path, default=HERE / 'deployment-native-v6')
    p.add_argument('--release-date', default='2026-10-13')
    p.add_argument('--check-only', action='store_true')
    a = p.parse_args()
    core, app = a.core.resolve(), a.app.resolve()
    out, deploy = a.output.resolve(), a.deployment_output.resolve()
    evidence = a.evidence_root.resolve()
    require(out != deploy and out not in deploy.parents and deploy not in out.parents,
            'Source and deployment directories must be separate')
    require(not deploy.exists(), 'Deployment output already exists')
    if out.exists():
        require(set(x.name for x in out.iterdir()) <= {'app', 'core'}, 'Source release already exists or has unexpected files')
        require(all(not (out / n).exists() or (out / n).resolve() == v
                    for n, v in [('app', app), ('core', core)]), 'Refusing to overwrite a child package')
    verify_child(core)
    verify_child(app)
    cm, am = read(core / 'manifest.json'), read(app / 'manifest.json')
    physical, validation = read(a.physical), read(a.validation)
    clean = read(evidence / 'app-clean-rebuild.json')
    appearance = read(evidence / 'appearance-review.json')
    portable = read(evidence / 'core-portable-proof.json')
    require(sha(a.physical) == sha(core / cm['physical_result']), 'Physical receipt does not match packaged core')
    require(clean['manifest_sha256'] == sha(app / 'manifest.json') and
            clean['build_log_sha256'] == sha(evidence / 'staged-app-proof/build.log'), 'Clean app rebuild pins differ')
    require(portable['passed'] is True and portable['worker_regression_passed'] is True and
            portable['core_manifest_sha256'] == sha(core / 'manifest.json') and
            portable['bitstream_sha256'] == cm['artifact_sha256'], 'Portable core proof does not match selected core')
    require(sha(a.normal) == NORMAL and sha(a.profile) == PROFILE, 'Selected ELF bytes differ')
    require(sha(a.native5_deployment / 'install.py') == INSTALLER, 'Native5 installer changed')
    require(read(a.native5_deployment / 'manifest.json')['compatibility'] == COMPATIBILITY, 'Native5 compatibility differs')
    selection = select(cm, am, physical, validation, clean, appearance)
    # Resolve all evidence and reject absent/stale pins before creating either output.
    copies = {
        'validation.json': a.validation, 'physical.json': a.physical,
        'app-clean-rebuild.json': evidence / 'app-clean-rebuild.json',
        'app-clean-build.log': evidence / 'staged-app-proof/build.log',
        'core-portable-proof.json': evidence / 'core-portable-proof.json',
        'appearance-review.json': evidence / 'appearance-review.json',
        'appearance-panel.png': evidence / safe_name(appearance['reviewed_panel']),
        'appearance-measurements.json': evidence / 'validation/image-comparisons/particles-v5-main-v2/result.json',
        'appearance-baseline-vector.json': evidence / 'validation/baseline-vectors-main/frame_00148.json',
        'historical-validation-report.txt': evidence / 'validation/REPORT.txt',
        'quake2-feature-audit.txt': evidence / 'quake2-feature-audit.txt',
        'assembly-recipe.py': Path(__file__).resolve(),
    }
    require(sha(copies['appearance-panel.png']) == appearance['panel_sha256'], 'Appearance panel pin differs')
    worst = next(f for f in read(copies['appearance-measurements.json'])['frames'] if f['frame'] == 148)
    require(worst['color_changed'] == appearance['maximum_changed_pixels'] and worst['depth_changed'] == 0,
            'Appearance measurements disagree with reviewed worst frame')
    for name, expected in validation['selected_evidence_pins'].items():
        source = evidence / safe_name(name)
        require(sha(source) == expected, 'Stale selected evidence: ' + name)
        copies['selected/' + name] = source
    copy_pins = {name: sha(path) for name, path in copies.items()}
    for name in ('verify.py', 'release_checks.py'):
        sha(TEMPLATES / name)
    if a.check_only:
        print(json.dumps({'passed': True, 'selection': selection, 'evidence_files': len(copies),
                          'scope': 'Read-only readiness check; no release or deployment created.'}, indent=2))
        return
    out.mkdir(parents=True, exist_ok=True)
    for name, source in [('app', app), ('core', core)]:
        if not (out / name).exists():
            shutil.copytree(source, out / name)
    deploy.mkdir(parents=True)
    for name, source in [('quake2.elf', a.normal), ('quake2-gpuprofile.elf', a.profile),
                         ('os30.rbf_r', core / cm['artifact']), ('install.py', a.native5_deployment / 'install.py')]:
        shutil.copy2(source, deploy / name)
    metadata = read(a.native5_deployment / 'core.json')
    metadata['core']['metadata'].update(version='1.0.6-native6', date_release=a.release_date,
                                       description='Quake2 GPU particle experiment')
    write_json(deploy / 'core.json', metadata)
    deployment = {
        'date': a.release_date, 'status': 'READY_FOR_DEVICE_TRIAL', 'deployed': False,
        'physical_Pocket_tested': False,
        'configured_clock_MHz': 100, 'timing_qualified': physical['constrained_timing_passed'],
        'physical_FPS_improvement_established': False,
        'audio_disabled_on_experimental_core': True, 'resolution': '320x240 RGB565',
        'compatibility': COMPATIBILITY,
        'files': {n: {'sha256': sha(deploy / n), 'bytes': (deploy / n).stat().st_size}
                  for n in ('quake2.elf', 'quake2-gpuprofile.elf', 'os30.rbf_r', 'core.json')},
        'installer_sha256': INSTALLER, 'app_source_manifest_sha256': sha(app / 'manifest.json'),
        'core_source_manifest_sha256': sha(core / 'manifest.json'),
        'validation_receipt_sha256': sha(a.validation), 'physical_receipt_sha256': sha(a.physical),
        'selection': selection,
    }
    write_json(deploy / 'manifest.json', deployment)
    check_deployment(deploy, deployment)
    deploy.joinpath('README.md').write_text(
        '# Quake2 native6 experimental device trial\n\n'
        'Prepared only; not installed or measured on Pocket. Resolution is 320x240 RGB565. '
        'Audio remains disabled on this experimental core. This is not an SM64 replacement.\n\n'
        'Configured at 100 MHz, with negative setup slack: this is not a timing-qualified 100 MHz build. '
        'The selected slow-corner Fmax estimates exceed 90 MHz; all hold/recovery/removal/pulse checks pass.\n\n'
        'The unchanged native5 installer checks OS/loader compatibility, stages and hashes payloads, '
        'and backs up existing files. It does not replace OS, loader, assets, or saves. '
        'Use `python3 install.py /run/media/alberto/POCKETDEV --check` to inspect a mounted card. '
        'Install with `python3 install.py /run/media/alberto/POCKETDEV`; '
        '`--profile` selects the diagnostic ELF. '
        'Runtime receipts/backups belong here, outside the immutable source bundle.\n')
    for name, source in copies.items():
        destination = out / 'evidence' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        require(sha(destination) == copy_pins[name], 'Evidence changed during assembly: ' + name)
    shutil.copy2(deploy / 'manifest.json', out / 'evidence/deployment-manifest.json')
    selection.update(deployment_manifest_sha256=sha(deploy / 'manifest.json'),
                     deployment_directory=deploy.name,
                     app_manifest_sha256=sha(app / 'manifest.json'),
                     core_manifest_sha256=sha(core / 'manifest.json'),
                     validation_receipt_sha256=sha(a.validation),
                     physical_receipt_sha256=sha(a.physical))
    write_json(out / 'selection.json', selection)
    for name in ('verify.py', 'release_checks.py'):
        shutil.copy2(TEMPLATES / name, out / name)
    lo, hi = selection['modeled_sample_gain_range_percent']
    out.joinpath('README.md').write_text(
        '# Quake2 GPU particle experiment — native6\n\n'
        'Ready for a device trial; not deployed or measured on Pocket. The app submits compact '
        'particle records for GPU projection while the CPU continues. Existing world/MD2 paths, '
        '320x240 resolution, particle count/order, alpha and depth-write states are preserved. '
        'The canonical fixed-point particle reference and actual GPU outputs agree exactly.\n\n'
        f'Eight paced GPU samples model {lo:.1f}–{hi:.1f}% whole-frame throughput gains; '
        f'the three busy samples together improve {selection["modeled_busy_aggregate_gain_percent"]:.2f}%. '
        'These are simulation results, not measured Pocket FPS, and not every frame gains 20%. '
        'The scalar CPU model does not fully couple CPU caches, video and audio traffic.\n\n'
        'Appearance differs from the old floating-point path at localized particle texture samples: '
        'at most 252/76800 pixels (0.328125%) in measured frames and at most 1/16 pixel in projected vertices. '
        'The reviewed worst-frame panel and exact baseline/reference dump pins are in `evidence/`. '
        'Depth memory agrees; particles do not write depth, so this is not a claim of identical depth-test inputs.\n\n'
        f'Seed 2 fits at {physical["ALMs"]["used"]}/{physical["ALMs"]["available"]} ALMs, '
        f'{physical["M10Ks"]["used"]} M10Ks and {physical["DSPs"]["used"]} DSPs. '
        f'Slow-corner Fmax estimates are {physical["main_clock_restricted_Fmax_MHz"]["slow_85"]:.2f} / '
        f'{physical["main_clock_restricted_Fmax_MHz"]["slow_0"]:.2f} MHz (85 / 0 °C). '
        f'Configured clock remains 100 MHz; worst setup slack is {physical["worst_setup_ns"]:.3f} ns. '
        'This does not close timing at 100 MHz. Positive hold/recovery/removal/pulse checks and '
        'both slow estimates above 90 MHz satisfy the selected experimental trial gate. '
        'Historical validation receipts and the historical report describe preceding equivalent RTL '
        'or pending physical work; `selection.json` and `evidence/validation.json` bind the selected '
        'worker/GPU/QSF pins to the fitted artifact and records the completed trial checks.\n\n'
        'This core is Quake2-only: the unused additive color combiner was removed to fit. '
        'RGB modulation, lightmapped world/alias rendering, translucency and particle blending remain. '
        'Audio remains intentionally disabled. Do not use this image as an SM64 replacement.\n\n'
        'Run `python3 -B verify.py` to verify this portable source bundle. Add '
        '`--deployment /path/to/deployment-native-v6` to verify the separate deployment payloads. '
        '`app/package.py` stages complete app sources; `core/package.py` stages exact fitted RTL. '
        'Their build helpers and instructions are inside each package. The app clean rebuild matched '
        'the selected normal ELF, and packaged worker tests were freshly compiled and replayed exactly.\n\n'
        'This bundle contains complete app/core source archives plus selected validation summaries, '
        'logs, vector/dump hashes, feature audits, physical reports and the appearance review. '
        'It does not contain the full raw capture/replay vector archives needed to rerun every frame. '
        'Keep this source bundle immutable; installation backups and receipts are written only to '
        'the separately maintained `deployment-native-v6` directory. No installer was run during assembly.\n')
    write_json(out / 'manifest.json', {'format_version': 1, 'status': 'READY_FOR_DEVICE_TRIAL',
                                      'deployed': False, 'files': inventory(out, exclude_manifest=True)})
    import subprocess
    subprocess.run([sys.executable, '-B', str(out / 'verify.py'), '--deployment', str(deploy)], check=True)
    print(json.dumps({'source_bundle': str(out), 'deployment': str(deploy), 'deployed': False}, indent=2))


if __name__ == '__main__':
    main()
