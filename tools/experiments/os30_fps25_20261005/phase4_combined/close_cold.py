#!/usr/bin/env python3
"""Close both builds and CPU captures of the sole cold-helper candidate."""
import csv,hashlib,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
PHASE=CAMP/'phase4';OUT=PHASE/'combined-cold';STAGE=OUT/'SM64'
CAND=OUT/'aspect-texmem-cold-v1';PARENT=PHASE/'combined/aspect-texmem-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    manifest=read(CAND/'manifest.json');expected=read(OUT/'source-inputs.json')['files']
    for name,h in manifest['source_sha256'].items():
        assert sha(CAND/name)==h
        assert sha(PARENT/name)==manifest['parent_source_sha256'][name]
        text=(PARENT/name).read_text()
        if name=='gfx_gpu.c':
            for typ,fn in [('void','gpu_fast_tex_fallback'),('int','gpu_fast_tex_upload'),('void','gpu_fast_tex_init')]:
                old='static '+typ+' '+fn+'(';assert text.count(old)==1
                text=text.replace(old,'static __attribute__((cold, noinline)) '+typ+' '+fn+'(')
        assert text==(CAND/name).read_text(),name
    assert len(expected)==6747
    for rel,h in expected.items():assert sha(STAGE/rel)==h,rel
    actual={str(p.relative_to(STAGE))for p in STAGE.rglob('*')if p.is_file() and not str(p.relative_to(STAGE)).startswith('.obj/')}
    assert actual==set(expected)
    deps=set();bindings={};evidence={}
    for mode,folder,elfname in [('normal','sm64','app.elf'),('diagnostic','sm64-perfdiag','diagnostic.elf')]:
        path=CAND/('build-'+mode+'.json');build=read(path);assert build['returncode']==0 and build['diagnostic']==(mode=='diagnostic')
        assert build['cwd']==str(STAGE/'src/sm64')
        for name,info in build['inputs'].items():assert info['sha256']==manifest['source_sha256'][name] and info['source']==str(CAND/name)
        elf=CAND/elfname;assert sha(elf)==build['elf_sha256']==sha(STAGE/'.obj'/folder/'app.elf')
        log=CAND/('build-'+mode+'.log')
        for line in log.read_text().splitlines():
            if not line.startswith('riscv64-unknown-elf-gcc ') or ' -c ' not in line:continue
            args=shlex.split(line);assert '-march=rv32imafc' in args and '-mabi=ilp32f' in args and '-nostdinc' in args
            assert [x for x in args if x.startswith('-mtune=')]==['-mtune=sifive-5-series']
        files=list((STAGE/'.obj'/folder).rglob('*.d'));assert len(files)==298
        for p in files:
            assert p.with_suffix('.o').is_file()
            first=p.read_text().replace('\\\n',' ').splitlines()[0]
            for token in shlex.split(first.split(':',1)[1]):
                q=Path(token)
                if not q.is_absolute():q=STAGE/'src/sm64'/q
                q=q.resolve();assert q.is_relative_to(STAGE);rel=str(q.relative_to(STAGE));assert rel in expected;deps.add(rel)
        bindings[mode]={'elf_sha256':sha(elf),'record_sha256':sha(path),'log_sha256':sha(log),'dependency_files':len(files)}
        for p in [path,log,elf]:evidence[str(p.relative_to(ROOT))]=sha(p)
    baseline=read(PHASE/'division/source-baseline.json')['files']
    sdk=[p for p in expected if '/sdk/' in p or p.startswith('src/sdk/')];assert len(sdk)==333 and all(expected[p]==baseline[p]for p in sdk)
    changed=[p for p in expected if expected[p]!=baseline[p]]
    assert set(changed)=={'src/sm64/sm64/src/pc/gfx/gfx_pc.c','src/sm64/sm64/src/pc/gfx/gfx_gpu.c','src/sm64/pocket/perf_pocket.c'}
    for p in [Path(__file__),CAND/'manifest.json',OUT/'source-inputs.json']:evidence[str(p.relative_to(ROOT))]=sha(p)
    closure={'source_and_build_closure_passed':True,'candidate':'aspect-texmem-cold-v1','parent':'combined aspect-texmem-v1','source_inputs':len(expected),'changed_inputs_vs_F5':changed,'sdk_inputs_unchanged':len(sdk),'compiler_dependency_input_count':len(deps),'all_compiler_dependencies_in_closed_tree':True,'composition_exact':True,'build_bindings':bindings,'evidence_sha256':evidence,'runtime_qualified':False,'scope':'Both closed F6 builds and exact three-attribute composition only; GPU replay and device qualification owned by root.'}
    (OUT/'source-and-build-closure.json').write_text(json.dumps(closure,indent=2)+'\n')
    for mode in ['normal','fps']:
        paths={'F5':CAMP/'validation'/('capture-phase2-exact-v1-'+mode),'aspect':PHASE/'division'/('capture-aspect-'+mode),'combined':PHASE/'texmem-capture-v2'/('capture-combined-'+mode),'cold':PHASE/'texmem-capture-v2'/('capture-combined-cold-'+mode)}
        capinputs={k:read(p/'inputs.json')for k,p in paths.items()}
        for k,m in capinputs.items():
            assert m['success'] and m['audio'] and not m['geometry_capability'] and not m['panel'] and not m['command_dma']
            for rel,h in m['results_sha256'].items():assert sha(paths[k]/rel)==h,(k,rel)
        for n in ['scene.csv','geometry.csv','culling.csv']:assert len({sha(p/n)for p in paths.values()})==1,n
        if mode=='fps':assert sha(paths['cold']/'sm64.elf')==bindings['diagnostic']['elf_sha256']
        else:assert sha(paths['cold']/'sm64.elf')==bindings['normal']['elf_sha256']
        rows={k:list(csv.DictReader((p/'frames.csv').open()))[360:]for k,p in paths.items()};assert all(len(v)==361 for v in rows.values())
        metrics={}
        for key in ['insns','est_cycles','icache_miss_sdram','dcache_miss','mispredict','gpu_cmd_words']:
            vals={k:sum(int(r[key])for r in v)for k,v in rows.items()}
            metrics[key]={'values':vals,'cold_saving_vs_F5_percent':100*(1-vals['cold']/vals['F5']),'cold_saving_vs_combined_percent':100*(1-vals['cold']/vals['combined']),'cold_delta_vs_aspect':vals['cold']-vals['aspect']}
        profiles={r['function']:r for r in csv.DictReader((paths['cold']/'profile.tsv').open(),delimiter='\t')}
        upload=profiles['gpu_fast_tex_upload'];assert int(upload['self_insns'])>10*int(upload['calls'])
        record={'mode':mode,'candidate':'aspect-texmem-cold-v1','scene_geometry_culling_exact':True,'metrics_frames360_through720':metrics,'capture_inputs_sha256':{k:sha(p/'inputs.json')for k,p in paths.items()},'active_upload_helper_profile':upload,'source_and_build_closure_sha256':sha(OUT/'source-and-build-closure.json'),'script_sha256':sha(Path(__file__)),'scope':'Matched CPU-model gate only. qsim upload busy is zero; real controller contention is separate. No hardware FPS claim.'}
        (OUT/('cpu-'+mode+'.json')).write_text(json.dumps(record,indent=2)+'\n');print(mode,json.dumps(metrics))
if __name__=='__main__':main()
