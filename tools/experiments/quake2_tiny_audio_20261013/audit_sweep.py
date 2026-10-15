#!/usr/bin/env python3
"""Reparse all30 raw timing results and repeat the recorded seed selection."""
import json
from pathlib import Path
import re
import sys
import tempfile
sys.dont_write_bytecode = True
import package


def audit(root, selected_seed, selected_artifact=None, *, source_only=False):
    import timing_results
    manifest = package.load(root / 'manifest.json')
    if manifest['seeds'] != list(range(1, 31)) or manifest['variant'] != 'os30' or manifest['configured_mhz'] != 100:
        raise ValueError('Unexpected sweep scope')
    if package.sha(root/'baseline-manifest.json')!=manifest['baseline_manifest_sha256']:
        raise ValueError('Sweep baseline manifest changed')
    if package.inventory(root/'recipes')!=manifest['recipe_sha256']:
        raise ValueError('Sweep recipes changed')
    if package.sha(Path(timing_results.__file__))!=manifest['recipe_sha256']['results.py']:
        raise ValueError('Reparser differs from executed sweep parser')
    if any(manifest['source_sha256'].get(n)!=d for n,d in manifest['protected_sha256'].items()):
        raise ValueError('Protected source pin mismatch')
    if package.sha(root / 'source.tar.gz') != manifest['source_archive_sha256']:
        raise ValueError('Sweep source archive changed')
    for name,data,mode in package.archive_members(root / 'source.tar.gz', manifest['source_sha256']):
        if name=='src/fpga/targets/pocket/ap_core.qsf':
            settings=package.optimization_settings(data.decode())
            if settings['global_setting']!=manifest['synthesis_optimization_technique']:
                raise ValueError('Sweep effective synthesis setting differs from QSF')
            if settings['physical_synthesis']!=manifest['physical_synthesis']:
                raise ValueError('Sweep physical synthesis policy differs from QSF')
            if settings['cpu_combo_scope']!=manifest['cpu_combo_scope']:
                raise ValueError('Sweep CPU physical synthesis scope differs from QSF')
    omissions = package.load(root / 'bundle-omissions.json') if (root / 'bundle-omissions.json').exists() else {str(n):{} for n in range(1,31)}
    rows = []
    for seed in range(1, 31):
        directory = root / 'seeds' / f'seed_{seed:02}'
        attempt_name = package.load(directory / 'latest.json')['attempt']
        if not re.fullmatch(r'attempt_\d+', attempt_name):
            raise ValueError('Invalid attempt name')
        attempt = directory / attempt_name
        row = package.load(directory / 'result.json')
        if row != package.load(attempt / 'receipt.json') or row['seed'] != seed or row['frozen_sha256'] != manifest['frozen_sha256']:
            raise ValueError('Seed receipt/source differs')
        actual = package.inventory(attempt); actual.pop('receipt.json')
        missing = omissions[str(seed)]
        if not set(missing) <= {'os30.rbf_r', 'ap_core.rbf', 'ap_core.sof'}:
            raise ValueError('Nonartifact omission')
        pins = dict(row['evidence_sha256'])
        for name, digest in missing.items():
            if pins.pop(name, None) != digest:
                raise ValueError('Omitted artifact hash differs')
        if actual != pins:
            raise ValueError('Seed evidence inventory/hash differs')
        if row.get('valid_reports'):
            with tempfile.TemporaryDirectory(prefix='tiny-audio-timing-') as temporary:
                project = Path(temporary)
                for name, data, mode in package.archive_members(attempt / 'quartus-reports.tar.gz', row['quartus_report_sha256']):
                    if name in ('src/fpga/targets/pocket/output_files/ap_core.fit.rpt', 'src/fpga/targets/pocket/output_files/ap_core.fit.summary'):
                        p = project / 'output_files' / Path(name).name
                        p.parent.mkdir(exist_ok=True); p.write_bytes(data)
                measured = timing_results.parse(project, attempt / 'timing')
            if any(row.get(k) != v for k, v in measured.items()):
                raise ValueError('Stored result differs from raw timing reparse')
        rows.append(row)
    selection = timing_results.select(rows)
    if rows != package.load(root / 'results.json') or selection != package.load(root / 'selection.json'):
        raise ValueError('Collected sweep results/selection differ')
    if source_only:
        status=package.load(root/'status.json')
        if status.get('state')!='complete' or status.get('seeds_completed')!=30 or not selection['all_seeds_attempted']:
            raise ValueError('Source-only export requires all 30 completed attempts')
        if selected_seed is not None or selection['selected_seed'] is not None or selected_artifact is not None:
            raise ValueError('Source-only export requires null selection and no release artifact')
        return selection
    if selected_seed != selection['selected_seed'] or selected_seed is None:
        raise ValueError('No matching eligible selected seed')
    chosen = rows[selected_seed - 1]
    artifact = selected_artifact or root.parent / 'core/os30.rbf_r'
    if package.sha(artifact) != chosen['bitstream_sha256']:
        raise ValueError('Selected artifact hash differs')
    reverse = bytes(int(f'{n:08b}'[::-1], 2) for n in range(256))
    import hashlib
    if hashlib.sha256(artifact.read_bytes().translate(reverse)).hexdigest() != chosen['raw_rbf_sha256']:
        raise ValueError('Selected bit reversal does not match original RBF')
    return selection
