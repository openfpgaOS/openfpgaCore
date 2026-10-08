#!/usr/bin/env python3
"""Compare enabled/disabled report strings and quota against the old literal block."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-pass2-20261008'
C=P/'components/cold-v1'
W=P/'work/cold'
O=C/'report-checks-v2'
assert not O.exists()
O.mkdir()
test=(W/'tools/tests/quake2_renderer_test.c').read_text()
old='void Com_Printf(const char *fmt, ...) { (void)fmt; }'
new='''static char report_text[2048]; static int report_calls;
void Com_Printf(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); vsnprintf(report_text,sizeof report_text,fmt,ap); va_end(ap); report_calls++;
}'''
assert test.count(old)==1
test=test.replace(old,new).replace('int main(int argc, char **argv)','int unused_renderer_main(int argc, char **argv)')
assert 'int main(' not in test
test+='''
static void reference_report(const ofgl_cvert_t *v0, const ofgl_cvert_t *v1,
    const ofgl_cvert_t *v2, float tw, float th) {
    const ofgl_cvert_t *v[3]={v0,v1,v2}; float fx[3],fy[3]; int k;
    for(k=0;k<3;k++){fx[k]=v[k]->sx;fy[k]=v[k]->sy;}
'''+(C/'old-report.c').read_text()+'''\n}
static uint32_t rng=0x092567ab;
static uint32_t random32(void){rng^=rng<<13;rng^=rng>>17;rng^=rng<<5;return rng;}
int main(void) {
    cvar_t cv={0}; ofgl_cvert_t v[3]={{0}}; unsigned cases=0, emitted=0;
    char expected[2048];
    for(unsigned n=0;n<100000;n++) {
        for(unsigned i=0;i<3;i++) {
            v[i].sx=((int)(random32()%131073)-65536)*0.03125f;
            v[i].sy=((int)(random32()%131073)-65536)*0.03125f;
            v[i].z=(random32()%65536)*0.0625f;
        }
        ofgl_vp_w=1+random32()%1920; ofgl_vp_h=1+random32()%1080;
        if(n<4) {v[0].sx=v[0].sy=v[1].sy=0;v[1].sx=ofgl_vp_w;v[2].sx=0;v[2].sy=ofgl_vp_h+(n==1?-0.03125f:n==2?0.03125f:0);}
        ofgl_bisect_cv=(n%7)?&cv:NULL;cv.value=(n%5)?1:0;
        int quota=random32()%23;
        ofgl_lm_active=random32()%2;ofgl_tex2d_on[0]=random32()%2;
        ofgl_va_color.enabled=random32()%2;ofgl_va_color.ptr=(n%3)?(const uint8_t *)v:NULL;
        ofgl_blend_on=random32()%2;ofgl_depth_test=random32()%2;ofgl_depth_mask=random32()%2;
        float tw=1u<<(random32()%11),th=1u<<(random32()%11);
        report_calls=0;report_text[0]=0;ofgl_dbg_big=quota;
        reference_report(&v[0],&v[1],&v[2],tw,th);
        int calls=report_calls,end_quota=ofgl_dbg_big;strcpy(expected,report_text);
        emitted+=calls;report_calls=0;report_text[0]=0;ofgl_dbg_big=quota;
        if (ofgl_bisect_cv && ofgl_bisect_cv->value && ofgl_dbg_big < 20)
            ofgl_report_big_tri(&v[0],&v[1],&v[2],tw,th);
        assert(calls==report_calls && end_quota==ofgl_dbg_big && !strcmp(expected,report_text));cases++;
    }
    assert(emitted>1000);printf("PASS report_cases=%u matching_report_lines=%u\\n",cases,emitted);return 0;
}
'''
(O/'report.c').write_text(test)
G=W/'src/quake2'
includes=[G,G/'src/common/header',G/'src/client/refresh/gl1/glad-gles1/include',W/'src/sdk/include',G/'openfpga']
flags=['cc','-std=gnu11','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-fno-pie','-no-pie','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-ffunction-sections','-fdata-sections','-DYQ2OSTYPE="test"','-DYQ2ARCH="host"','-DOFGL_SOURCE="'+str(W/'src/quake2/openfpga/of_gl1.c')+'"']+['-I'+str(p) for p in includes]
commands=[flags+[str(O/'report.c'),'-Wl,--gc-sections','-lm','-o',str(O/'report')],[str(O/'report')]]
env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
for name,cmd in zip(['build','run'],commands):
    with (O/(name+'.log')).open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env,check=True)
(O/'commands.json').write_text(json.dumps(commands,indent=2)+'\n')
(O/'result.json').write_text(json.dumps(dict(passed=True,report_cases=100000,compared=['exact report text','call count','quota counter'],sanitizers=['ASan','UBSan'],limits='Reporter directly exercised; existing renderer suite separately covers original emitter path.'),indent=2)+'\n')
(O/'manifest.json').write_text(json.dumps(dict(passed=True,evidence_sha256={str(p.relative_to(R)):hashlib.sha256(p.read_bytes()).hexdigest() for p in list(O.iterdir())+[Path(__file__),C/'old-report.c',W/'src/quake2/openfpga/of_gl1.c'] if p.is_file()}),indent=2)+'\n')
print((O/'run.log').read_text().strip())
