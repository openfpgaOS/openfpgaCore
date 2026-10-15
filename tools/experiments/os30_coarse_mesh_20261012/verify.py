#!/usr/bin/env python3
"""Verify source-only inventory; optionally exact local patches and external evidence."""
from pathlib import Path
import argparse, hashlib, json, shutil, subprocess, tempfile
P=Path(__file__).resolve().parent; sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
p=argparse.ArgumentParser(description=__doc__); p.add_argument('--patches',action='store_true');p.add_argument('--evidence',action='store_true');a=p.parse_args()
m=json.loads((P/'manifest.json').read_text())
actual={str(q.relative_to(P)):sha(q) for q in P.rglob('*') if q.is_file() and str(q.relative_to(P)) not in m['excluded_inventory'] and '__pycache__' not in q.parts}
assert actual==m['files'],'Bundle inventory mismatch'
for name in actual:
    assert Path(name).suffix not in {'.elf','.bin','.rbf','.rbf_r','.mif','.z64','.n64','.v64','.o','.a','.qdb','.png'},name
if a.patches:
    for name,item in m['patches'].items():
        with tempfile.TemporaryDirectory(prefix='aj03-patch-') as temp:
            out=Path(temp)
            for rel,hashes in item['files'].items():
                r=Path(rel); assert not r.is_absolute() and '..' not in r.parts
                source=Path(item['base'])/r; target=out/r
                if hashes['before_sha256'] is not None:
                    assert sha(source)==hashes['before_sha256']; target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
                else: assert not source.exists(),source
            for direction,key in [('--forward','after_sha256'),('--reverse','before_sha256')]:
                run=subprocess.run(['patch','-p1','--batch',direction,'--fuzz=0','-i',str(P/name)],cwd=out,capture_output=True,text=True)
                log=run.stdout+run.stderr; assert run.returncode==0 and 'offset' not in log.lower() and 'fuzz' not in log.lower(),log
                expected=set()
                for rel,hashes in item['files'].items():
                    if hashes[key] is None: assert not (out/rel).exists(),rel
                    else: assert sha(out/rel)==hashes[key],rel;expected.add(rel)
                assert {str(q.relative_to(out)) for q in out.rglob('*') if q.is_file()}==expected
if a.evidence:
    for name,h in m['external_evidence_sha256'].items(): assert sha(Path(name))==h,name
    for name,h in m['native_source_sha256'].items(): assert sha(Path(m['native_revision'])/name)==h,name
    for name,item in m['copied_inputs'].items(): assert sha(Path(item['source']))==item['sha256']==sha(P/name),name
    for item in m['patches'].values():
        for h in item['files'].values(): assert sha(Path(h['postimage_source']))==h['after_sha256']
print('PASS source inventory'+(f'; {len(m["patches"])} exact forward/reverse patches' if a.patches else '')+('; frozen evidence bindings' if a.evidence else ''))
