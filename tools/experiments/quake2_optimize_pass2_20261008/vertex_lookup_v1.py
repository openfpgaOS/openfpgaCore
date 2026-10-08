#!/usr/bin/env python3
"""Private screen: inline the unchanged cache-hit probe; keep misses out of line."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-pass2-20261008'
B=P/'work/base'; W=P/'work/vertex'; C=P/'components/vertex-v1'
REL='src/quake2/openfpga/of_gl1.c'
PARENT='a920bb512a578029265ab2f756dd273faac6e641fa2a4ff75cf9a266e8e843fb'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2,sort_keys=True)+'\n')

assert sha(B/REL)==PARENT and not C.exists() and not W.exists()
before=(B/REL).read_text()
start=before.index('static ofgl_cvert_t *\nofgl_cvert(int i)')
end=before.index('\n/* Sutherland-Hodgman',start)
old=before[start:end]
fetch=old[old.index('\tofgl_fetch_position'):]
assert fetch.endswith('}\n')
new='''/* Keep the three common cache probes in the caller, while the transform
 * and clipping work stays shared on the miss path. */
static ofgl_cvert_t * __attribute__((noinline))
ofgl_cvert_miss(int i, int slot)
{
'''+fetch+'''
static inline __attribute__((always_inline)) ofgl_cvert_t *
ofgl_cvert(int i)
{
\tint slot = i & (OFGL_VMEMO_N - 1);
\tif (ofgl_vmemo_tag[slot] == ofgl_vmemo_cur && ofgl_vmemo_idx[slot] == i)
\t{
\t\treturn &ofgl_vmemo[slot];
\t}
\treturn ofgl_cvert_miss(i, slot);
}
'''
after=before[:start]+new+before[end:]
C.mkdir(parents=True);shutil.copytree(B,W)
(W/REL).write_text(after);(C/'of_gl1.c').write_text(after)
(C/'old-function.c').write_text(old);(C/'new-functions.c').write_text(new)
(C/'candidate.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+REL,tofile='b/'+REL)))
shutil.copy2(__file__,C/'recipe.py')
put(C/'prepared.json',dict(parent_sha256=PARENT,source_sha256=sha(W/REL),changed_files=[REL],worktree=str(W),scope='Inline full-index/generation cache probe, noinline unchanged transform/classify miss; direct map size 256 and collision handling unchanged.'))
O=C/'checks';O.mkdir()
G=W/'src/quake2'
includes=[G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include',G/'openfpga']
flags=['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"','-DOFGL_SOURCE="'+str(W/REL)+'"']+['-I'+str(p) for p in includes]
commands=[];env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
def run(cmd,name):
    commands.append(dict(argv=cmd,name=name))
    with (O/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
run(flags+[str(W/'tools/tests/quake2_renderer_test.c'),'-Wl,--gc-sections','-lm','-o',str(O/'renderer')],'renderer-build')
run([str(O/'renderer')],'renderer-run')
test=(W/'tools/tests/quake2_renderer_test.c').read_text().replace('int main(int argc, char **argv)','int unused_renderer_main(int argc, char **argv)')
ref=old.replace('ofgl_cvert(int i)','reference_cvert(int i)').replace('ofgl_vmemo','ref_vmemo')
test+='''
static ofgl_cvert_t ref_vmemo[OFGL_VMEMO_N];
static int32_t ref_vmemo_idx[OFGL_VMEMO_N];
static uint8_t ref_vmemo_tag[OFGL_VMEMO_N],ref_vmemo_cur;
'''+ref+'''
static uint32_t rng=0x283710ab;
static uint32_t random32(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void) {
    const unsigned count=65537;float (*xyz)[3]=calloc(count,sizeof *xyz);assert(xyz);
    ofgl_va_vertex.ptr=(const uint8_t *)xyz;ofgl_va_vertex.size=3;ofgl_va_vertex.stride=sizeof *xyz;
    ofgl_MatrixMode(GL_MODELVIEW);ofgl_LoadIdentity();
    ofgl_pc_xs=160;ofgl_pc_ys=120;ofgl_pc_near=1;ofgl_pc_xc=159.5f;ofgl_pc_yc=119.5f;ofgl_build_clip_planes();
    for(unsigned i=0;i<count;i++){xyz[i][0]=(int)(i%371)-150;xyz[i][1]=(int)(i%257)-100;xyz[i][2]=-8-(i%512);}
    unsigned cases=0;
    for(unsigned batch=0;batch<769;batch++) {
        ofgl_vmemo_new_batch();if(++ref_vmemo_cur==0){memset(ref_vmemo_tag,0,sizeof ref_vmemo_tag);ref_vmemo_cur=1;}
        ofgl_batch_inside=batch&1;
        for(unsigned n=0;n<1024;n++) {
            int i=n%8==0?65536:n%8==1?0:n%8==2?256:n%8==3?512:n%8==4?batch%256:(int)(random32()%count);
            ofgl_cvert_t *a=reference_cvert(i),*b=ofgl_cvert(i);assert(!memcmp(a,b,sizeof *a));
            /* A hit must preserve deferred values; a miss invalidates only
             * their flags exactly as the original did. */
            a->attrs_valid=b->attrs_valid=1;a->projected_valid=b->projected_valid=1;
            a->s=b->s=(float)n;a->sx=b->sx=(float)batch;
            xyz[i][0]+=0.125f;cases++;
        }
        assert(!memcmp(ref_vmemo,ofgl_vmemo,sizeof ref_vmemo));
        assert(!memcmp(ref_vmemo_idx,ofgl_vmemo_idx,sizeof ref_vmemo_idx));
        assert(!memcmp(ref_vmemo_tag,ofgl_vmemo_tag,sizeof ref_vmemo_tag));
        assert(ref_vmemo_cur==ofgl_vmemo_cur);
    }
    free(xyz);printf("PASS cache_lookups=%u batches=769 full_index_65536=1 generation_wraps=3\\n",cases);return 0;
}
'''
(O/'cache.c').write_text(test)
run(flags+[str(O/'cache.c'),'-Wl,--gc-sections','-lm','-o',str(O/'cache')],'cache-build')
run([str(O/'cache')],'cache-run')
put(O/'commands.json',commands)
put(C/'result.json',dict(passed=True,renderer_collision_tests=True,paired_cache_lookups=769*1024,batches=769,generation_wraps=3,full_index_65536=True,sanitizers=['ASan','UBSan'],performance_measured=False))
put(C/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in C.rglob('*') if p.is_file()}))
print((O/'cache-run.log').read_text().strip());print('source',sha(W/REL),'manifest',sha(C/'manifest.json'))
