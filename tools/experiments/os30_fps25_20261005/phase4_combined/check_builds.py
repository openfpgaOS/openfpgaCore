#!/usr/bin/env python3
"""Verify combined leaf composition and both closed private builds."""
import hashlib,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase4/combined'
STAGE=OUT/'SM64';CANDIDATE=OUT/'aspect-texmem-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    manifest=read(CANDIDATE/'manifest.json');expected=read(OUT/'source-inputs.json')['files']
    for name,h in manifest['source_sha256'].items():assert sha(CANDIDATE/name)==h,name
    for name,source in manifest['source_components'].items():
        p=Path(source['path']);assert sha(p)==source['sha256']
        if name=='perf_pocket.c':assert (CANDIDATE/name).read_text()==p.read_text().replace('PERF F5 FPS','PERF F6 FPS')
        else:assert (CANDIDATE/name).read_bytes()==p.read_bytes(),name
    assert len(expected)==6747
    for rel,h in expected.items():assert sha(STAGE/rel)==h,rel
    actual={str(p.relative_to(STAGE))for p in STAGE.rglob('*')if p.is_file() and not str(p.relative_to(STAGE)).startswith('.obj/')}
    assert actual==set(expected)
    dependencies=set();bindings={};evidence={}
    for mode,folder,elfname in [('normal','sm64','app.elf'),('diagnostic','sm64-perfdiag','diagnostic.elf')]:
        path=CANDIDATE/('build-'+mode+'.json');build=read(path)
        assert build['returncode']==0 and build['diagnostic']==(mode=='diagnostic')
        assert build['cwd']==str(STAGE/'src/sm64')
        for name,info in build['inputs'].items():assert info['sha256']==manifest['source_sha256'][name] and info['source']==str(CANDIDATE/name)
        elf=CANDIDATE/elfname;assert sha(elf)==build['elf_sha256']==sha(STAGE/'.obj'/folder/'app.elf')
        log=CANDIDATE/('build-'+mode+'.log')
        for line in log.read_text().splitlines():
            if not line.startswith('riscv64-unknown-elf-gcc ') or ' -c ' not in line:continue
            args=shlex.split(line);assert '-march=rv32imafc' in args and '-mabi=ilp32f' in args and '-nostdinc' in args
            assert [x for x in args if x.startswith('-mtune=')]==['-mtune=sifive-5-series']
        dfiles=list((STAGE/'.obj'/folder).rglob('*.d'));assert len(dfiles)==298
        for p in dfiles:
            assert p.with_suffix('.o').is_file()
            first=p.read_text().replace('\\\n',' ').splitlines()[0]
            for token in shlex.split(first.split(':',1)[1]):
                d=Path(token)
                if not d.is_absolute():d=STAGE/'src/sm64'/d
                d=d.resolve();assert d.is_relative_to(STAGE)
                rel=str(d.relative_to(STAGE));assert rel in expected;dependencies.add(rel)
        nm=subprocess.check_output(['llvm-nm','-S',str(elf)],text=True)
        vertex=re.search(r'^([0-9a-f]+) ([0-9a-f]+) t gfx_sp_vertex$',nm,re.M);assert vertex
        bindings[mode]={'elf_sha256':sha(elf),'record_sha256':sha(path),'log_sha256':sha(log),'dependency_files':len(dfiles),'vertex_function_address':vertex[1],'vertex_function_size':int(vertex[2],16)}
        for p in [path,log,elf]:evidence[str(p.relative_to(ROOT))]=sha(p)
    sdk=[p for p in expected if '/sdk/' in p or p.startswith('src/sdk/')];assert len(sdk)==333
    baseline=read(OUT.parent/'division/source-baseline.json')['files']
    assert all(expected[p]==baseline[p]for p in sdk)
    changed=[p for p in expected if expected[p]!=baseline[p]]
    assert set(changed)=={'src/sm64/sm64/src/pc/gfx/gfx_pc.c','src/sm64/sm64/src/pc/gfx/gfx_gpu.c','src/sm64/pocket/perf_pocket.c'}
    for p in [Path(__file__),CANDIDATE/'manifest.json',OUT/'source-inputs.json']:evidence[str(p.relative_to(ROOT))]=sha(p)
    result={'source_and_build_closure_passed':True,'candidate':'aspect-texmem-v1','parent':'F5 exact-v1','source_inputs':len(expected),'changed_inputs':changed,'sdk_inputs_unchanged':len(sdk),'compiler_dependency_input_count':len(dependencies),'all_compiler_dependencies_in_closed_tree':True,'composition_exact':True,'build_bindings':bindings,'evidence_sha256':evidence,'runtime_qualified':False,'scope':'Normal/FPS build and leaf source closure only. Root independently owns CRAM-aware captures, full color/depth replay, timing and qualification. No publishing/deployment.'}
    (OUT/'source-and-build-closure.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
