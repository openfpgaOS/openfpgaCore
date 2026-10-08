#!/usr/bin/env python3
from pathlib import Path
import argparse,hashlib,json,os,subprocess,time
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-batch-memory-20261008';W=P/'work/alias_positions';O=P/'components/alias-positions-v1';G=W/'src/quake2';SDK=W/'src/sdk'
QSIM=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2/qsim/qsim'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def run(cmd,name):
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1');t=time.monotonic()
    with (O/(name+'.log')).open('w') as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,env=env)
    (O/(name+'.json')).write_text(json.dumps(dict(command=cmd,returncode=r.returncode,seconds=time.monotonic()-t),indent=2)+'\n');assert not r.returncode,(name,(O/(name+'.log')).read_text()[-2500:])
def prepare():
    source=W/'tools/tests/quake2_renderer_test.c';fixture=source.read_text().replace('#define OFGL_SOURCE "../../src/quake2/openfpga/of_gl1.c"','#define OFGL_SOURCE "openfpga/of_gl1.c"')
    (O/'renderer_fixture.c').write_text(fixture);(O/'render_test.c').write_text(Path(__file__).with_name('alias_positions_render_test.c').read_text())
    b=(R/'tools/experiments/quake2_comprehensive_20261008/alias_buffer_test.c').read_text()
    b=b.replace('static unsigned draws,world_draws,pushes,pops,perspectives;','static unsigned draws,world_draws,pushes,pops,perspectives,position_draws;\nstatic int reuse_enabled=1;\nstatic float source_positions[2][4];')
    at='void R_EnableMultitexture(qboolean e)'
    add='''int OFGL_AliasPositionsEnabled(void) { return reuse_enabled; }
void OFGL_DrawAliasElements(const int *map,int stride,const float *positions,const unsigned short *indices,int count) {
    assert(alias_direct&&map==&alias_direct->vertices[0].xyz&&stride==sizeof(alias_direct->vertices[0]));
    assert(positions==source_positions[0]);
    for(int i=0;i<count;i++){int xyz=*(const int*)((const byte*)map+indices[i]*stride);assert(xyz<2);volatile float live=positions[4*xyz]+positions[4*xyz+1]+positions[4*xyz+2];(void)live;}
    position_draws++;draw(GL_TRIANGLES,count,GL_UNSIGNED_SHORT,indices);
}
'''
    b=b.replace(at,add+at).replace('assert(R_BufferAliasDirect(m));','assert(R_BufferAliasDirect(m));assert(R_BufferAliasPositions(source_positions[0]));')
    b=b.replace('assert(!alias_direct&&!world_count&&!idx_ptr&&!vtx_ptr);','assert(!alias_direct&&!alias_positions&&!world_count&&!idx_ptr&&!vtx_ptr);')
    b=b.replace('set_alias(0);assert(!R_BufferAliasDirect(NULL));','set_alias(0);assert(!R_BufferAliasPositions(source_positions[0]));assert(!R_BufferAliasDirect(NULL));')
    b=b.replace('puts("PASS: direct alias GL pointers,','assert(position_draws>5);\n    puts("PASS: direct XYZ lifetime/fallback plus direct alias GL pointers,')
    (O/'buffer_test.c').write_text(b)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['host','target']);a=ap.parse_args();prepare()
    defs=['-std=gnu11','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-DYQ2OSTYPE="test"','-DYQ2ARCH="test"','-ffunction-sections','-fdata-sections','-I'+str(G),'-I'+str(G/'src/common/header'),'-I'+str(G/'src/client/refresh/gl1/glad-gles1/include'),'-I'+str(SDK/'include')]
    if a.action=='host':
        for name,flags in [('host',['-O3','-ffast-math']),('host-sanitize',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
            run(['cc',*defs,*flags,str(O/'render_test.c'),'-Wl,--gc-sections','-lm','-o',str(O/name)],name+'-build');run([str(O/name)],name+'-run')
        run(['cc',*defs,'-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(O/'buffer_test.c'),str(G/'src/client/refresh/gl1/glad-gles1/src/glad.c'),'-Wl,--gc-sections','-lm','-o',str(O/'buffer-test')],'buffer-build');run([str(O/'buffer-test')],'buffer-run')
        run(['python3',str(W/'tools/check_quake2_renderer.py')],'renderer')
    else:
        compiler=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}','-v',f'{R}:{R}','-w',str(O),'-e','CPATH=','--entrypoint','riscv64-unknown-elf-gcc','openfpgaos-firmware']
        flags=['-march=rv32imafc_zicsr','-mabi=ilp32f','-O3','-flto=auto','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-fno-tree-loop-distribute-patterns','-falign-functions=64','-falign-loops=8','-nostdinc','-isystem',str(SDK/'musl/include'),'-nostdlib','-static','-T',str(SDK/'app.ld'),'-L'+str(SDK/'musl/lib'),'-Wl,--gc-sections','-Wl,--no-warn-rwx-segments']
        run([*compiler,*defs,*flags,'-o',str(O/'alias-target.elf'),*[str(SDK/'musl/lib'/p) for p in ['crt1.o','crti.o','crtn.o']],str(O/'render_test.c'),'-Wl,--start-group','-lc','-lm','-lgcc','-Wl,--end-group'],'target-build')
        run([str(QSIM),str(O/'alias-target.elf'),'--out',str(O/'target-run'),'--max-insns','3e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp'],'target-run')
        assert json.loads((O/'target-run/summary.json').read_text())['stop_reason']=='guest exit(0)'
        console=(O/'target-run/console.log').read_text();assert 'errors=0' in console
        result=dict(passed=True,console=console,source_sha256=sha(O/'render_test.c'),fixture_sha256=sha(O/'renderer_fixture.c'),target_elf_sha256=sha(O/'alias-target.elf'),manifest_sha256=sha(O/'manifest.json'),rounding_modes=['RNE','RTZ','RDN','RUP','RMM'])
        (O/'tests.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
