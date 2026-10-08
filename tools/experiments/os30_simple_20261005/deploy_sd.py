#!/usr/bin/env python3
"""Update only the qualified SM64 app on the previously selected A Pocket card."""
import argparse,hashlib,json,os,shutil,subprocess,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-simple-20261005'
CARD=Path('/run/media/alberto/POCKETDEV')
APP='Assets/sm64/common/sm64.elf'
PROTECTED={
 'Cores/ThinkElastic.SM64/os30.rbf_r':'d3d90e20c7116304108a2f94b65d3a69335347603fab388b905cafc3fc6f5f9f',
 'Assets/sm64/common/os.bin':'054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7'
}
BEFORE='990ba23281d6793a479df4c9094cb0cd600894ca3ab5548df360a62c016f2984'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def check_mount():
 r=subprocess.run(['findmnt','--json','--target',str(CARD),'-o','TARGET,SOURCE,FSTYPE'],check=True,capture_output=True,text=True)
 m=json.loads(r.stdout)['filesystems'][0]
 assert m['target']==str(CARD) and m['fstype']=='vfat',m
 assert (CARD/APP).resolve().is_relative_to(CARD.resolve())
 return m

def main():
 p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');a=p.parse_args()
 selected=read(OUT/'selection.json')
 assert selected['qualified'] and selected['core']=='A-single'
 published=read(OUT/'published.json')
 assert published['selection_sha256']==sha(OUT/'selection.json')
 source=ROOT.parent/'SM64/build/pocket/sm64'/APP
 expected=selected['diagnostic_app_sha256'];assert sha(source)==expected
 mount=check_mount();target=CARD/APP
 assert sha(target)==BEFORE,'Card app changed since retained deployment'
 for rel,digest in PROTECTED.items():assert sha(CARD/rel)==digest,rel
 record={'card':str(CARD),'mount':mount,'path':APP,'before_sha256':BEFORE,'after_sha256':expected,
         'core':'A-single','core_written':False,'protected_sha256':PROTECTED,
         'selection_sha256':sha(OUT/'selection.json'),'publication_sha256':sha(OUT/'published.json'),
         'synced':False,'verified':False,'sd_changes':'SM64 application only; retained A bitstream and OS checked unchanged'}
 print(json.dumps({'mode':'apply' if a.apply else 'dry-run',**record},indent=2),flush=True)
 if not a.apply:return
 backup=OUT/'sd-backup';backup.mkdir(exist_ok=False)
 shutil.copy2(target,backup/'sm64.elf');assert sha(backup/'sm64.elf')==BEFORE
 record['backup']=str(backup/'sm64.elf')
 (backup/'before.json').write_text(json.dumps(record,indent=2)+'\n')
 assert check_mount()==mount
 assert sha(target)==BEFORE
 temporary=None
 try:
  with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.sm64-qualified-',delete=False) as f:
   temporary=Path(f.name)
   with source.open('rb') as incoming:shutil.copyfileobj(incoming,f)
   f.flush();os.fsync(f.fileno())
  assert sha(temporary)==expected
  assert check_mount()==mount
  os.replace(temporary,target);temporary=None
  os.sync();record['synced']=True
  assert sha(target)==expected
  for rel,digest in PROTECTED.items():assert sha(CARD/rel)==digest,rel
  record['verified']=True
  (OUT/'sd-deployed.json').write_text(json.dumps(record,indent=2)+'\n')
  print('PASS: qualified diagnostic app copied, synced and read back; A core and OS unchanged.',flush=True)
 finally:
  if temporary is not None:temporary.unlink(missing_ok=True)

if __name__=='__main__':main()
