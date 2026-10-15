#!/usr/bin/env python3
"""Portable source-package runner; recipe and source hashes are mandatory."""
import argparse, hashlib, json, os, subprocess
from pathlib import Path

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
 p=argparse.ArgumentParser();p.add_argument('--test',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
 root=Path(__file__).resolve().parent
 manifest=json.loads((root/'manifest.json').read_text())
 for item in manifest['files']:
  file=root/item['path']
  if digest(file)!=item['sha256']:raise SystemExit(f'Package input changed: {file}')
 recipes=json.loads((root/'recipes.json').read_text());recipe=recipes[a.test]
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
 sources=[str(root/x) for x in recipe['sources']]
 if recipe['kind']=='verilator':
  command=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','--top-module',recipe['top'],'--Mdir',str(out/'obj'),
   '-CFLAGS','-std=c++17 -O1 -fsanitize=address,undefined -fno-omit-frame-pointer','-LDFLAGS','-fsanitize=address,undefined',*sources]
  executable=out/'obj'/('V'+recipe['top'])
 else:raise SystemExit('Unknown recipe type')
 result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (out/'build.log').write_text(result.stdout)
 if result.returncode:print(result.stdout[-6000:]);raise SystemExit(result.returncode)
 environment=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
 result=subprocess.run([str(executable)],env=environment,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
 (out/'run.log').write_text(result.stdout);print(result.stdout)
 report={'status':'PASS' if result.returncode==0 else 'FAIL','recipe':a.test,'build_command':command,
  'package_manifest_sha256':digest(root/'manifest.json'),'result':json.loads(result.stdout) if result.returncode==0 else result.stdout,
  'sanitizers':{'address':True,'undefined':True,'leak_detection':False,'reason':'LeakSanitizer is incompatible with sandbox ptrace.'},
  'scope':recipe['scope']}
 (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');raise SystemExit(result.returncode)
if __name__=='__main__':main()
