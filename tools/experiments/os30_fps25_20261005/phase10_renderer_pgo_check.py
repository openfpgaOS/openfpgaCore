#!/usr/bin/env python3
"""Seal the one renderer-only in-sample edge-PGO screen."""
import csv,json,re,shutil
from pathlib import Path
from phase10_experiment import ROOT,CAMP,INPUTS,module,sha,write,verify_capture
H=Path(__file__).resolve().parent; P=CAMP/'phase10'; OUT=P/'renderer-pgo-v1'

def main():
    assert not (OUT/'manifest.json').exists()
    prep=json.loads((OUT/'prepared.json').read_text()); cmd=json.loads((OUT/'commands.json').read_text())
    a,b=OUT/'capture-baseline2',OUT/'capture-use'
    ar=verify_capture(a,prep['selected_elf_sha256']); br=verify_capture(b,sha(OUT/'use/app.elf'))
    assert len(ar)==len(br)==721
    for n in ('scene.csv','geometry.csv','culling.csv'):assert (a/n).read_bytes()==(b/n).read_bytes(),n
    assert [r['gpu_cmd_words']for r in ar]==[r['gpu_cmd_words']for r in br]
    for n in ('frames.csv','scene.csv','geometry.csv','culling.csv','profile.tsv'):
        assert (OUT/'capture-baseline'/n).read_bytes()==(a/n).read_bytes(),('bridge-off baseline',n)
    v=module('pgocheck_v',H/'phase7_validate.py');v.v=module('pgocheck_v16',OUT/'vector16.py')
    c=module('pgocheck_lod',H/'phase10_load_cram_capture.py'); vec=[]
    for f in sorted((a/'gpuvec').glob('*.vec')):
        target=b/'gpuvec'/f.name; old,new=[v.v.vector(x.read_bytes())for x in(f,target)]
        assert c.render_events(old)==c.render_events(new),(f.name,'ordered events')
        assert all(old[k]==new[k]for k in('depth','fence'))
        # Private high source-pool addresses may relink; ordinary texture bytes remain checked.
        stop=min(0xfc0000,max(len(old['texture']),len(new['texture'])))
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())
               for i in range(0,stop,4)if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        vec.append(dict(frame=int(f.stem.split('_')[1]),baseline_sha256=sha(f),candidate_sha256=sha(target),
                        ordered_events=len(c.render_events(old)),texture_changes=diffs))
    assert len(vec)==8
    pad=v.padding(vec,a,b,OUT/'work')
    counters={}
    for n in ('gfx_pc','gfx_gpu'):
        gd=(OUT/'training'/f'{n}.gcda.dump').read_text();gn=(OUT/'training'/f'{n}.gcno.dump').read_text()
        fn=r'FUNCTION ident=(\d+), lineno_checksum=(0x[0-9a-f]+), cfg_checksum=(0x[0-9a-f]+)'
        assert sorted(re.findall(fn,gd))==sorted(re.findall(fn,gn))
        assert re.search(r':stamp (\d+)',gd).group(1)==re.search(r':stamp (\d+)',gn).group(1)
        assert 'COUNTERS' in gd and not re.search(r'COUNTERS (?!arcs)',gd)
        use=(OUT/'use'/f'{n}-profile.txt').read_text()
        assert 'Read edge' in use and 'Profile feedback for function is available' in use
        assert 'Setting hotness threshold to 0' in use
        log=(OUT/'use'/f'{n}-compile.log').read_text().split('\n',1)[1]
        assert not re.search(r'warning:|error:',log)
        counters[n]=dict(functions=len(re.findall(fn,gd)),read_edges=use.count('Read edge'),
            available_functions=use.count('Profile feedback for function is available'),hotness_threshold=0,
            summary_zero_unmodified=True,compile_warnings=0)
    changed=[]
    for n,d in prep['baseline_objects'].items():
        if sha(OUT/'work'/n)!=d:changed.append(n)
    assert sorted(Path(n).stem for n in changed)==['gfx_gpu','gfx_pc']
    for n,r in INPUTS.items():assert sha(OUT/n)==sha(OUT/'work'/r)==prep['sources'][n]
    timing={}
    for name,start,end in [('gameplay',360,720),('warm8',360,367),('cold8',234,241),('early',360,479),('middle',480,599),('late',600,720)]:
        sums={}
        for label,rows in [('baseline',ar),('pgo',br)]:
            chosen=[r for r in rows if start<=int(r['frame'])<=end];assert len(chosen)==end-start+1
            sums[label]={k:sum(int(r[k])for r in chosen)for k in('est_cycles','insns','icache_miss_sdram','dcache_miss','mispredict','gpu_cmd_words')}
            sums[label]['frame_ms']=sums[label]['est_cycles']/(len(chosen)*100000)
        timing[name]=dict(start=start,end=end,frames=end-start+1,**sums,
            saving_ms=sums['baseline']['frame_ms']-sums['pgo']['frame_ms'],
            saving_percent=100*(1-sums['pgo']['est_cycles']/sums['baseline']['est_cycles']))
    result=dict(passed=True,baseline_elf_sha256=sha(OUT/'baseline.elf'),candidate_elf_sha256=sha(OUT/'use/app.elf'),
        all721_records_exact=True,all721_wordcounts_exact=True,ordered_streams=vec,padding_check=pad,
        native_fma=True,qsim_sha256=sha(OUT/'qsim-export-v2/qsim'),export_bridge_off_cycles_exact=True,
        actual_pixel_render_proof=False,in_sample=True,training_frames=721,evaluation_frames=721,
        edge_profiles=counters,only_two_renderer_objects_changed=changed,timing=timing,
        limitations=['Synthetic two-poll LOAD and eager GPU model; not hardware FPS or actual 3D buffer proof.',
          'Same deterministic run trains and evaluates; this is an in-sample screen, not a generalization claim.',
          'GCC freestanding stream carries real edge counts but no object summary; merge-stream runs/sum_max remain zero. Hotness threshold0 is documented, not patched.',
          'Profile-use implicitly changes compiler optimization choices; source arithmetic/ISA/FP flags and assets are unchanged.'],deployment_ready=False)
    write(OUT/'result.json',result)
    (OUT/'REPORT.txt').write_text('Renderer edge-PGO first screen: PASS\n\n'
        f"Gameplay360..720: {timing['gameplay']['baseline']['frame_ms']:.9f} -> {timing['gameplay']['pgo']['frame_ms']:.9f} ms/frame; saves {timing['gameplay']['saving_ms']:.9f} ms ({timing['gameplay']['saving_percent']:.4f}%).\n"
        f"Warm8 saves {timing['warm8']['saving_ms']:.9f} ms; cold8 saves {timing['cold8']['saving_ms']:.9f} ms.\n\n"
        'All721 scene/geometry/culling records and command counts are exact. Eight ordered renderer streams, depth/fence records and ordinary texture bytes match, apart from previously bounded star-upload linked-pointer padding. Source-pool uploads are private data transport and excluded from ordered render events. Qsim 3D command no-op behavior means its framebuffer hashes are not renderer proof.\n\n'
        'Only gfx_pc.c/gfx_gpu.c objects were rebuilt. Original arithmetic/source flags, game/assets and hardware remain unchanged. Training uses -fprofile-arcs/-ftest-coverage/-fprofile-info-section, real libgcov stream output after timing reports, and actual gcov-tool merge-stream. No value/indirect profile stubs or fabricated histograms. All136 gcno/gcda function identities/checksums match, both profile-use logs consume real edge feedback with no warnings. Uninstrumented optimized ELF has no gcov section/symbols.\n\n'
        'GCC13 freestanding export omits object summary. merge-stream produced runs=0,sum_max=0; compiler hotness threshold is0. These bytes are retained unmodified. This affects optimization policy and limits interpretation; it does not erase the nonzero edge counters. Profile-use can enable inlining, unrolling, loop peeling/vectorization and tracing beyond ordinary O3. Exact ISA/fast-math flags remain original.\n\n'
        'Initial export used the already-closed console and emitted no stream; its source/run remain preserved. Export-v2 opens a separate untimed sink and requires guest exit0. First merge-stream lacked nested output parents; its failure log is preserved, parents were validated and one successful merge performed. Baseline with bridge disabled is cycle-identical before/after the host fix.\n\n'
        'This is one in-sample deterministic721-frame screen. No deployment or Pocket FPS claim. Root authorized one matched warm8+cold8 actual RTL comparison after this screen, with no new compiler/hardware choice.\n')
    shutil.copy2(__file__,OUT/'analysis-recipe.py');shutil.copy2(H/'phase10_renderer_pgo.py',OUT/'recipe-final.py')
    files={str(f.relative_to(OUT)):sha(f)for f in OUT.rglob('*')if f.is_file()and not any(x in f.relative_to(OUT).parts for x in('work','__pycache__','build'))and f.name!='manifest.json'}
    evidence={str(p.relative_to(ROOT)):sha(p)for p in [P/'load-submit-coupled-v1/manifest.json',P/'load-submit-delivery-v1/manifest.json',P/'renderer-pgo-feasibility-v1/manifest.json',H/'phase7_validate.py',H/'phase10_load_cram_capture.py']}
    write(OUT/'manifest.json',dict(passed=True,files=files,evidence_sha256=evidence,deployment_ready=False))
    print(json.dumps(dict(manifest_sha256=sha(OUT/'manifest.json'),timing=timing),indent=2))
if __name__=='__main__':main()
