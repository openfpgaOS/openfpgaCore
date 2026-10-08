#!/usr/bin/env python3
"""One isolated cold/noinline texture-helper placement experiment."""
import difflib,hashlib,importlib.util,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
PHASE=ROOT/'docs/measurements/os30-fps25-20261005/phase4'
OUT=PHASE/'combined-cold';STAGE=OUT/'SM64'
PARENT=PHASE/'combined/aspect-texmem-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def module():
    p=Path(__file__).parents[1]/'build.py'
    spec=importlib.util.spec_from_file_location('retained_build',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.OUT=OUT;m.STAGE=STAGE;return m

def main():
    m=module()
    if len(sys.argv)>1:m.main();return
    STAGE.mkdir(parents=True,exist_ok=False)
    expected=json.loads((PHASE/'combined/source-inputs.json').read_text())['files']
    assert len(expected)==6747
    for rel,h in expected.items():
        src=PHASE/'combined/SM64'/rel;assert sha(src)==h,rel
        dst=STAGE/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    dest=OUT/'aspect-texmem-cold-v1';dest.mkdir()
    for name in m.INPUTS:shutil.copy2(PARENT/name,dest/name)
    p=dest/'gfx_gpu.c';old=p.read_text();new=old
    for typ,name in [('void','gpu_fast_tex_fallback'),('int','gpu_fast_tex_upload'),('void','gpu_fast_tex_init')]:
        a='static '+typ+' '+name+'(';assert new.count(a)==1
        new=new.replace(a,'static __attribute__((cold, noinline)) '+typ+' '+name+'(')
    p.write_text(new)
    (dest/'change.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile='aspect-texmem-v1/gfx_gpu.c',tofile='aspect-texmem-cold-v1/gfx_gpu.c')))
    inputs={name:sha(dest/name)for name in m.INPUTS}
    expected.update({str(rel):inputs[name]for name,rel in m.INPUTS.items()})
    (OUT/'source-inputs.json').write_text(json.dumps({'files':expected,'parent':'aspect-texmem-v1','candidate':'aspect-texmem-cold-v1'},indent=2)+'\n')
    (dest/'manifest.json').write_text(json.dumps({'candidate':'aspect-texmem-cold-v1','parent':'aspect-texmem-v1','qualified':False,'source_sha256':inputs,'parent_source_sha256':{n:sha(PARENT/n)for n in m.INPUTS},'only_change':'cold,noinline attributes on gpu_fast_tex_upload/fallback/init; no statement or arithmetic change.','script_sha256':sha(Path(__file__)),'sweep':False,'published':False,'deployed':False},indent=2)+'\n')
    print(dest)
if __name__=='__main__':main()
