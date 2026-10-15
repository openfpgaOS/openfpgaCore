#!/usr/bin/env python3
import hashlib,json,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent
pins=json.loads((root/'manifest.json').read_text())['files']
actual={str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() or p.is_symlink()}
assert actual==set(pins)|{'manifest.json'}, 'Package inventory differs'
for name,digest in pins.items():
 p=root/name
 assert not p.is_symlink() and hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
for sub in ('app','core'):
 subprocess.run([sys.executable,str(root/sub/'package.py'),'verify'],check=True)
print('PASS: selected app/core, full package inventory, source and evidence hashes')
