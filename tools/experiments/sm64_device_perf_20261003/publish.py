#!/usr/bin/env python3
"""Publish only the validated diagnostic source and instructions, with rollback."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-device-perf-20261003'
STAGE = OUT / 'SM64'
DEST = ROOT.parent / 'SM64'
BACKUP = DEST / '.obj/perf-snapshot-backup-20261003'
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
load = lambda p: json.loads(p.read_text())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = load(OUT / 'results.json')
    assert report['paired_framebuffer_depth_comparisons'] == 18
    assert report['disabled_model_cycles_match_all_frames']
    assert digest(STAGE / '.obj/sm64-perfdiag/app.elf') == report['app_sha256']
    expected = load(OUT / 'before.json')
    for rel in ('src/sm64/pocket/perf_pocket.h', 'src/sm64/pocket/perf_pocket.c', 'PERFORMANCE-TEST.txt'):
        expected[rel] = None
    for rel, before in expected.items():
        assert digest(DEST / rel) == before, ('Destination changed since review', rel)
    after = {**report['source_sha256'], 'PERFORMANCE-TEST.txt': report['notes_sha256']}
    for rel, value in {**after, **report['unchanged_sha256']}.items():
        assert digest(STAGE / rel) == value, ('Staged file changed after validation', rel)
    assert digest(DEST / '.obj/sm64/app.elf') == report['baseline_app_sha256']
    assert digest(DEST / 'build/pocket/sm64/Assets/sm64/common/sm64.elf') == report['baseline_app_sha256']
    rows = [dict(path=rel, before=expected[rel], after=value) for rel, value in after.items()]
    publication = dict(destination=str(DEST), backup=str(BACKUP), files=rows)
    print(json.dumps(publication, indent=2))
    if not args.apply:
        return
    BACKUP.mkdir(parents=True, exist_ok=False)
    for row in rows:
        if row['before'] is not None:
            dst = BACKUP / row['path']
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DEST / row['path'], dst)
            assert digest(dst) == row['before']
    shutil.copy2(DEST / '.obj/sm64/app.elf', BACKUP / 'app.elf')
    assert digest(BACKUP / 'app.elf') == report['baseline_app_sha256']
    (BACKUP / 'files.json').write_text(json.dumps(rows, indent=2) + '\n')
    (BACKUP / 'restore.py').write_text('''#!/usr/bin/env python3
import hashlib,json,shutil
from pathlib import Path
b=Path(__file__).resolve().parent;r=b.parent.parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
rows=json.loads((b/'files.json').read_text())
for x in rows:
    assert h(r/x['path'])==x['after'],('File changed; review before restoring',x['path'])
    if x['before'] is not None:
        assert h(b/x['path'])==x['before'],('Backup changed',x['path'])
for x in rows:
    p=r/x['path']
    if x['before'] is None:p.unlink()
    else:shutil.copyfile(b/x['path'],p)
print('Previous source restored. Run make -C src/sm64 before make copy.')
''')
    for row in rows:
        dst = DEST / row['path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STAGE / row['path'], dst)
        assert digest(dst) == row['after']
    (OUT / 'published.json').write_text(json.dumps(publication, indent=2) + '\n')
    print('Diagnostic source updated. Rebuild the app/package; no SD-card copy performed.')

if __name__ == '__main__':
    main()

