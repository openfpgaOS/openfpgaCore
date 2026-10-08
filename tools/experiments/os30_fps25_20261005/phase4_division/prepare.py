#!/usr/bin/env python3
"""Freeze a private F5 control and one exact aspect-quotient candidate."""
import difflib, hashlib, importlib.util, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CAMPAIGN = ROOT/'docs/measurements/os30-fps25-20261005'
OUT = CAMPAIGN/'phase4/division'
STAGE = OUT/'SM64'
PARENT = CAMPAIGN/'phase2/exact-v1'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def build_module():
    p = Path(__file__).parents[1]/'build.py'
    spec = importlib.util.spec_from_file_location('retained_build', p)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.OUT = OUT; m.STAGE = STAGE
    return m

def main():
    m = build_module()
    if len(sys.argv)>1:
        m.main(); return
    STAGE.mkdir(parents=True, exist_ok=False)
    expected = json.loads((CAMPAIGN/'phase2/source-baseline.json').read_text())['files']
    expected.update({str(rel):sha(PARENT/name) for name,rel in m.INPUTS.items()})
    assert len(expected)==6747
    prior=CAMPAIGN/'phase2/SM64'
    for rel,digest in expected.items():
        source=prior/rel; assert sha(source)==digest,rel
        target=STAGE/rel; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source,target)
    for label in ('control-f5','aspect-v1'):
        dest=OUT/label;dest.mkdir()
        for name in m.INPUTS:shutil.copy2(PARENT/name,dest/name)
    path=OUT/'aspect-v1/gfx_pc.c';source=path.read_text();original=source
    global_line='struct GfxDimensions gfx_current_dimensions;'
    assert source.count(global_line)==1
    source=source.replace(global_line,global_line+'\n/* Installed with aspect_ratio at frame start, before any display list. */\nstatic float gfx_vertex_aspect_scale;')
    old='    // Fold the per-frame aspect-ratio divide into one reciprocal (was a per-vertex fdiv).\n    const float aspect_scale = (4.0f / 3.0f) / (float)gfx_current_dimensions.aspect_ratio;'
    assert source.count(old)==1
    source=source.replace(old,'    const float aspect_scale = gfx_vertex_aspect_scale;')
    old='    gfx_current_dimensions.aspect_ratio = (float)gfx_current_dimensions.width / (float)gfx_current_dimensions.height;'
    assert source.count(old)==1
    source=source.replace(old,old+'\n    /* Preserve the original rounded aspect before the quotient: fast-math\n     * must not reassociate it through the width/height expression above. */\n    volatile float rounded_aspect = gfx_current_dimensions.aspect_ratio;\n    gfx_vertex_aspect_scale = (4.0f / 3.0f) / rounded_aspect;')
    path.write_text(source)
    (OUT/'aspect-v1/change.patch').write_text(''.join(difflib.unified_diff(original.splitlines(True),source.splitlines(True),fromfile='control-f5/gfx_pc.c',tofile='aspect-v1/gfx_pc.c')))
    (OUT/'source-baseline.json').write_text(json.dumps({'parent':'phase2/exact-v1','files':expected,'source':str(prior),'parent_normal_elf_sha256':sha(PARENT/'app.elf'),'parent_diagnostic_elf_sha256':sha(PARENT/'diagnostic.elf')},indent=2)+'\n')
    (OUT/'aspect-v1/manifest.json').write_text(json.dumps({'candidate':'aspect-v1','parent':'phase2/exact-v1','qualified':False,'source_sha256':{name:sha(OUT/'aspect-v1'/name)for name in m.INPUTS},'scope':'Cache original float quotient once at frame start; volatile local prevents reassociation through rounded aspect. No other expression, GPU code, or feature change.','initialization':'Existing lifecycle requires gfx_start_frame to install dimensions/aspect before display lists; standalone renderer fixtures that bypass this must install the new cache too.','script_sha256':sha(Path(__file__))},indent=2)+'\n')
    print('Prepared private stage with',len(expected),'sources:',OUT)

if __name__=='__main__':main()
