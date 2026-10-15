#!/usr/bin/env python3
"""Freeze explicitly selected, hash-bound prototype sources; never game assets."""
import argparse, hashlib, json, shutil
from pathlib import Path
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--spec',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 spec=json.loads(a.spec.read_text());out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
 external={};recipes={};bindings=[]
 def copy(src,dest,expected=None):
  src=Path(src).resolve();got=sha(src)
  if expected is not None and got!=expected:raise RuntimeError(f'Changed receipt input: {src}')
  target=out/dest;target.parent.mkdir(parents=True,exist_ok=True)
  if target.exists() and sha(target)!=got:raise RuntimeError(f'Package collision: {dest}')
  shutil.copyfile(src,target);bindings.append({'source':str(src),'package':dest,'sha256':got})
  return dest
 for name,receipt_file in spec['tests'].items():
  receipt_path=Path(receipt_file).resolve();receipt=json.loads(receipt_path.read_text())
  if receipt['status']!='PASS':raise RuntimeError(f'Failed test receipt {receipt_path}')
  external[str(receipt_path)]=sha(receipt_path);copy(receipt_path,f'evidence/{name}.json')
  sources=[]
  for item in receipt.get('sources',receipt.get('inputs',[])):
   src=Path(item['path'])
   if src.suffix not in ('.v','.sv','.cpp'):continue
   dest=('tests/' if src.suffix=='.cpp' or src.name in spec.get('test_wrappers',[]) else 'rtl/')+src.name
   sources.append(copy(src,dest,item['sha256']))
  command=receipt['build_command'];top=command[command.index('--top-module')+1]
  cpp=[Path(x) for x in command if x.endswith('.cpp')]
  for src in cpp:
   dest='tests/'+src.name
   if dest not in sources:sources.append(copy(src,dest,receipt.get('test_sha256')))
  recipes[name]={'kind':'verilator','top':top,'sources':sources,'scope':receipt['scope']}
 for entry in spec['copies']:
  src=Path(entry['source']).resolve();copy(src,entry['destination'],entry.get('sha256'))
  if entry.get('external_evidence'):external[str(src)]=sha(src)
 copy(Path(__file__).resolve(),'recipes/build_source_package.py')
 copy(a.spec,'package-spec.json')
 (out/'recipes.json').write_text(json.dumps(recipes,indent=2)+'\n')
 (out/'source-bindings.json').write_text(json.dumps(bindings,indent=2)+'\n')
 files=[{'path':str(x.relative_to(out)),'sha256':sha(x),'bytes':x.stat().st_size} for x in sorted(out.rglob('*')) if x.is_file()]
 manifest={'schema':'os30-mesh-front-source-v1','status':'EXPERIMENTAL_COMPONENT_PROTOTYPE',
  'scope':'Source-only, no assets, bitstream, full-app integration, full-frame gain or physical fit claim.',
  'files':files,'external_evidence':[{'path':k,'sha256':v} for k,v in sorted(external.items())]}
 (out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print(json.dumps({'package':str(out),'files':len(files),'bytes':sum(x['bytes'] for x in files),'manifest_sha256':sha(out/'manifest.json')},indent=2))
if __name__=='__main__':main()
