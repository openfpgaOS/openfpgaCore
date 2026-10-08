#!/usr/bin/env python3
"""Guarded app-only publication; leaves SDK, OS and FPGA runtime intact."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-render-perf-20261004'
STAGE = OUT / 'SM64'
DEST = ROOT.parent / 'SM64'
BACKUP = DEST / '.obj/perf-render-backup-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
load = lambda p: json.loads(p.read_text())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    assert not (args.apply and args.verify)
    report = load(OUT / 'results.json')
    assert report['paired_framebuffer_depth_comparisons'] == 10
    assert report['disabled_loadable_check']['only_assertion_line_numbers_changed']
    assert report['frozen_snapshot_records'] == 77
    before = load(OUT / 'before.json')
    after = dict(report['source_sha256'])
    for rel in ('.obj/sm64-perfdiag/app.elf', 'build/pocket/sm64/Assets/sm64/common/sm64.elf'):
        after[rel] = report['app_sha256']
    for rel, value in report['source_sha256'].items():
        assert sha(STAGE / rel) == value, ('Staged source changed', rel)
    app = STAGE / '.obj/sm64-perfdiag/app.elf'
    assert sha(app) == report['app_sha256']
    for rel, value in before.items():
        expected = after.get(rel, value) if args.verify else value
        assert sha(DEST / rel) == expected, ('Destination changed', rel)
    if args.verify:
        publication = load(OUT / 'published.json')
        for row in publication['files']:
            assert sha(BACKUP / row['path']) == row['before']
        print('PASS: published source/app/package, rollback backups and unchanged SDK/runtime hashes.')
        return
    rows = [dict(path=rel, before=before[rel], after=value) for rel, value in after.items()]
    print(json.dumps(dict(destination=str(DEST), backup=str(BACKUP), files=[r['path'] for r in rows]), indent=2))
    if not args.apply:
        return
    BACKUP.mkdir(parents=True, exist_ok=False)
    for row in rows:
        dst = BACKUP / row['path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DEST / row['path'], dst)
        assert sha(dst) == row['before']
    (BACKUP / 'files.json').write_text(json.dumps(rows, indent=2) + '\n')
    (BACKUP / 'restore.py').write_text('''#!/usr/bin/env python3
import hashlib,json,shutil
from pathlib import Path
b=Path(__file__).resolve().parent;r=b.parent.parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows=json.loads((b/'files.json').read_text())
for x in rows:
    assert h(r/x['path'])==x['after'],('Destination changed',x['path'])
    assert h(b/x['path'])==x['before'],('Backup changed',x['path'])
for x in rows:shutil.copyfile(b/x['path'],r/x['path'])
print('P1 source, app and local package restored. Pocket SD is unchanged.')
''')
    for row in rows:
        src = app if row['path'].endswith('.elf') else STAGE / row['path']
        shutil.copyfile(src, DEST / row['path'])
        assert sha(DEST / row['path']) == row['after']
    (OUT / 'published.json').write_text(json.dumps(dict(destination=str(DEST), backup=str(BACKUP), files=rows), indent=2) + '\n')
    for rel, value in before.items():
        assert sha(DEST / rel) == after.get(rel, value), rel
    print('P2 source and validated app published; SDK, OS and FPGA runtime unchanged.')

if __name__ == '__main__':
    main()
