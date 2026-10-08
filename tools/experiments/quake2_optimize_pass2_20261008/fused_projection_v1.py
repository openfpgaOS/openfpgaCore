#!/usr/bin/env python3
"""Fuse projection only for CPU cache misses proven inside all batch planes."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-pass2-20261008'
B=P/'work/vertex';W=P/'work/fused';C=P/'components/fused-v1'
REL='src/quake2/openfpga/of_gl1.c'
PARENT='1be015945437d2df4fffbc792a1ba1186e816e88939c07d075dae93e3d46faab'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')
assert sha(B/REL)==PARENT and not C.exists() and not W.exists()
before=(B/REL).read_text()
start=before.index('static void\nofgl_project_cvert(ofgl_cvert_t *v)')
end=before.index('\nstatic void\nofgl_build_clip_planes',start)
projection=before[start:end]
inline_projection=projection.replace('static void\nofgl_project_cvert(', 'static inline __attribute__((always_inline)) void\nofgl_project_cvert_inline(',1)
wrapper='''
static void
ofgl_project_cvert(ofgl_cvert_t *v)
{
\tofgl_project_cvert_inline(v);
}
'''
after=before[:start]+inline_projection+wrapper+before[end:]
old='''\tif (ofgl_batch_inside)
\t\tofgl_vmemo[slot].clipcode = ofgl_vmemo[slot].viewcode = 0;
\telse
\t\tofgl_classify_cvert(&ofgl_vmemo[slot]);'''
new='''\tif (ofgl_batch_inside)
\t{
\t\tofgl_vmemo[slot].clipcode = ofgl_vmemo[slot].viewcode = 0;
\t\t/* These CPU triangles cannot be rejected by the coarse planes, so
\t\t * each fetched vertex will need projection. Keep its transform
\t\t * results in registers through the unchanged projection math.
\t\t * Geometry-capable batches retain their lazy projection path. */
\t\tif (!ofgl_indexed_geometry)
\t\t\tofgl_project_cvert_inline(&ofgl_vmemo[slot]);
\t}
\telse
\t\tofgl_classify_cvert(&ofgl_vmemo[slot]);'''
assert after.count(old)==1
after=after.replace(old,new,1)
C.mkdir(parents=True);shutil.copytree(B,W,ignore=shutil.ignore_patterns('.obj','build','__pycache__'))
(W/REL).write_text(after);(C/'of_gl1.c').write_text(after);(C/'original-projection.c').write_text(projection)
(C/'candidate.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+REL,tofile='b/'+REL)))
shutil.copy2(__file__,C/'recipe.py')
put(C/'prepared.json',dict(parent_sha256=PARENT,dependency='vertex-v1 cache-hit split; compare fused against vertex as well as deployed base',source_sha256=sha(W/REL),changed_files=[REL],worktree=str(W),scope='Only cache misses in wholly-inside CPU batches project eagerly; exact source expressions reused through inline helper; other paths remain lazy.'))
O=C/'checks';O.mkdir()
G=W/'src/quake2';includes=[G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include',G/'openfpga']
common=['cc','-std=gnu11','-g','-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"','-DOFGL_SOURCE="'+str(W/REL)+'"']+['-I'+str(p) for p in includes]
flags=common+['-O1','-fsanitize=address,undefined']
commands=[];env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
def run(cmd,name):
    commands.append(dict(argv=cmd,name=name))
    with (O/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
run(flags+[str(W/'tools/tests/quake2_renderer_test.c'),'-Wl,--gc-sections','-lm','-o',str(O/'renderer')],'renderer-build')
run([str(O/'renderer')],'renderer-run')
test=(W/'tools/tests/quake2_renderer_test.c').read_text().replace('int main(int argc, char **argv)','int unused_renderer_main(int argc, char **argv)')
test+=projection.replace('static void\nofgl_project_cvert(', 'static void __attribute__((noinline))\nreference_project(')+'''
static void __attribute__((noinline)) reference_position(int i, ofgl_cvert_t *out) {ofgl_fetch_position(i,out);}
static uint32_t rng=0x912bc501;
static uint32_t random32(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void) {
    float xyz[3][3]; ofgl_cvert_t expected={0};unsigned compared=0;
    ofgl_va_vertex.ptr=(const uint8_t *)xyz;ofgl_va_vertex.size=3;ofgl_va_vertex.stride=sizeof xyz[0];
    ofgl_MatrixMode(GL_MODELVIEW);ofgl_LoadIdentity();
    ofgl_pc_xs=160;ofgl_pc_ys=120;ofgl_pc_near=1;ofgl_pc_xc=159.5f;ofgl_pc_yc=119.5f;ofgl_build_clip_planes();
    for(unsigned n=0;n<100000;n++) {
        xyz[0][0]=((int)(random32()%1025)-512)*0.015625f;
        xyz[0][1]=((int)(random32()%1025)-512)*0.015625f;
        xyz[0][2]=-16.0f-(random32()%65536)*0.015625f;
        ofgl_depth_scale=n%3==0?0.3f:1.0f;
        for(unsigned cap=0;cap<2;cap++)for(unsigned inside=0;inside<2;inside++) {
            ofgl_vmemo_new_batch();ofgl_indexed_geometry=cap;ofgl_batch_inside=inside;
            memset(&expected,0,sizeof expected);memset(&ofgl_vmemo[0],0,sizeof ofgl_vmemo[0]);
            reference_position(0,&expected);
            if(inside)expected.clipcode=expected.viewcode=0;else ofgl_classify_cvert(&expected);
            ofgl_cvert_t *got=ofgl_cvert(0);
            if(inside&&!cap) {reference_project(&expected);assert(got->projected_valid==1);}
            else assert(got->projected_valid==0);
            assert(!memcmp(&expected,got,sizeof expected));
            /* Completing the lazy path must give the exact same fields. */
            reference_project(&expected);if(!got->projected_valid)ofgl_project_cvert(got);
            assert(!memcmp(&expected,got,sizeof expected));compared++;
        }
    }
    printf("PASS projected_vertices=%u source_math_exact=1 geometry_and_outside_remain_lazy=1\\n",compared);return 0;
}
'''
(O/'fusion.c').write_text(test)
for label,opts in [('sanitized',['-O1','-fsanitize=address,undefined']),('fastmath',['-O3','-ffast-math','-fsingle-precision-constant'])]:
    run(common+opts+[str(O/'fusion.c'),'-Wl,--gc-sections','-lm','-o',str(O/label)],label+'-build')
    run([str(O/label)],label+'-run')
put(O/'commands.json',commands)
put(C/'result.json',dict(passed=True,renderer_boundary_and_capability_tests=True,paired_vertices_per_mode=400000,modes=['ASan+UBSan O1','O3 fast-math single-precision-constant'],geometry_and_outside_remain_lazy=True,performance_measured=False,target_fma_equivalence_pending=True))
put(C/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in C.rglob('*') if p.is_file()}))
print((O/'sanitized-run.log').read_text().strip());print((O/'fastmath-run.log').read_text().strip());print('source',sha(W/REL),'manifest',sha(C/'manifest.json'))
