#!/usr/bin/env python3
"""Review/apply only the authorized SM64 trial files, with a verified rollback."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-geometry-20261003'
STAGE=OUT/'SM64'
DEST=ROOT.parent/'SM64'
BACKUP=DEST/'.obj/geometry-trial-backup-20261003'
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    report=json.loads((OUT/'results.json').read_text())
    assert report['paired_framebuffer_depth_comparisons']==24
    assert digest(STAGE/'.obj/sm64/app.elf')==report['app_sha256']
    expected=json.loads((OUT/'publish-expected.json').read_text())
    expected['GEOMETRY-TRIAL.txt']=None
    records=[]
    for rel,before in expected.items():
        assert digest(DEST/rel)==before,('SM64 file changed since review',rel)
        assert (STAGE/rel).is_file(),rel
        records.append(dict(path=rel,before=before,after=digest(STAGE/rel),
            bytes=(STAGE/rel).stat().st_size))
    print(json.dumps(dict(destination=str(DEST),backup=str(BACKUP),files=records),indent=2))
    if not args.apply:return
    BACKUP.mkdir(parents=True,exist_ok=False)
    # Complete the backup before changing any source or runtime file.
    for row in records:
        if row['before'] is not None:
            dst=BACKUP/row['path'];dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(DEST/row['path'],dst)
            assert digest(dst)==row['before']
    oldelf=DEST/'.obj/sm64/app.elf'
    if oldelf.exists():shutil.copy2(oldelf,BACKUP/'app.elf')
    (BACKUP/'files.json').write_text(json.dumps(records,indent=2)+'\n')
    (BACKUP/'restore.py').write_text('''#!/usr/bin/env python3
import hashlib,json,shutil
from pathlib import Path
b=Path(__file__).resolve().parent;r=b.parent.parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
rows=json.loads((b/'files.json').read_text())
for x in rows:
    assert h(r/x['path'])==x['after'],('File changed after trial; review before restoring',x['path'])
for x in rows:
    p=r/x['path']
    if x['before'] is None:p.unlink()
    else:shutil.copyfile(b/x['path'],p)
# Remove only disposable object/dependency files for the changed renderer.
for name in ('gfx_pc','gfx_gpu'):
    for suffix in ('.o','.d'):
        (r/'.obj/sm64/sm64/src/pc/gfx'/ (name+suffix)).unlink(missing_ok=True)
print('Previous source/runtime restored. Run make -C src/sm64 before make copy.')
''')
    for row in records:
        dst=DEST/row['path'];dst.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(STAGE/row['path'],dst)
        assert digest(dst)==row['after']
    (OUT/'published.json').write_text(json.dumps(dict(destination=str(DEST),
        backup=str(BACKUP),files=records),indent=2)+'\n')
    print('SM64 trial updated. No hardware copy or git commit performed.')

if __name__=='__main__':main()
