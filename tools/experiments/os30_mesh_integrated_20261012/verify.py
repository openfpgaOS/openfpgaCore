#!/usr/bin/env python3
"""Verify inventory and exact local source-patch round trips, without mutation."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile

P = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
a = argparse.ArgumentParser()
a.add_argument('--patches', action='store_true')
a.add_argument('--evidence', action='store_true')
args = a.parse_args()
m = json.loads((P / 'manifest.json').read_text())
actual = {str(p.relative_to(P)): sha(p) for p in P.rglob('*') if p.is_file()
          and str(p.relative_to(P)) not in m['excluded_inventory'] and '__pycache__' not in p.parts}
assert actual == m['files'], 'Inventory mismatch'
for name in actual:
    assert Path(name).suffix not in {'.elf', '.bin', '.rbf', '.rbf_r', '.mif', '.z64', '.n64', '.v64', '.o', '.a', '.qdb'}, name
if args.patches:
    for name, item in m['patches'].items():
        with tempfile.TemporaryDirectory(prefix='mesh-integrated-verify-') as temp:
            out = Path(temp)
            for relative, hashes in item['files'].items():
                r = Path(relative)
                assert not r.is_absolute() and '..' not in r.parts
                src, dst = Path(item['base']) / r, out / r
                if hashes['before_sha256'] is not None:
                    assert sha(src) == hashes['before_sha256'], src
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dst)
                else:
                    assert not src.exists(), src
            for direction, key in [('--forward', 'after_sha256'), ('--reverse', 'before_sha256')]:
                run = subprocess.run(['patch', '-p1', '--batch', direction, '--fuzz=0', '-i', str(P / name)],
                                     cwd=out, text=True, capture_output=True)
                log = run.stdout + run.stderr
                assert run.returncode == 0 and 'offset' not in log.lower() and 'fuzz' not in log.lower(), log
                expected_files = set()
                for relative, hashes in item['files'].items():
                    expected = hashes[key]
                    if expected is None:
                        assert not (out / relative).exists(), relative
                    else:
                        assert sha(out / relative) == expected, relative
                        expected_files.add(relative)
                assert {str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()} == expected_files
if args.evidence:
    for name, expected in m['external_evidence_sha256'].items():
        assert sha(Path(name)) == expected, name
    for name, expected in m['native_source_sha256'].items():
        assert sha(Path(m['native_revision']) / name) == expected, name
    for name, item in m['copied_inputs'].items():
        assert sha(Path(item['source'])) == item['sha256'] == sha(P / name), name
    for item in m['patches'].values():
        for hashes in item['files'].values():
            assert sha(Path(hashes['postimage_source'])) == hashes['after_sha256']
print('PASS source-only inventory' + (f'; {len(m["patches"])} exact forward/reverse patch round trips' if args.patches else '') +
      ('; frozen input/evidence hashes with explicit historical relocations' if args.evidence else ''))
