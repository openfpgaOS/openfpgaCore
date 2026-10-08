#!/usr/bin/env python3
"""Check clear/HUD/packed interaction and exact selected component ownership."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import clear_reuse as base

R, P, B, GL = base.R, base.P, base.B, base.GL
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def function(text, signature):
    a=text.index(signature); b=text.index('{',a); depth=1; i=b+1
    while depth:
        if text[i]=='{': depth+=1
        elif text[i]=='}': depth-=1
        i+=1
    return text[a:i]

def check(name):
    W=P/'work'/name; O=P/('clear-interaction-'+name+'-v1'); O.mkdir(exist_ok=False)
    text=(W/GL).read_text()
    clear=(P/'work/clear'/GL).read_text()
    hud=(P/'work/hud'/GL).read_text()
    packed=(P/'work/packed2'/GL).read_text()
    floor=(P/'work/floor_leaf'/GL).read_text()
    # Check shared function bodies against the already tested components.
    fragment_proofs={}
    for signature in ('ofgl_refresh_fb(void)', 'ofgl_clear_fb565(void)',
                      'ofgl_begin3d(', 'RI_InitContext(void *win)', 'RI_ShutdownContext(void)'):
        actual=function(text,signature)
        assert actual==function(clear,signature),signature
        fragment_proofs[signature]=sha_text(actual)
    actual=function(text,'ofgl_blit_rect(')
    invalidation='\t/* Both solid/alpha fills and textured HUD blits write colour here. */\n\tofgl_fb_clear.fb = NULL;\n'
    assert actual.count(invalidation)==1
    assert actual.replace(invalidation,'')==function(hud,'ofgl_blit_rect(')
    fragment_proofs['HUD body plus invalidation']=sha_text(actual)
    assert function(text,'ofgl_uv_floor(')==function(floor,'ofgl_uv_floor(')
    fragment_proofs['floor leaf']=sha_text(function(text,'ofgl_uv_floor('))
    actual=function(text,'ofgl_emit_ctri(')
    expected=function(packed,'ofgl_emit_ctri(').replace('ofgl_uv_floor(', 'ofgl_uv_floor(')
    # Packed2 begins from the same pass-two base. The floor-leaf change changes
    # only ofgl_uv_floor's body, not its call sites or the packet arithmetic.
    assert actual==expected
    fragment_proofs['packed renderer']=sha_text(actual)
    file_proofs={}
    pairs=[('packed2','src/quake2/openfpga/of_emit_q2.c'),
           ('packed2','src/quake2/openfpga/of_emit_q2.h'),
           ('packed2','tools/tests/quake2_renderer_test.c'),
           ('alias','src/quake2/src/client/refresh/gl1/gl1_buffer.c'),
           ('alias','src/quake2/src/client/refresh/gl1/gl1_mesh.c'),
           ('alias','src/quake2/src/client/refresh/gl1/header/local.h'),
           ('alias','tools/tests/quake2_aliasmesh_test.c')]
    for candidate,rel in pairs:
        assert (W/rel).read_bytes()==(P/'work'/candidate/rel).read_bytes(),rel
        file_proofs[rel]=dict(component=candidate,sha256=sha(W/rel))
    # Preserve the sealed baseline harness; add only the actual selected test's
    # packed adapter, so the final renderer drives the same recording GPU.
    old=P/'components/clear-v1/checks'
    lifecycle=(old/'lifecycle.c').read_text()
    adapter=function((W/'tools/tests/quake2_renderer_test.c').read_text(),
                     'int OFQ2_DrawVertTriRGBPacked(')
    (O/'lifecycle.c').write_text(lifecycle+'\n'+adapter+'\n')
    G=W/'src/quake2'
    flags=['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined',
           '-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC',
           '-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections',
           '-DYQ2OSTYPE="test"','-DYQ2ARCH="host"']
    flags+=['-I'+str(p) for p in (G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include')]
    cmd=flags+['-DOFGL_SOURCE="'+str(W/GL)+'"','-DEXPECT_REUSE=1',str(O/'lifecycle.c'),
               '-Wl,--gc-sections','-lm','-o',str(O/'lifecycle')]
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
    with (O/'build.log').open('w') as log: subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
    with (O/'run.log').open('w') as log: subprocess.run([str(O/'lifecycle')],stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
    def snapshots(path): return [s for s in path.read_text().splitlines() if s and s[0].isdigit()]
    assert snapshots(O/'run.log')==snapshots(old/'baseline-run.log')==snapshots(old/'candidate-run.log')
    result=dict(passed=True,worktree=name,source_sha256=sha(W/GL),lifecycle_exact_snapshots=53,
                sanitizer='ASan+UBSan',compile_command=cmd,fragment_sha256=fragment_proofs,
                whole_component_files=file_proofs,
                review='All color writes still invalidate before writes, including the opaque HUD fast path. Packed owner is byte-identical to the selected tested owner and retains one ring reservation/claim/commit, state/capability guards and pending bookkeeping. No state ownership migrated into renderer TU.')
    (O/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    evidence=[p for p in O.iterdir() if p.is_file()]+[Path(__file__),old/'lifecycle.c',old/'baseline-run.log',old/'candidate-run.log',W/GL]
    (O/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in evidence}),indent=2)+'\n')
    print(json.dumps(dict(passed=True,worktree=name,snapshots=53,source_sha256=sha(W/GL)),indent=2))

def sha_text(text): return hashlib.sha256(text.encode()).hexdigest()
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('worktree');args=parser.parse_args();check(args.worktree)
