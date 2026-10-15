#!/usr/bin/env python3
"""Verify this source-only prototype and optionally its external local evidence."""
import argparse, hashlib, json
from pathlib import Path

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',action='store_true');a=p.parse_args()
 root=Path(__file__).resolve().parent
 manifest=json.loads((root/'manifest.json').read_text());failures=[]
 for item in manifest['files']:
  path=root/item['path']
  if not path.is_file() or digest(path)!=item['sha256']:failures.append(str(path))
 extras=sorted(str(x.relative_to(root)) for x in root.rglob('*') if x.is_file() and x.name!='manifest.json' and str(x.relative_to(root)) not in {i['path'] for i in manifest['files']})
 failures.extend(extras)
 if a.evidence:
  for item in manifest['external_evidence']:
   path=Path(item['path'])
   if not path.is_file() or digest(path)!=item['sha256']:failures.append(str(path))
 print(json.dumps({'status':'FAIL' if failures else 'PASS','package_files':len(manifest['files']),
  'external_evidence_checked':len(manifest['external_evidence']) if a.evidence else 0,'failures':failures},indent=2))
 raise SystemExit(bool(failures))
if __name__=='__main__':main()
