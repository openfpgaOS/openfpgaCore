#!/usr/bin/env python3
"""Review/apply the verified SM64 candidate with guarded source/runtime rollback."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / 'docs/measurements/sm64-geometry-fast-20261003'
STAGE = OUT / 'SM64'
DEST = ROOT.parent / 'SM64'
BACKUP = DEST / '.obj/geometry-fast-backup-20261003'
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
load = lambda p: json.loads(p.read_text())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = load(OUT / 'results.json')
    assert report['paired_framebuffer_depth_comparisons'] == 72
    assert digest(STAGE / '.obj/sm64/app.elf') == report['app_sha256']
    expected = load(OUT / 'before.json')
    expected['src/sm64/sm64/src/pc/gfx/gfx_vertex_cull.h'] = None
    for rel, before in expected.items():
        assert digest(DEST / rel) == before, ('Destination changed since review', rel)
    for rel, after in {**report['source_sha256'], **report['runtime_sha256']}.items():
        assert digest(STAGE / rel) == after, ('Staged file changed after validation', rel)
    assert digest(DEST / 'runtime/pocket/os.bin') == report['os_bin_unchanged_sha256']
    old_app = load(ROOT / 'docs/measurements/sm64-geometry-batch-20261003/results.json')['app_sha256']
    assert digest(DEST / '.obj/sm64/app.elf') == old_app, 'Published app changed since baseline'
    paths = list(report['source_sha256']) + ['GEOMETRY-TRIAL.txt',
            'runtime/pocket/ap_core.sof', 'runtime/pocket/os30.rbf_r', 'runtime/MANIFEST']
    records = [dict(path=rel, before=expected[rel], after=digest(STAGE / rel),
                    bytes=(STAGE / rel).stat().st_size) for rel in paths
               if expected[rel] != digest(STAGE / rel)]
    publication = dict(destination=str(DEST), backup=str(BACKUP), files=records)
    print(json.dumps(publication, indent=2))
    if not args.apply:
        return
    BACKUP.mkdir(parents=True, exist_ok=False)
    # Finish and verify the backup before modifying any destination file.
    for row in records:
        if row['before'] is not None:
            dst = BACKUP / row['path']
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(DEST / row['path'], dst)
            assert digest(dst) == row['before']
    shutil.copy2(DEST / '.obj/sm64/app.elf', BACKUP / 'app.elf')
    assert digest(BACKUP / 'app.elf') == old_app
    (BACKUP / 'files.json').write_text(json.dumps(records, indent=2) + '\n')
    (BACKUP / 'restore.py').write_text('''#!/usr/bin/env python3
import hashlib,json,shutil
from pathlib import Path
b=Path(__file__).resolve().parent;r=b.parent.parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
rows=json.loads((b/'files.json').read_text())
for x in rows:
    assert h(r/x['path'])==x['after'],('File changed; review before restoring',x['path'])
for x in rows:
    p=r/x['path']
    if x['before'] is None:p.unlink()
    else:shutil.copyfile(b/x['path'],p)
for name in ('gfx_pc','gfx_gpu'):
    for suffix in ('.o','.d'):(r/'.obj/sm64/sm64/src/pc/gfx'/(name+suffix)).unlink(missing_ok=True)
print('Previous source/runtime restored. Run make -C src/sm64 before make copy.')
''')
    for row in records:
        dst = DEST / row['path']
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(STAGE / row['path'], dst)
        assert digest(dst) == row['after']
    for name in ('gfx_pc', 'gfx_gpu'):
        for suffix in ('.o', '.d'):
            (DEST / '.obj/sm64/sm64/src/pc/gfx' / (name + suffix)).unlink(missing_ok=True)
    (OUT / 'published.json').write_text(json.dumps(publication, indent=2) + '\n')
    print('SM64 source and runtime updated. Rebuild the app/package locally; no SD-card copy performed.')

if __name__ == '__main__':
    main()
