#!/usr/bin/env python3
"""Private follow-up on the hardware-tested F4 build; never publish or deploy."""
import argparse, hashlib, importlib.util, json, shutil, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
CAMPAIGN=ROOT/'docs/measurements/os30-fps25-20261005'
OUT=CAMPAIGN/'phase2'
STAGE=OUT/'SM64'
FILES=('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h','perf_pocket.h','perf_pocket.c','Makefile.sm64')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def prepare():
    STAGE.mkdir(parents=True,exist_ok=False)
    prior=CAMPAIGN/'SM64'
    expected=json.loads((CAMPAIGN/'independent-review/render-v6-stage-inputs.json').read_text())['files']
    for rel,h in expected.items():
        source=prior/rel
        assert sha(source)==h,rel
        target=STAGE/rel;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    baseline=OUT/'baseline';baseline.mkdir()
    for name in FILES:
        source=CAMPAIGN/'render-v6'/name
        if not source.exists():source=CAMPAIGN/'baseline'/name
        shutil.copy2(source,baseline/name)
    for name,source in [('normal.elf',CAMPAIGN/'render-v6/app.elf'),('diagnostic.elf',CAMPAIGN/'render-v6/diagnostic.elf')]:
        shutil.copy2(source,baseline/name)
    (OUT/'source-baseline.json').write_text(json.dumps({'files':expected,'parent':'render-v6','source':str(prior)},indent=2)+'\n')
    print('Frozen render-v6 into private phase2 stage:',len(expected),'files')

def split():
    dest=OUT/'vertex-two-pass-v1';dest.mkdir(exist_ok=False)
    for name in FILES:shutil.copy2(OUT/'baseline'/name,dest/name)
    path=dest/'gfx_pc.c';source=path.read_text()
    begin=source.index('static __attribute__((noinline)) void gfx_sp_vertex(')
    end=source.index('\nstatic inline struct ColorCombiner *gfx_pick_combiner',begin)
    fn=source[begin:end]
    start=fn.index('        short U =')
    finish=fn.index('        // trivial clip rejection',start)
    attributes=fn[start:finish]
    fn=fn[:start]+fn[finish:]
    vn='        const Vtx_tn *vn = &vertices[i].n;\n'
    assert fn.count(vn)==1;fn=fn.replace(vn,'')
    loop='    for (size_t i = 0; i < n_vertices; i++, dest_index++) {'
    assert fn.count(loop)==1
    fn=fn.replace(loop,'    const size_t attribute_dest_index = dest_index;\n'+loop)
    tail='    }\n    }\n#ifdef TARGET_POCKET'
    assert fn.count(tail)==1
    separate=('    }\n    /* Finish the eager UV/color work after the matrix registers die. */\n'
        '    for (size_t i = 0; i < n_vertices; i++) {\n'
        '        const Vtx_t *v = &vertices[i].v;\n'
        '        const Vtx_tn *vn = &vertices[i].n;\n'
        '        struct LoadedVertex *d = &rsp.loaded_vertices[attribute_dest_index + i];\n\n'
        +attributes+'    }\n    }\n#ifdef TARGET_POCKET')
    fn=fn.replace(tail,separate)
    path.write_text(source[:begin]+fn+source[end:])
    (dest/'manifest.json').write_text(json.dumps({'parent':'render-v6','candidate':'vertex-two-pass-v1',
        'qualified':False,'source_sha256':sha(path),'parent_sha256':sha(OUT/'baseline/gfx_pc.c'),
        'attribute_block_sha256':hashlib.sha256(attributes.encode()).hexdigest(),
        'change':'Eager position/clip/reciprocal/alpha pass followed by original eager UV/RGB lighting pass. No arithmetic rewritten.',
        'required_checks':['Source ordering/alias review','Code generation and paired model cost','Exact gameplay/color/depth checks']},indent=2)+'\n')
    print(dest)

def build(candidate,diagnostic):
    spec=importlib.util.spec_from_file_location('retained_build',Path(__file__).with_name('build.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.OUT=OUT;module.STAGE=STAGE
    sys.argv=[str(Path(__file__).with_name('build.py')),candidate,'--diagnostic',str(diagnostic)]
    module.main()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','split','build'])
    p.add_argument('--candidate',default='vertex-two-pass-v1')
    p.add_argument('--diagnostic',type=int,choices=(0,1),default=1)
    a=p.parse_args()
    if a.action=='prepare':prepare()
    elif a.action=='split':split()
    else:build(a.candidate,a.diagnostic)
