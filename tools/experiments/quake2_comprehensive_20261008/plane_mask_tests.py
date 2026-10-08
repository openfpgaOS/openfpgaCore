#!/usr/bin/env python3
from pathlib import Path
import argparse,hashlib,json,os,subprocess,time
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/quake2-comprehensive-20261008'
W=P/'work/plane_mask';OUT=P/'components/plane-mask-v1';GAME=W/'src/quake2';SDK=W/'src/sdk'
QSIM=ROOT/'docs/measurements/quake2-optimize-20261008/demo-screen-v2/qsim/qsim'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def extract(s,name):
    pos=s.index('\n'+name+'(');start=s.rfind('\n',0,pos-1)+1
    brace=s.index('{',pos);level=1;i=brace+1
    while level:
        level+=(s[i]=='{')-(s[i]=='}');i+=1
    return s[start:i]+'\n'
def run(cmd,name):
    env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1')
    start=time.monotonic()
    with (OUT/(name+'.log')).open('w') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,env=env)
    (OUT/(name+'.json')).write_text(json.dumps(dict(command=cmd,returncode=r.returncode,seconds=time.monotonic()-start),indent=2)+'\n')
    assert r.returncode==0,(name,(OUT/(name+'.log')).read_text()[-3000:])
def prepare():
    rel='src/quake2/src/client/refresh/gl1/gl1_surf.c'
    old=(P/'work/base'/rel).read_text();new=(W/rel).read_text()
    shared=(GAME/'src/client/refresh/files/surf.c').read_text()
    funcs=extract(new,'R_WorldPlaneMask')+extract(old,'R_RecursiveWorldNode').replace('R_RecursiveWorldNode','original_traversal')+extract(new,'R_RecursiveWorldNode').replace('R_RecursiveWorldNode','candidate_traversal')
    area=extract(shared,'R_AreaVisible').replace('R_AreaVisible','area_visible_impl')
    funcs=area+'\nqboolean R_AreaVisible(const byte *b,mleaf_t *l) {emit(3000+(int)(l-leaves));return area_visible_impl(b,l);}\n'+extract(shared,'R_CullBox')+extract(shared,'R_TextureAnimation')+funcs
    (OUT/'plane_functions.h').write_text(funcs)
    (OUT/'plane_test.c').write_text(Path(__file__).with_name('plane_mask_test.c').read_text())
def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['host','target']);a=ap.parse_args();prepare()
    defs=['-std=gnu11','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1','-DYQ2OSTYPE="test"','-DYQ2ARCH="test"','-ffunction-sections','-fdata-sections','-I'+str(GAME),'-I'+str(GAME/'src/common/header'),'-I'+str(GAME/'src/client/refresh/gl1/glad-gles1/include'),'-I'+str(SDK/'include')]
    src=[str(OUT/'plane_test.c'),str(GAME/'src/client/refresh/files/models.c'),str(GAME/'src/common/shared/shared.c')]
    if a.action=='host':
        for name,flags in [('host',['-O3','-ffast-math']),('host-sanitize',['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer'])]:
            run(['cc',*defs,*flags,*src,'-Wl,--gc-sections','-lm','-o',str(OUT/name)],name+'-build');run([str(OUT/name)],name+'-run')
    else:
        compiler=['docker','run','--rm','--network','none','--user',f'{os.getuid()}:{os.getgid()}','-v',f'{ROOT}:{ROOT}','-w',str(OUT),'-e','CPATH=','--entrypoint','riscv64-unknown-elf-gcc','openfpgaos-firmware']
        flags=['-march=rv32imafc_zicsr','-mabi=ilp32f','-O3','-flto=auto','-ffast-math','-fno-math-errno','-fno-trapping-math','-fsingle-precision-constant','-fno-tree-loop-distribute-patterns','-falign-functions=64','-falign-loops=8','-nostdinc','-isystem',str(SDK/'musl/include'),'-nostdlib','-static','-T',str(SDK/'app.ld'),'-L'+str(SDK/'musl/lib'),'-Wl,--gc-sections','-Wl,--no-warn-rwx-segments']
        run([*compiler,*defs,*flags,'-o',str(OUT/'plane-target.elf'),*[str(SDK/'musl/lib'/p) for p in ['crt1.o','crti.o','crtn.o']],*src,'-Wl,--start-group','-lc','-lm','-lgcc','-Wl,--end-group'],'target-build')
        run([str(QSIM),str(OUT/'plane-target.elf'),'--out',str(OUT/'target-run'),'--max-insns','3e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp'],'target-run')
        assert json.loads((OUT/'target-run/summary.json').read_text())['stop_reason']=='guest exit(0)'
        console=(OUT/'target-run/console.log').read_text();assert 'errors=0' in console
        result=dict(passed=True,console=console,unit_source_sha256=sha(OUT/'plane_test.c'),extracted_functions_sha256=sha(OUT/'plane_functions.h'),target_elf_sha256=sha(OUT/'plane-target.elf'),qsim_sha256=sha(QSIM),manifest_sha256=sha(OUT/'manifest.json'),rounding_modes=['RNE','RTZ','RDN','RUP','RMM'])
        (OUT/'tests.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
