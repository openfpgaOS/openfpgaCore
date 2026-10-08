#!/usr/bin/env python3
"""Close the one cold-helper CPU-model gate, without selecting/deploying it."""
import csv,hashlib,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4]
PHASE=ROOT/'docs/measurements/os30-fps25-20261005/phase4'
OUT=PHASE/'combined-cold';CAND=OUT/'aspect-texmem-cold-v1'
CAP=PHASE/'texmem-capture-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())

def main():
    sources=read(OUT/'source-inputs.json')['files'];assert len(sources)==6747
    for rel,h in sources.items():assert sha(OUT/'SM64'/rel)==h,rel
    depfiles=list((OUT/'SM64/.obj/sm64-perfdiag').rglob('*.d'));assert len(depfiles)==298
    deps=set()
    for p in depfiles:
        first=p.read_text().replace('\\\n',' ').splitlines()[0]
        for token in shlex.split(first.split(':',1)[1]):
            q=Path(token)
            if not q.is_absolute():q=OUT/'SM64/src/sm64'/q
            q=q.resolve();assert q.is_relative_to(OUT/'SM64');rel=str(q.relative_to(OUT/'SM64'));assert rel in sources;deps.add(rel)
    paths={'F5':CAP/'capture-f5-control','combined':CAP/'capture-combined-fps','cold':CAP/'capture-combined-cold-fps','aspect':PHASE/'division/capture-aspect-fps'}
    inputs={k:read(v/'inputs.json')for k,v in paths.items()}
    for k,v in inputs.items():
        assert v['success'] and v['audio'] and not v['geometry_capability'] and not v['panel'] and not v['command_dma']
        for rel,h in v['results_sha256'].items():assert sha(paths[k]/rel)==h,(k,rel)
    for key in ['F5','combined']:
        for name in ['scene.csv','geometry.csv','culling.csv']:assert (paths[key]/name).read_bytes()==(paths['cold']/name).read_bytes(),(key,name)
    vals={k:list(csv.DictReader((p/'frames.csv').open()))[360:]for k,p in paths.items()};assert all(len(v)==361 for v in vals.values())
    metrics={}
    for metric in ['insns','est_cycles','icache_miss_sdram','dcache_miss','mispredict','gpu_cmd_words']:
        values={k:sum(int(r[metric])for r in rows)for k,rows in vals.items()}
        metrics[metric]={'values':values,'cold_saving_vs_F5_percent':100*(1-values['cold']/values['F5']),'cold_saving_vs_combined_percent':100*(1-values['cold']/values['combined']),'cold_delta_vs_aspect':values['cold']-values['aspect']}
    profiles={k:{r['function']:r for r in csv.DictReader((p/'profile.tsv').open(),delimiter='\t')}for k,p in paths.items()}
    names=['gpu_fast_tex_upload','gpu_fast_tex_fallback','gpu_fast_tex_init','emit_tri_state','gpu_draw_triangles','gfx_sp_tri1','gfx_sp_vertex','gfx_clip_triangle.constprop.0']
    funcs={name:{k:profiles[k].get(name)for k in profiles}for name in names}
    upload=profiles['cold']['gpu_fast_tex_upload'];assert int(upload['self_insns'])>10*int(upload['calls'])
    elf=CAND/'diagnostic.elf';build=read(CAND/'build-diagnostic.json');assert build['returncode']==0 and build['elf_sha256']==sha(elf)==sha(paths['cold']/'sm64.elf')
    nm=subprocess.check_output(['llvm-nm','-S',str(elf)],text=True)
    symbols={}
    for name in ['gpu_fast_tex_upload','gpu_fast_tex_fallback','gpu_fast_tex_init']:
        m=re.search(r'^([0-9a-f]+) ([0-9a-f]+) [tT] '+name+'$',nm,re.M);assert m,name
        symbols[name]={'address':m[1],'size':int(m[2],16)}
    result={'candidate':'aspect-texmem-cold-v1','source_and_capture_checks_passed':True,'only_source_change':'cold,noinline on three fast-texture helpers','source_closure':{'files':len(sources),'dependency_files':len(depfiles),'dependency_inputs':len(deps)},'metrics_frames360_through720':metrics,'full_run_function_profiles':funcs,'cold_helper_symbols':symbols,'elf_sha256':sha(elf),'evidence_sha256':{str(p.relative_to(ROOT)):sha(p)for p in [CAND/'manifest.json',CAND/'build-diagnostic.json',OUT/'source-inputs.json',CAP/'manifest.json',Path(__file__),*(p/'inputs.json'for p in paths.values())]},'gpu_exact_replay_passed':False,'normal_build_performed':False,'scope':'One FPS candidate and matched actual guest instruction / modeled CPU cost gate only. Active fast texture route verified by upload helper work; GPU replay is required before selection. No additional variants or deployment.'}
    (OUT/'results-fps.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'metrics':metrics,'helpers':symbols},indent=2))
if __name__=='__main__':main()
