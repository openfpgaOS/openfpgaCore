#!/usr/bin/env python3
"""Publish only hash-guarded Quake2 source/ELF changes, with reversible backups."""
from pathlib import Path
import datetime
import difflib
import hashlib
import json
import os
import shutil
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
DEST = ROOT.parent / 'Quake2'
CAND = Path('/tmp/quake2-sm64-20261006/candidate')
EVIDENCE = ROOT/'docs/measurements/quake2-sm64-transfer-20261006'
BEFORE = json.loads((HERE/'before.json').read_text())

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None

def plan():
    files=[]
    for directory in ('src','tools','docs'):
        for p in sorted((CAND/directory).rglob('*')):
            if not p.is_file() or '__pycache__' in p.parts or p.name.startswith('._'):
                continue
            rel=str(p.relative_to(CAND))
            if sha(p)==BEFORE.get(rel): continue
            assert rel not in BEFORE or sha(DEST/rel)==BEFORE[rel], rel
            assert rel in BEFORE or not (DEST/rel).exists(), rel
            files.append(dict(path=rel,source=str(p),before=sha(DEST/rel),after=sha(p)))
    for rel,source in (
        ('.obj/quake2/app.elf',CAND/'.obj/quake2/app.elf'),
        ('build/pocket/quake2/Assets/quake2/common/quake2.elf',CAND/'.obj/quake2/app.elf'),
        ('build/diagnostics/quake2-gpuprofile.elf',CAND/'build/diagnostics/quake2-gpuprofile.elf'),
    ):
        files.append(dict(path=rel,source=str(source),before=sha(DEST/rel),after=sha(source)))
    assert len(files)==16, len(files)
    result=dict(destination=str(DEST),files=files,
                retained_core_sha256=sha(DEST/'runtime/pocket/os30.rbf_r'),
                retained_os_sha256=sha(DEST/'runtime/pocket/os.bin'))
    (HERE/'publication-plan.json').write_text(json.dumps(result,indent=2)+'\n')
    patch=[]
    for item in files:
        if item['path'].endswith('.elf'): continue
        old=(DEST/item['path']).read_text() if item['before'] else ''
        new=Path(item['source']).read_text()
        patch.extend(difflib.unified_diff(old.splitlines(True),new.splitlines(True),
                     fromfile='a/'+item['path'],tofile='b/'+item['path']))
    (HERE/'candidate.patch').write_text(''.join(patch))
    print(json.dumps(result,indent=2))

def publish():
    result=json.loads((HERE/'publication-plan.json').read_text())
    assert not (EVIDENCE/'published.json').exists()
    for rel,digest in BEFORE.items(): assert sha(DEST/rel)==digest, rel
    for item in result['files']:
        assert sha(DEST/item['path'])==item['before'],item['path']
        assert sha(Path(item['source']))==item['after'],item['path']
    backup=EVIDENCE/'publication-backup'
    backup.mkdir(parents=True,exist_ok=False)
    for item in result['files']:
        target=DEST/item['path']
        if target.exists():
            saved=backup/item['path'];saved.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,saved)
            assert sha(saved)==item['before']
    changed=[]
    try:
        for item in result['files']:
            target=DEST/item['path'];source=Path(item['source'])
            target.parent.mkdir(parents=True,exist_ok=True)
            fd,temp=tempfile.mkstemp(prefix='.'+target.name+'.q2-transfer-',dir=target.parent)
            with os.fdopen(fd,'wb') as handle:
                handle.write(source.read_bytes());handle.flush();os.fsync(handle.fileno())
            os.chmod(temp,target.stat().st_mode & 0o777 if target.exists() else source.stat().st_mode & 0o777)
            os.replace(temp,target);changed.append(item)
            assert sha(target)==item['after'],item['path']
        after={item['path']:item['after'] for item in result['files']}
        for rel,digest in BEFORE.items():assert sha(DEST/rel)==after.get(rel,digest),rel
        assert sha(DEST/'build/pocket/quake2/Cores/thinkelastic.quake2/os30.rbf_r')==result['retained_core_sha256']
        assert sha(DEST/'build/pocket/quake2/Assets/quake2/common/os.bin')==result['retained_os_sha256']
    except Exception:
        for item in reversed(changed):
            target=DEST/item['path']
            if item['before'] is None: target.unlink()
            else: shutil.copy2(backup/item['path'],target)
        raise
    result.update(verified=True,updated_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  unchanged_prior_files=sum(r not in after for r in BEFORE),
                  sd_deployed=False,core_changed=False,commit_created=False)
    (EVIDENCE/'published.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    if sys.argv[1]=='plan':plan()
    elif sys.argv[1]=='publish':publish()
    else:raise SystemExit('expected plan or publish')
