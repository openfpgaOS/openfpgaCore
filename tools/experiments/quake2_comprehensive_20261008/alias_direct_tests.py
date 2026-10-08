#!/usr/bin/env python3
"""Exercise the actual direct-view owner and preserve the source identity."""
from pathlib import Path
import hashlib,json,os,subprocess

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/quake2-comprehensive-20261008'
W=P/'work/alias';OUT=P/'components/alias-direct-v1';GAME=W/'src/quake2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    source=Path(__file__).with_name('alias_buffer_test.c')
    old=OUT/'buffer-build.json'
    if old.exists():
        n=1
        while (OUT/f'buffer-build-attempt{n}.json').exists():n+=1
        for ext in ['json','log']:(OUT/f'buffer-build.{ext}').rename(OUT/f'buffer-build-attempt{n}.{ext}')
    flags=['cc','-std=gnu11','-O1','-g','-DOF_PC','-DYQ2_OPENFPGA=1','-DYQ2_GL1_GLES=1',
        '-DYQ2OSTYPE="test"','-DYQ2ARCH="host"','-ffunction-sections','-fdata-sections',
        '-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(GAME),
        '-I'+str(GAME/'src/common/header'),'-I'+str(GAME/'src/client/refresh/gl1/glad-gles1/include'),
        '-I'+str(W/'src/sdk/include')]
    cmd=flags+[str(source),str(GAME/'src/client/refresh/gl1/glad-gles1/src/glad.c'),
        '-Wl,--gc-sections','-lm','-o',str(OUT/'alias-buffer-test')]
    with (OUT/'buffer-build.log').open('w') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
    (OUT/'buffer-build.json').write_text(json.dumps(dict(command=cmd,returncode=r.returncode),indent=2)+'\n')
    assert r.returncode==0
    run=subprocess.run([str(OUT/'alias-buffer-test')],capture_output=True,text=True,
        env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0',UBSAN_OPTIONS='halt_on_error=1'))
    (OUT/'buffer-run.log').write_text(run.stdout+run.stderr);assert run.returncode==0,run.stderr
    m=json.loads((OUT/'manifest.json').read_text())
    for rel,pin in m['files'].items():assert sha(W/rel)==pin['after'],rel
    assert json.loads((OUT/'renderer-result.json').read_text())['returncode']==0
    (OUT/'tests.json').write_text(json.dumps(dict(passed=True,
        renderer_suite_count=8,sanitizers=['ASan','UBSan'],
        alias_mesh_differential='65,540 varying draws compare direct/cached against original fan/strip staging; includes UV seam and color-generation wrap.',
        buffer_output=run.stdout,buffer_test_sha256=sha(source),
        buffer_executable_sha256=sha(OUT/'alias-buffer-test'),manifest_sha256=sha(OUT/'manifest.json'),
        product_sources_rehashed_unchanged=True),indent=2)+'\n')
    print((OUT/'tests.json').read_text())

if __name__=='__main__':main()
