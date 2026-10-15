#!/usr/bin/env python3
"""Independent source-only rejection and qualified-release compatibility audit."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
EXP = HERE.parent
REPO = EXP.parents[2]
PORTABLE = REPO / 'tools/experiments/quake2_tiny_audio_20261013'
SWEEP = EXP / 'sweep-final-ring-audio'
sys.path.insert(0, str(PORTABLE))
import package
import audit_sweep
import timing_results


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def main():
    manifest = package.load(SWEEP / 'manifest.json')
    assert manifest['frozen_sha256'] == 'f63613efe348448c45af303972d6804a41d15724ac3a716f17eff4b40e6eb2d3'
    assert package.inventory(SWEEP / 'frozen') == manifest['source_sha256']
    assert package.inventory(SWEEP / 'recipes') == manifest['recipe_sha256']
    assert package.sha(PORTABLE / 'timing_results.py') == manifest['recipe_sha256']['results.py']
    qsf = (SWEEP / 'frozen/src/fpga/targets/pocket/ap_core.qsf').read_text()
    settings = package.optimization_settings(qsf)
    assert settings['global_setting'] == manifest['synthesis_optimization_technique']
    assert settings['physical_synthesis'] == manifest['physical_synthesis']
    assert settings['cpu_combo_scope'] == manifest['cpu_combo_scope']
    app = package.verify_component('app', PORTABLE)
    prior_root = REPO / 'tools/experiments/quake2_gpu_draws_20261013/app'
    prior = package.load(prior_root / 'manifest.json')
    source_art = [n for n in app['source'] if n.endswith(('.png', '.pcx', '.md2'))]
    # Three small icon/crosshair inputs are part of the unchanged source bundle,
    # not newly bundled commercial PAK content. Preserve their explicit pins.
    assert source_art == ['src/quake2/stuff/icon/Quake2.png',
                          'src/quake2/stuff/models/crosshair/skin.pcx',
                          'src/quake2/stuff/models/crosshair/tris.md2']
    for name in source_art:
        assert app['source'][name]['sha256'] == prior['baseline']['sha256'][name]
    assert not [n for n in app['source'] if n.endswith(('.pak', '.wad', '.bsp', '.wal', '.wav', '.ogg'))]
    artifacts = [n for n in package.inventory(PORTABLE) if n.endswith(('.elf', '.rbf', '.rbf_r', '.sof'))]
    assert artifacts == ['app/app.elf']
    plan = package.load(EXP / 'package-evidence-plan.json')
    for item in plan['files']:
        assert package.sha(EXP / item['source']) == item['sha256'], item['source']
        assert not item['source'].endswith(('.elf', '.rbf', '.rbf_r', '.sof', '.pak', '.wad'))
    # No live sweep artifacts are modified or interpreted before completion.
    sys.path.insert(0, str(EXP / 'sweep-tooling'))
    fixture = module('sweep_fixture', EXP / 'sweep-tooling/test_sweep.py')
    checks = []
    with tempfile.TemporaryDirectory(prefix='tiny-audio-independent-audit-') as temp:
        root = Path(temp)
        shutil.copyfile(SWEEP / 'manifest.json', root / 'manifest.json')
        for name in ('source.tar.gz', 'baseline-manifest.json'):
            shutil.copyfile(SWEEP / name, root / name)
        shutil.copytree(SWEEP / 'recipes', root / 'recipes')
        rows = []
        raw = b'fixture-only-not-a-hardware-bitstream'
        reverse = bytes(int(f'{n:08b}'[::-1], 2) for n in range(256))
        artifact = root / 'selected.rbf_r'
        artifact.write_bytes(raw.translate(reverse))
        for seed in range(1, 31):
            seed_root = root / 'seeds' / f'seed_{seed:02}'
            attempt = seed_root / 'attempt_01'
            attempt.mkdir(parents=True)
            project = root / 'scratch'
            project.mkdir(exist_ok=True)
            fixture.fit(project)
            fixture.timing(attempt / 'timing', fmax=101 if seed == 5 else 95,
                           setup=.1 if seed == 5 else -.526,
                           hold=-.1 if seed == 1 else .1)
            report_pins = {}
            with tarfile.open(attempt / 'quartus-reports.tar.gz', 'w:gz') as archive:
                for path in sorted((project / 'output_files').iterdir()):
                    name = 'src/fpga/targets/pocket/output_files/' + path.name
                    archive.add(path, arcname=name)
                    report_pins[name] = package.sha(path)
            measured = timing_results.parse(project, attempt / 'timing')
            shutil.rmtree(project)
            (attempt / 'ap_core.rbf').write_bytes(raw)
            (attempt / 'os30.rbf_r').write_bytes(raw.translate(reverse))
            (attempt / 'ap_core.sof').write_bytes(b'fixture-only-sof')
            row = dict(measured, seed=seed, frozen_sha256=manifest['frozen_sha256'],
                       state='complete', valid_reports=True,
                       bitstream_sha256=package.sha(artifact), raw_rbf_sha256=hashlib.sha256(raw).hexdigest(),
                       quartus_report_sha256=report_pins)
            if seed == 7:
                row.update(state='failed', valid_reports=False, eligible_above_90_mhz_estimate=False)
            row['evidence_sha256'] = package.inventory(attempt)
            save(attempt / 'receipt.json', row)
            save(seed_root / 'result.json', row)
            save(seed_root / 'latest.json', dict(attempt='attempt_01'))
            rows.append(row)
        save(root / 'results.json', rows)
        selection = timing_results.select(rows)
        save(root / 'selection.json', selection)
        assert selection['selected_seed'] == 5
        assert audit_sweep.audit(root, 5, artifact) == selection
        checks.append('all30-current-format-reparse-and-selection-with-failed-seed-and-bad-hold')
        # Source-only must not be used to hide an eligible selected artifact.
        save(root / 'status.json', dict(state='complete', seeds_completed=30))
        try:
            audit_sweep.audit(root, None, source_only=True)
        except ValueError as error:
            assert 'null selection' in str(error)
        else:
            raise AssertionError('Source-only accepted eligible synthetic seed')
        checks.append('source-only-audit-rejects-eligible-computed-seed')
        assembler = module('qualified_export_review', EXP / 'assemble_package.py')
        dest = root / 'qualified-bundle'
        shutil.copytree(PORTABLE, dest)
        assembler.OUT = dest
        shutil.copytree(SWEEP / 'frozen', root / 'frozen')
        for name in ('map-receipt.json', 'run-configuration.json'):
            save(root / name, dict(synthetic_test=True))
        (root / 'map.log').write_text('SYNTHETIC ONLY; no Quartus executed\n')
        try:
            assembler.final(root, EXP / 'package-evidence-plan.json', True, source_only=True)
        except ValueError as error:
            assert 'no eligible selected seed' in str(error)
        else:
            raise AssertionError('Source-only assembler accepted eligible synthetic seed')
        checks.append('source-only-assembler-rejects-eligible-computed-seed')
        assembler.final(root, EXP / 'package-evidence-plan.json', True)
        assembler.final(root, EXP / 'package-evidence-plan.json')
        release = package.verify(dest)
        assert release['state'] == 'assembled' and release['selected_seed'] == 5
        assert release['qualified'] is True and not (dest / 'core-source').exists()
        assert (dest / 'core/os30.rbf_r').read_bytes() == artifact.read_bytes()
        release_artifacts = [str(p.relative_to(dest)) for p in dest.rglob('*')
                             if p.suffix.lower() in ('.rbf', '.rbf_r', '.sof')]
        assert release_artifacts == ['core/os30.rbf_r'], release_artifacts
        checks.append('default-qualified-full-assembly-still-verifies-with-one-selected-artifact')

        omissions = {}
        for seed in range(1, 31):
            attempt = root / 'seeds' / f'seed_{seed:02}' / 'attempt_01'
            omissions[str(seed)] = {}
            for name in ('ap_core.rbf', 'os30.rbf_r', 'ap_core.sof'):
                omissions[str(seed)][name] = package.sha(attempt / name)
                (attempt / name).unlink()
        save(root / 'bundle-omissions.json', omissions)
        assert audit_sweep.audit(root, 5, artifact) == selection
        checks.append('portable-omitted-unselected-binaries-reparse-identically')
        def rejects(label, action):
            try:
                action()
            except (ValueError, FileNotFoundError):
                checks.append(label)
                return
            raise AssertionError('Unexpected acceptance: ' + label)
        selected = root / 'selection.json'
        save(selected, dict(selection, selected_seed=1))
        rejects('tampered-selection-rejected', lambda: audit_sweep.audit(root, 1, artifact))
        save(selected, selection)
        original = artifact.read_bytes()
        artifact.write_bytes(b'changed')
        rejects('selected-bitstream-tamper-rejected', lambda: audit_sweep.audit(root, 5, artifact))
        artifact.write_bytes(original)
        omissions['1']['ap_core.sof'] = '0' * 64
        save(root / 'bundle-omissions.json', omissions)
        rejects('omitted-artifact-digest-tamper-rejected', lambda: audit_sweep.audit(root, 5, artifact))
    result = dict(passed=True, frozen_sha256=manifest['frozen_sha256'],
                  source_files=len(manifest['source_sha256']), source_and_recipe_drift=False,
                  app_source_files=len(app['source']), unchanged_baseline_source_art=source_art,
                  proprietary_pak_or_map_assets=[], duplicate_release_binaries=[],
                  current_release_artifacts=artifacts, evidence_plan_files=len(plan['files']),
                  settings=settings, checks=checks, actual_sweep_modified=False,
                  limitation='Disposable synthetic eligible seed only. Real outcomes remain unqualified unless the unchanged timing/resource gate selects one; source-only is an explicitly separate null-selection export.',
                  file_sha256={str(p.relative_to(REPO)): package.sha(p) for p in [
                      Path(__file__), EXP / 'assemble_package.py', EXP / 'prepare_package_evidence.py',
                      EXP / 'package-evidence-plan.json', PORTABLE / 'package.py',
                      PORTABLE / 'audit_sweep.py', PORTABLE / 'timing_results.py']})
    save(HERE / 'source-only-review-receipt.json', result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
