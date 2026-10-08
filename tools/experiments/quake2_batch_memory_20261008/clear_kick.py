#!/usr/bin/env python3
"""Publish only the next-frame clear immediately, preserving advisory 3D kicks."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-batch-memory-20261008'
B=P/'work/base';W=P/'work/clear_kick';O=P/'components/clear-kick-v1'
GL='src/quake2/openfpga/of_gl1.c';EM='src/quake2/openfpga/of_emit_q2.c';HD='src/quake2/openfpga/of_emit_q2.h';TEST='tools/tests/quake2_renderer_test.c'
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def put(path,value): path.write_text(json.dumps(value,indent=2)+'\n')
def function(text,signature):
    a=text.index(signature);b=text.index('{',a);depth=1;i=b+1
    while depth:
        if text[i]=='{':depth+=1
        elif text[i]=='}':depth-=1
        i+=1
    return text[a:i]

def prepare():
    assert sha(B/GL)=='4e67f9c34ed4be2d1db8b0bd81e2980624991afda3c277d4432258ecbeaae10b'
    assert not W.exists() and not O.exists()
    O.mkdir(parents=True);shutil.copytree(B,W,ignore=shutil.ignore_patterns('.obj','build','__pycache__'))
    text=(B/GL).read_text();old=function(text,'RI_EndFrame(void)')
    assert old.count('OFQ2_Kick();')==1
    new=old.replace('''\t\tOFQ2_Kick();''','''\t\t/* Advisory kicks retain short batches; this clear must start now. */
\t\tOFQ2_KickNow();''')
    (W/GL).write_text(text.replace(old,new,1))
    text=(B/EM).read_text();old=function(text,'void\nOFQ2_Kick(void)')
    wrapper='''

/* Publish a frame-boundary batch without waiting for its GPU completion. */
void
OFQ2_KickNow(void)
{
\tif (ofq2_ready)
\t{
\t\tof_gpu_kick_now();
\t}
}'''
    (W/EM).write_text(text.replace(old,old+wrapper,1))
    text=(B/HD).read_text();assert text.count('void OFQ2_Kick(void);')==1
    (W/HD).write_text(text.replace('void OFQ2_Kick(void);','void OFQ2_Kick(void);\n/* Publish immediately; leaves pending work for the existing Finish barriers. */\nvoid OFQ2_KickNow(void);',1))
    text=(B/TEST).read_text();assert text.count('void OFQ2_Kick(void) { }')==1
    text=text.replace('void OFQ2_Kick(void) { }','void OFQ2_Kick(void) { }\nvoid OFQ2_KickNow(void) { }',1)
    (W/TEST).write_text(text)
    changed=[GL,EM,HD,TEST]
    patch=[]
    for rel in changed:
        patch.extend(difflib.unified_diff((B/rel).read_text().splitlines(True),(W/rel).read_text().splitlines(True),fromfile='a/'+rel,tofile='b/'+rel))
    (O/'source.patch').write_text(''.join(patch));shutil.copy2(__file__,O/'recipe.py')
    put(O/'prepared.json',dict(worktree=str(W),files={rel:dict(before=sha(B/rel),after=sha(W/rel)) for rel in changed},
        scope='Only RI_EndFrame requests unconditional publication. Existing advisory per-3D-batch Kick, pending flags, Finish waits, clear tracking and SDK threshold are unchanged.'))
    print(json.dumps(dict(worktree=str(W),gl=sha(W/GL),owner=sha(W/EM)),indent=2))

if __name__=='__main__':prepare()
