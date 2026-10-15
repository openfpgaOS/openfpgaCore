#!/usr/bin/env python3
from pathlib import Path
import hashlib,json,subprocess,sys
root=Path(__file__).resolve().parent
m=json.loads((root/'manifest.json').read_text())
actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
assert actual==set(m['files'])|{'manifest.json'},'inventory differs'
for name,digest in m['files'].items():
 p=root/name
 assert p.is_file() and not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
for name in ('app','core'):
 subprocess.run([sys.executable,str(root/name/'package.py'),'verify'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
print('PASS: complete native5 package inventory, app source and unchanged core source/artifact')
