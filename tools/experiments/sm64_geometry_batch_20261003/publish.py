#!/usr/bin/env python3
"""Review/apply only the verified SM64 software update; preserve the bitstream."""
import argparse,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-geometry-batch-20261003'
STAGE=OUT/'SM64';DEST=ROOT.parent/'SM64'
BACKUP=DEST/'.obj/geometry-batch-backup-20261003'
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');a=p.parse_args()
r=json.loads((OUT/'results.json').read_text())
assert r['paired_framebuffer_depth_comparisons']==48 and r['runtime_unchanged']
assert digest(STAGE/'.obj/sm64/app.elf')==r['app_sha256']
expected=json.loads((OUT/'before.json').read_text())
for rel,sha in expected.items():assert digest(DEST/rel)==sha,('Destination changed since review',rel)
for rel,sha in r['source_sha256'].items():assert digest(STAGE/rel)==sha,('Source changed after validation',rel)
files=list(r['source_sha256'])+['GEOMETRY-TRIAL.txt']
records=[dict(path=rel,before=expected[rel],after=digest(STAGE/rel),bytes=(STAGE/rel).stat().st_size) for rel in files]
print(json.dumps(dict(destination=str(DEST),backup=str(BACKUP),files=records),indent=2))
if not a.apply:raise SystemExit(0)
BACKUP.mkdir(parents=True,exist_ok=False)
for row in records:
    dst=BACKUP/row['path'];dst.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(DEST/row['path'],dst);assert digest(dst)==row['before']
shutil.copy2(DEST/'.obj/sm64/app.elf',BACKUP/'app.elf')
(BACKUP/'files.json').write_text(json.dumps(records,indent=2)+'\n')
(BACKUP/'restore.py').write_text('''#!/usr/bin/env python3
import hashlib,json,shutil
from pathlib import Path
b=Path(__file__).resolve().parent;r=b.parent.parent
h=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rows=json.loads((b/'files.json').read_text())
for x in rows:assert h(r/x['path'])==x['after'],('File changed; review before restoring',x['path'])
for x in rows:shutil.copyfile(b/x['path'],r/x['path'])
for name in ('gfx_pc','gfx_gpu'):
    for suffix in ('.o','.d'):(r/'.obj/sm64/sm64/src/pc/gfx'/(name+suffix)).unlink(missing_ok=True)
print('Previous accelerator trial restored. Run make -C src/sm64, then make copy when ready.')
''')
for row in records:
    shutil.copyfile(STAGE/row['path'],DEST/row['path']);assert digest(DEST/row['path'])==row['after']
# Rebuild both sides of the renderer interface; never reuse experimental objects.
for name in ('gfx_pc','gfx_gpu'):
    for suffix in ('.o','.d'):(DEST/'.obj/sm64/sm64/src/pc/gfx'/(name+suffix)).unlink(missing_ok=True)
(OUT/'published.json').write_text(json.dumps(dict(destination=str(DEST),backup=str(BACKUP),files=records),indent=2)+'\n')
print('SM64 software updated. Bitstream preserved. Run the local build before copying to hardware.')
