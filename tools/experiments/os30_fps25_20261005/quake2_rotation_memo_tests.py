#!/usr/bin/env python3
"""Actual-source host differential and target compile for the rotation memo."""
import hashlib,json,os,re,shlex,subprocess
from pathlib import Path
import quake2_rotation_memo as m
R,P,O,B=m.R,m.P,m.O,m.B
T=O/'tests-v2'
T.mkdir(exist_ok=False)
source=(O/'of_gl1.c').read_text()
assert source==m.apply_to_text(B.read_text())
prefix=r'''
#include <stdint.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <assert.h>
typedef float GLfloat;
typedef float ofgl_mat4[16];
static ofgl_mat4 current, last_rotation;
static unsigned dirty, postcalls, side;
static uint64_t trig[2][3], hits, misses, checks;
static __attribute__((noinline)) float test_cosf(float v) { trig[side][0]++; return cosf(v); }
static __attribute__((noinline)) float test_sinf(float v) { trig[side][1]++; return sinf(v); }
static __attribute__((noinline)) float test_sqrtf(float v) { trig[side][2]++; return sqrtf(v); }
'''
support='\n'.join(m.function(m.BASE,n) for n in ['ofgl_finite','ofgl_mat4_identity','ofgl_mat4_mul'])
spy=r'''
static void ofgl_mat_postmul(const ofgl_mat4 rotation) {
    memcpy(last_rotation,rotation,sizeof(last_rotation));
    ofgl_mat4_mul(current,current,rotation); dirty=1;postcalls++;
}
#define cosf test_cosf
#define sinf test_sinf
#define sqrtf test_sqrtf
'''
base=m.OLD.replace('static void','static __attribute__((noinline)) void',1).replace('ofgl_Rotatef','baseline_rotate')
cand=m.NEW.replace('static void','static __attribute__((noinline)) void',1).replace('ofgl_Rotatef','candidate_rotate')
cand=cand.replace('ofgl_mat_postmul(cache[i].matrix);','hits++; ofgl_mat_postmul(cache[i].matrix);')
cand=cand.replace('\tfloat rad =','\tmisses++;\n\tfloat rad =')
main=r'''
#undef cosf
#undef sinf
#undef sqrtf
static uint32_t rng=0x1978abcd;
static uint32_t next(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
static float bits(uint32_t b){float v;memcpy(&v,&b,4);return v;}
static void pair(float a,float x,float y,float z) {
    ofgl_mat4 input,out,rot;
    for(unsigned i=0;i<16;i++)input[i]=(float)((int)(next()%65535)-32767)/4096.0f;
    unsigned initial_dirty=next()&1;
    memcpy(current,input,sizeof(input));memset(last_rotation,0xa5,sizeof(last_rotation));
    dirty=initial_dirty;postcalls=0;side=0;baseline_rotate(a,x,y,z);
    memcpy(out,current,sizeof(out));memcpy(rot,last_rotation,sizeof(rot));
    unsigned expected_dirty=dirty,expected_calls=postcalls;
    memcpy(current,input,sizeof(input));memset(last_rotation,0xa5,sizeof(last_rotation));
    dirty=initial_dirty;postcalls=0;side=1;candidate_rotate(a,x,y,z);
    if(memcmp(current,out,sizeof(out))||memcmp(last_rotation,rot,sizeof(rot))||dirty!=expected_dirty||postcalls!=expected_calls){
        uint32_t k[4];memcpy(k,(float[4]){a,x,y,z},sizeof(k));
        fprintf(stderr,"mismatch case%llu args%08x/%08x/%08x/%08x calls%u/%u dirty%u/%u\n",(unsigned long long)checks,k[0],k[1],k[2],k[3],postcalls,expected_calls,dirty,expected_dirty);
        exit(2);
    }
    checks++;
}
int main(void){
    pair(0,1,0,0);pair(bits(0x80000000u),bits(0x7fc12345u),0,0);
    assert(!hits&&!misses&&!trig[0][0]&&!trig[1][0]);
    /* Four simultaneous entries, followed by eviction of the oldest. */
    for(unsigned i=0;i<4;i++)pair(111.0f+i,1,2,3);
    uint64_t h=hits;
    for(unsigned i=0;i<4;i++)pair(111.0f+i,1,2,3);
    assert(hits==h+4);
    pair(115,1,2,3);h=hits;pair(111,1,2,3);assert(hits==h);
    /* Input bit keys must not merge signed zeros or normalized axes. */
    pair(131,1,0,0);h=hits;pair(131,1,bits(0x80000000u),0);assert(hits==h);
    pair(132,1,0,0);h=hits;pair(132,2,0,0);assert(hits==h);
    pair(bits(0x43010000u),0,1,0);h=hits;pair(bits(0x43010001u),0,1,0);assert(hits==h);
    /* Nonfinite/overflow rad or len and degenerate axes stay uncached. */
    const uint32_t edge[]={0,0x80000000u,1,0x80000001u,0x00800000u,0x7f7fffffu,0xff7fffffu,0x7f800000u,0xff800000u,0x7fc12345u,0x7fa54321u};
    for(unsigned i=0;i<sizeof(edge)/sizeof(edge[0]);i++){
        float v=bits(edge[i]);pair(17,v,v,v);pair(17,v,v,v);
        pair(v,1,2,3);pair(v,1,2,3);
    }
    for(unsigned i=0;i<10;i++){
        h=hits;pair(bits(0x7fc12345u),1,2,3);pair(17,bits(0x7f800000u),0,0);
        pair(17,0,0,0);pair(17,1e-12f,0,0);assert(hits==h);
    }
    /* Typical world constants interspersed with changing model/view angles. */
    for(unsigned frame=0;frame<512;frame++){
        pair(-90,1,0,0);pair(90,0,0,1);pair((float)(frame%12),0,1,0);
        pair(45,0,0,1);pair(0,0,1,0);pair(0,1,0,0);
        pair(-90,1,0,0);pair(90,0,0,1);pair(45,0,0,1);
    }
    for(unsigned i=0;i<20000;i++){
        float a=(float)((int)(next()%72001)-36000)/100.0f;
        float x=(float)((int)(next()%2001)-1000)/100.0f;
        float y=(float)((int)(next()%2001)-1000)/100.0f;
        float z=(float)((int)(next()%2001)-1000)/100.0f;
        pair(a,x,y,z);if((i&3)==0)pair(a,x,y,z);
    }
    assert(hits>5000&&trig[0][0]>trig[1][0]);
    assert(trig[0][0]-trig[1][0]==hits);
    assert(trig[0][1]-trig[1][1]==hits&&trig[0][2]-trig[1][2]==hits);
    printf("{\"passed\":true,\"cases\":%llu,\"hits\":%llu,\"misses\":%llu,\"baseline_trig_each\":%llu,\"candidate_trig_each\":%llu,\"static_entry_bytes\":%zu}\n",
      (unsigned long long)checks,(unsigned long long)hits,(unsigned long long)misses,
      (unsigned long long)trig[0][0],(unsigned long long)trig[1][0],4*(4*sizeof(uint32_t)+sizeof(ofgl_mat4)+sizeof(int)));
}
'''
test=T/'rotation-diff.c';test.write_text(prefix+support+spy+base+'\n'+cand+main)
results=[]
configs=[('strict',['-O2','-ffp-contract=off']),
 ('target-fp-flags',['-O3','-flto=auto','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-falign-functions=64','-falign-loops=8','-fno-tree-loop-distribute-patterns']),
 ('sanitized',['-O2','-ffp-contract=off','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]
for name,flags in configs:
 cmd=['cc','-std=gnu11','-Wall','-Wextra','-fcommon','-fno-strict-aliasing','-fwrapv']+flags+[str(test),'-lm','-o',str(T/name)]
 (T/(name+'-command.json')).write_text(json.dumps(cmd,indent=2)+'\n')
 c=subprocess.run(cmd,capture_output=True,text=True);(T/(name+'-compile.log')).write_text(c.stdout+c.stderr);assert c.returncode==0
 c=subprocess.run([str(T/name)],capture_output=True,text=True,env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'));(T/(name+'-run.log')).write_text(c.stdout+c.stderr);assert c.returncode==0,(name,c.stderr)
 results.append(dict(name=name,**json.loads(c.stdout)))

# Compile the entire modified translation unit using the recorded real target
# command, changing only source/output locations and preserving local includes.
line=next(s for s in (P/'baseline-build.log').read_text().splitlines() if s.startswith('riscv64-unknown-elf-gcc ') and s.endswith(' openfpga/of_gl1.c'))
cmd=shlex.split(line);cmd[cmd.index('-o')+1]=str(T/'of_gl1.o');cmd[-1]=str(O/'of_gl1.c')
cmd += ['-iquote',str(B.parent)]
(T/'target-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
c=subprocess.run(cmd,cwd=P/'work/baseline/src/quake2',capture_output=True,text=True)
(T/'target-compile.log').write_text(c.stdout+c.stderr);assert c.returncode==0,c.stderr
(O/'test-result.json').write_text(json.dumps(dict(passed=True,host=results,target_translation_unit_compiles=True,
 target_command_from_recorded_baseline=True,local_header_resolution='-iquote original openfpga source directory',
 sanitizer_scope='ASan/UBSan enabled; LeakSanitizer disabled because process tracing blocks LSAN. Original tests/failed log preserved.',
 source_unchanged_during_tests=m.sha(O/'of_gl1.c')==json.loads((O/'prepared.json').read_text())['candidate_sha256'],
 limitations=['Host matrix byte/dirty-state equivalence is not native RISC-V runtime proof; parent owns matched whole-frame qsim before acceptance.',
 'Floating environment remains the renderer existing fixed mode; errno/trapping side effects are excluded by its actual -fno-math-errno/-fno-trapping-math flags.',
 'Synthetic hit count demonstrates replacement behavior, not the game hit rate or FPS improvement.']),indent=2)+'\n')
print(json.dumps(results))
