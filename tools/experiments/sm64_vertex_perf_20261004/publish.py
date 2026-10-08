#!/usr/bin/env python3
"""Publish the validated vertex diagnostics and app only, with guarded rollback copies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT/'docs/measurements/sm64-vertex-perf-20261004'
STAGE = OUT/'SM64'
DEST = ROOT.parent/'SM64'
BACKUP = DEST/'.obj/vertex-perf-backup-20261004'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
load = lambda p: json.loads(p.read_text())


def atomic_copy(src, dst):
    with tempfile.NamedTemporaryFile(dir=dst.parent, prefix='.'+dst.name+'.',delete=False) as f:
        temporary = Path(f.name)
        with src.open('rb') as source:
            shutil.copyfileobj(source,f)
        f.flush()
        os.fsync(f.fileno())
    shutil.copymode(src,temporary)
    assert sha(temporary) == sha(src)
    os.replace(temporary,dst)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--verify',action='store_true')
    args = p.parse_args()
    report = load(OUT/'results.json')
    assert report['paired_framebuffer_depth_comparisons'] == 10
    assert report['frozen_snapshot_records'] == 77
    assert report['disabled_loadable_check']['only_assertion_line_numbers_changed']
    assert report['snapshot_words'] == 39
    before = load(OUT/'before.json')
    after = dict(report['source_sha256'])
    after['.obj/sm64/app.elf'] = report['normal_app_sha256']
    for rel in ('.obj/sm64-perfdiag/app.elf','build/pocket/sm64/Assets/sm64/common/sm64.elf'):
        after[rel] = report['diagnostic_app_sha256']
    staged = {rel: (STAGE/'.obj/sm64-perfdiag/app.elf' if rel.startswith('build/') else STAGE/rel)
              for rel in after}
    for rel,value in after.items():
        assert sha(staged[rel]) == value, ('Staged source/app changed',rel)
    for rel in before.keys() | after.keys():
        expected = after.get(rel,before.get(rel)) if args.verify else before.get(rel)
        assert sha(DEST/rel) == expected, ('Destination changed',rel)
    if args.verify:
        publication = load(OUT/'published.json')
        for row in publication['files']:
            assert sha(BACKUP/row['path']) == row['before'], row['path']
        print('PASS: source, normal/diagnostic apps, package and rollback hashes; SDK/runtime unchanged.')
        return
    records = [dict(path=rel,before=before.get(rel),after=value) for rel,value in after.items()]
    print(json.dumps(dict(destination=str(DEST),backup=str(BACKUP),files=list(after)),indent=2))
    if not args.apply:
        return
    BACKUP.mkdir(parents=True,exist_ok=False)
    for row in records:
        if row['before'] is None:
            continue
        dest = BACKUP/row['path']
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(DEST/row['path'],dest)
        assert sha(dest) == row['before']
    (BACKUP/'files.json').write_text(json.dumps(records,indent=2)+'\n')
    (BACKUP/'restore.py').write_text('''#!/usr/bin/env python3
import hashlib,json,shutil
from pathlib import Path
b=Path(__file__).resolve().parent;r=b.parent.parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
rows=json.loads((b/'files.json').read_text())
for x in rows:
    assert h(r/x['path'])==x['after'],('Destination changed',x['path'])
    assert h(b/x['path'])==x['before'],('Backup changed',x['path'])
for x in rows:
    if x['before'] is None:(r/x['path']).unlink()
    else:shutil.copyfile(b/x['path'],r/x['path'])
print('P2 CACHE source, normal/diagnostic apps and local package restored. Pocket SD is unchanged.')
''')
    for row in records:
        atomic_copy(staged[row['path']],DEST/row['path'])
        assert sha(DEST/row['path']) == row['after']
    (OUT/'published.json').write_text(json.dumps(dict(destination=str(DEST),backup=str(BACKUP),
        files=records,pocket_sd_updated=False),indent=2)+'\n')
    print('Validated P3 source and apps published. Pocket SD is unchanged.')


if __name__ == '__main__':
    main()
