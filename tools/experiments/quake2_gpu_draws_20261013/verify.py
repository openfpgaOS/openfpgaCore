#!/usr/bin/env python3
"""Verify source bundle and, optionally, a separately maintained deployment copy."""
import argparse
from pathlib import Path
import sys
sys.dont_write_bytecode = True
from release_checks import (check_deployment, inventory, read, require, select, sha, verify_child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = read(root / 'manifest.json')
    require(inventory(root, exclude_manifest=True) == manifest['files'], 'Bundle inventory or hashes differ')
    for child in ('app', 'core'):
        verify_child(root / child)
    app, core = read(root / 'app/manifest.json'), read(root / 'core/manifest.json')
    clean = read(root / 'evidence/app-clean-rebuild.json')
    require(clean['manifest_sha256'] == sha(root / 'app/manifest.json'), 'Clean app source pin differs')
    require(clean['build_log_sha256'] == sha(root / 'evidence/app-clean-build.log'), 'Clean build log differs')
    portable = read(root / 'evidence/core-portable-proof.json')
    require(portable['passed'] is True and portable['worker_regression_passed'] is True and
            portable['core_manifest_sha256'] == sha(root / 'core/manifest.json') and
            portable['bitstream_sha256'] == core['artifact_sha256'], 'Portable core proof differs')
    require(sha(root / 'evidence/physical.json') == sha(root / 'core' / core['physical_result']),
            'Selected physical receipt differs from packaged core')
    validation = read(root / 'evidence/validation.json')
    for name, expected in validation['selected_evidence_pins'].items():
        require(sha(root / 'evidence/selected' / name) == expected, 'Selected validation evidence differs')
    appearance = read(root / 'evidence/appearance-review.json')
    require(sha(root / 'evidence/appearance-panel.png') == appearance['panel_sha256'], 'Appearance panel differs')
    selected = select(core, app, read(root / 'evidence/physical.json'), validation, clean, appearance)
    selection = read(root / 'selection.json')
    require(all(selection.get(k) == v for k, v in selected.items()), 'Selection no longer agrees with evidence')
    require(selection['app_manifest_sha256'] == sha(root / 'app/manifest.json') and
            selection['core_manifest_sha256'] == sha(root / 'core/manifest.json') and
            selection['validation_receipt_sha256'] == sha(root / 'evidence/validation.json') and
            selection['physical_receipt_sha256'] == sha(root / 'evidence/physical.json'),
            'Selection input pins differ')
    deployment = read(root / 'evidence/deployment-manifest.json')
    require(sha(root / 'evidence/deployment-manifest.json') == selection['deployment_manifest_sha256'],
            'Deployment manifest differs')
    require(deployment['files']['quake2.elf']['sha256'] == selection['normal_elf_sha256'] and
            deployment['files']['quake2-gpuprofile.elf']['sha256'] == selection['profile_elf_sha256'] and
            deployment['files']['os30.rbf_r']['sha256'] == selection['bitstream_sha256'],
            'Deployment payload selection differs')
    if args.deployment:
        require(sha(args.deployment / 'manifest.json') == selection['deployment_manifest_sha256'],
                'External deployment manifest differs')
        check_deployment(args.deployment, deployment)
    print('PASS: source bundle, selected evidence and trial gates' +
          ('; external deployment payloads' if args.deployment else '') +
          '. Configured 100 MHz setup timing remains unqualified; no device FPS claim.')


if __name__ == '__main__':
    main()
