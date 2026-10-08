#!/usr/bin/env python3
"""Compose frozen aspect and dedicated-texture changes into private F6 only."""
import hashlib,importlib.util,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
CAMPAIGN=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMPAIGN/'phase4/combined'
STAGE=OUT/'SM64'
CANDIDATE=OUT/'aspect-texmem-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def build_module():
    path=Path(__file__).parents[1]/'build.py'
    spec=importlib.util.spec_from_file_location('retained_build',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT;m.STAGE=STAGE;return m

def main():
    m=build_module()
    if len(sys.argv)>1:m.main();return
    STAGE.mkdir(parents=True,exist_ok=False)
    expected=json.loads((CAMPAIGN/'phase4/division/source-baseline.json').read_text())['files']
    assert len(expected)==6747
    for rel,h in expected.items():
        source=CAMPAIGN/'phase2/SM64'/rel;assert sha(source)==h,rel
        target=STAGE/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    CANDIDATE.mkdir()
    sources={name:CAMPAIGN/'phase2/exact-v1'/name for name in m.INPUTS}
    sources['gfx_pc.c']=CAMPAIGN/'phase4/division/aspect-v1/gfx_pc.c'
    sources['gfx_gpu.c']=CAMPAIGN/'phase4/texmem-app/gfx_gpu.c'
    assert sha(sources['gfx_pc.c'])=='0d292c56947f98e89333a7c87d781a729d28c4dce96c428f0d5303e028119439'
    # The texture leaf must match its frozen generator manifest. Root/reviewer
    # separately close helper safety and real-memory behavior before this runs.
    texmanifest=json.loads((CAMPAIGN/'phase4/texmem-app/manifest.json').read_text())
    assert sha(sources['gfx_gpu.c'])==texmanifest['source_sha256']['gfx_gpu.c']
    helperproof=CAMPAIGN/'phase4/texmem-app/helper-tests-v2/results.json'
    helper=json.loads(helperproof.read_text())
    assert helper['passed'] and helper['candidate_sha256']==sha(sources['gfx_gpu.c'])
    for name,path in sources.items():shutil.copy2(path,CANDIDATE/name)
    panel=CANDIDATE/'perf_pocket.c';text=panel.read_text();assert text.count('PERF F5 FPS')==1
    panel.write_text(text.replace('PERF F5 FPS','PERF F6 FPS'))
    inputs={name:sha(CANDIDATE/name)for name in m.INPUTS}
    expected.update({str(rel):inputs[name]for name,rel in m.INPUTS.items()})
    (OUT/'source-inputs.json').write_text(json.dumps({'files':expected,'parent':'phase2/exact-v1','candidate':'aspect-texmem-v1'},indent=2)+'\n')
    (CANDIDATE/'manifest.json').write_text(json.dumps({'candidate':'aspect-texmem-v1','parent':'shipped F5 phase2/exact-v1','qualified':False,'source_sha256':inputs,'source_components':{name:{'path':str(path),'sha256':sha(path)}for name,path in sources.items()},'additional_change':'Only FPS label PERF F5 FPS -> PERF F6 FPS.','source_inventory_files':len(expected),'aspect_qualification_sha256':sha(CAMPAIGN/'phase4/division/qualification.json'),'texmem_manifest_sha256':sha(CAMPAIGN/'phase4/texmem-app/manifest.json'),'helper_proof_sha256':sha(helperproof),'script_sha256':sha(Path(__file__)),'publication':False,'deployment':False},indent=2)+'\n')
    print(CANDIDATE)
if __name__=='__main__':main()
