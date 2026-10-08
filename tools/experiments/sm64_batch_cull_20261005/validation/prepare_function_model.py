#!/usr/bin/env python3
"""Permit the standalone actual-function app to omit SM64 level-entry hooks."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];OUT=ROOT/'docs/measurements/sm64-batch-cull-20261005/validation'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
old=OUT/'qsim-observed';new=OUT/'qsim-functions';new.mkdir(exist_ok=False)
for p in old.iterdir():
 if p.suffix in ('.c','.h') or p.name=='Makefile':shutil.copy2(p,new/p.name)
p=new/'env.c';s=p.read_text();a='        if (!sym || qsim_add_hook(cpu, sym->addr)) abort();';b='        if (sym && qsim_add_hook(cpu, sym->addr)) abort();';assert s.count(a)==1;s=s.replace(a,b,1);p.write_text(s)
with (OUT/'qsim-functions-build.log').open('w') as f:subprocess.run(['make','-j3'],cwd=new,stdout=f,stderr=subprocess.STDOUT,check=True)
(OUT/'qsim-functions-manifest.json').write_text(json.dumps(dict(scope='Only omit absent game level hooks in a standalone app; CPU/FPU execution and OS services remain byte-identical.',parent_sources={p.name:sha(p) for p in old.iterdir() if p.suffix in ('.c','.h') or p.name=='Makefile'},sources={p.name:sha(p) for p in new.iterdir() if p.is_file()},exe_sha256=sha(new/'qsim')),indent=2)+'\n')
