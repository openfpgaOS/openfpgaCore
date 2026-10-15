#!/usr/bin/env python3
"""Read-only verification of the two predecessor source/evidence bundles."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = next(p for p in HERE.parents if (p / 'tools/experiments').is_dir())
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

def inventory(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}

def verify(label):
    bundle = ROOT / 'tools/experiments' / ('os30_' + label + '_20261012')
    before = inventory(bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text())
    command = [sys.executable, '-B', str(bundle / 'verify.py'), '--patches', '--evidence']
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True)
    after = inventory(bundle)
    stdout = HERE / (label + '.stdout.txt')
    stderr = HERE / (label + '.stderr.txt')
    stdout.write_text(result.stdout)
    stderr.write_text(result.stderr)
    inventory_path = HERE / (label + '.inventory.json')
    inventory_path.write_text(json.dumps(before, indent=2) + '\n')
    return dict(label=label, bundle=str(bundle), command=command,
                returncode=result.returncode, status='PASS' if result.returncode == 0 and before == after else 'FAIL',
                manifest_sha256=before['manifest.json'], verifier_sha256=before['verify.py'],
                bundle_unchanged=(before == after), files=len(before),
                patch_round_trips=len(manifest['patches']), native_inputs=len(manifest['native_source_sha256']),
                stdout=dict(path=str(stdout), sha256=sha(stdout)),
                stderr=dict(path=str(stderr), sha256=sha(stderr)),
                inventory=dict(path=str(inventory_path), sha256=sha(inventory_path)))

assert not (HERE / 'result.json').exists(), 'Preserve completed verification receipts; choose a fresh directory.'
with ThreadPoolExecutor(max_workers=2) as pool:
    records = list(pool.map(verify, ['priority_mesh', 'async_mesh']))
report = dict(status='PASS' if all(r['status'] == 'PASS' for r in records) else 'FAIL',
              scope='Read-only source inventory, exact forward/reverse patches and frozen evidence verification; no builds.',
              runner_sha256=sha(__file__), bundles=records)
receipt = HERE / 'result.json'
receipt.write_text(json.dumps(report, indent=2) + '\n')
(HERE / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n'
                                         for p in sorted(HERE.iterdir()) if p.is_file() and p.name != 'SHA256SUMS'))
print(json.dumps(dict(status=report['status'], receipt=str(receipt), sha256=sha(receipt),
                     results=[dict(label=r['label'], status=r['status'], unchanged=r['bundle_unchanged'],
                                   patch_round_trips=r['patch_round_trips']) for r in records]), indent=2))
sys.exit(0 if report['status'] == 'PASS' else 1)
