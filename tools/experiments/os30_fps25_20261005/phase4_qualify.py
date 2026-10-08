#!/usr/bin/env python3
"""Bind the exact-output F6 device trial; no hardware-FPS or deployment claim."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
CAMP=ROOT/'docs/measurements/os30-fps25-20261005'
P4=CAMP/'phase4';BUILD=P4/'combined-cold';CAND=BUILD/'aspect-texmem-cold-v1'
CAP=P4/'texmem-capture-v2';OUT=P4/'release';RTL=P4/'texmem/extended'
SOURCES={n:'src/sm64/sm64/src/pc/gfx/'+n for n in ('gfx_pc.c','gfx_gpu.c','gfx_gpu.h','gfx_geometry.h')}
SOURCES.update({'perf_pocket.h':'src/sm64/pocket/perf_pocket.h','perf_pocket.c':'src/sm64/pocket/perf_pocket.c','Makefile.sm64':'src/sm64/Makefile'})
FRAMES={40,120,200,360,400,500,600,720}
evidence={}


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def bind(p,expected=None):
    h=sha(p)
    if expected is not None:assert h==expected,(p,h,expected)
    evidence[str(p.relative_to(ROOT))]=h
    return h
def read(p):bind(p);return json.loads(p.read_text())
def expand(mapping):
    for rel,h in mapping.items():bind(ROOT/rel,h)


def main():
    OUT.mkdir(exist_ok=True);assert not (OUT/'QUALIFIED.json').exists()
    parent=read(CAMP/'phase2/QUALIFIED.json');bind(CAMP/'phase2/QUALIFIED.json','bc88095dc147edcc70151da53d7d62b4fbc07f0f10e4f41b30873bd294b4c2e7')
    tree=dict(parent['source_tree_sha256']);assert len(tree)==6747
    source_hashes={n:bind(CAND/n)for n in SOURCES}
    for n,rel in SOURCES.items():tree[rel]=source_hashes[n]
    assert read(BUILD/'source-inputs.json')['files']==tree
    for rel,h in tree.items():bind(BUILD/'SM64'/rel,h)
    source_review=read(P4/'combined-cold-source-independent-review.json')
    assert source_review['passed'] and source_review['source_sha256']==source_hashes
    for name,h in source_review['parent_source_sha256'].items():
        bind(P4/'combined/aspect-texmem-v1'/name,h)
    build_review=read(BUILD/'source-and-build-closure.json')
    assert build_review['source_and_build_closure_passed'];expand(build_review['evidence_sha256'])
    independent=read(P4/'combined-cold-active-independent-review.json')
    bind(Path(__file__).parent/'phase4_texmem_app/audit_combined_cold.py',independent['independent_script_sha256'])
    bind(Path(__file__).parent/'phase4_texmem_app/audit_combined.py')
    assert independent['passed'] and independent['comparisons']==32
    assert independent['all_active_route'] and independent['all_color_depth_exact']
    assert independent['all_candidate_fences_exact'] and independent['all_upload_readbacks_exact']
    for name in ['texmem-capture-v2-independent-review.json','texmem-remap-review.json']:
        assert read(P4/name)['passed']
    bind(P4/'texmem-capture-v2-ordering-review.txt');bind(P4/'texmem-integration-review.txt')
    os_review=read(P4/'os-capability-review/results.json')
    assert os_review['passed'] and os_review['bit25_set_result']==16777216
    bind(ROOT/os_review['matching_elf'],os_review['elf_sha256'])
    bind(P4/'os-capability-review/caps-init-disassembly.txt',os_review['disassembly_sha256'])
    bind(P4/'os-capability-review/os-payload.bin',os_review['payload_sha256'])
    helper=read(P4/'texmem-app/helper-tests-v2/results.json');assert helper['passed']
    bind(P4/'texmem-app/helper-tests-v2/helpers.c',helper['extracted_source_sha256'])
    for n in ['plain','sanitized']:bind(P4/'texmem-app/helper-tests-v2'/n)
    div=read(P4/'division/qualification.json');assert div['passed']
    memory=read(P4/'texmem/REPORT.json');assert memory['passed'];expand(memory['evidence_sha256'])
    for base in [P4/'texmem',RTL]:
        m=read(base/'inputs.json')
        for rel,h in m['sources'].items():bind(base/rel,h)
    model=read(RTL/'model/build.json');bind(RTL/'model/obj/Vtb_gpu',model['simulator_sha256'])
    qsim=read(CAP/'manifest.json');bind(CAP/'qsim/qsim',qsim['simulator_sha256'])
    for name,h in qsim['sources'].items():bind(CAP/'qsim'/name,h)
    assert read(CAP/'f5-noninterference.json')['passed']
    cpu={};gpu=[];elfs={}
    for mode,filename in [('normal','app.elf'),('fps','diagnostic.elf')]:
        elfs[mode]=bind(CAND/filename)
        assert independent['pins'][mode]['elf_sha256']==elfs[mode]
        rec=read(CAND/('build-normal.json' if mode=='normal' else 'build-diagnostic.json'))
        assert rec['returncode']==0 and rec['elf_sha256']==elfs[mode]
        for n,h in source_hashes.items():assert rec['inputs'][n]['sha256']==h
        capture=CAP/('capture-combined-cold-'+mode);m=read(capture/'inputs.json')
        assert independent['pins'][mode]['capture_inputs_sha256']==sha(capture/'inputs.json')
        assert m['success'] and m['audio'] and not m['panel'] and not m['geometry_capability']
        bind(capture/'sm64.elf',elfs[mode])
        for rel,h in m['results_sha256'].items():bind(capture/rel,h)
        cpu[mode]=read(BUILD/'active'/f'cpu-{mode}.json')
        assert all(cpu[mode]['scene_geometry_culling_exact'].values())
        rows=read(BUILD/'active'/f'replay-{mode}.json')
        assert independent['pins'][mode]['replay_report_sha256']==sha(BUILD/'active'/f'replay-{mode}.json')
        assert {(r['traffic'],r['frame'])for r in rows}=={(t,f)for t in (1,2)for f in FRAMES}
        assert len(rows)==16
        for row in rows:
            assert row['color_depth_exact'] and row['simulator_sha256']==model['simulator_sha256']
            stem=BUILD/'active/replays'/mode/str(row['traffic'])/f"frame_{row['frame']:05}"
            bind(stem.with_suffix('.vec'),row['adapted_vector_sha256'])
            bind(stem.with_suffix('.bin'),row['output_sha256']);bind(stem.with_suffix('.log'),row['log_sha256'])
            bind(capture/'gpuvec'/f"frame_{row['frame']:05}.vec",row['vector_sha256'])
            assert row['gpu_cycle_saving_percent']>=0
            gpu.append(row)
    for name in ['phase4_qualify.py','phase4_publish.py','phase2_publish.py','phase4_combined_validate.py','phase4_combined_cold_validate.py','phase4_texmem_qsim_v2.py']:
        bind(Path(__file__).with_name(name))
    result=dict(candidate='aspect-texmem-cold-v1',candidate_directory=str(CAND.relative_to(ROOT)),
        qualified_for_device_trial=True,independent_review_passed=True,target_verified=False,
        parent='phase2/exact-v1/F5',fps_header='PERF F6 FPS',source_sha256=source_hashes,source_tree_sha256=tree,
        normal_elf_sha256=elfs['normal'],diagnostic_elf_sha256=elfs['fps'],
        core_sha256=parent['retained_core_sha256'],os_sha256='054abd4b3d2c57d55b4ecec23f2fcaa3703206db3df14ebe7793c22941d34ef7',
        cpu=cpu,gpu_replays=gpu,evidence_sha256=evidence,
        limits=['Qualified for a device trial, not verified25FPS or net performance.','GPU replay uses syntheticCPU/audio/scanout traffic and serializes each command batch.','qsim upload busy is instantaneous; separate actual-controller tests cover transfer costs and concurrent fills.','FPGA bitstream, clock, timing and ALM usage unchanged.','Software texture metadata uses128KiB extra SDRAM. All textures retain SDRAM backup; exhaustion permanently falls back after one drain.','Real controller/chip model exercises the first2MiB of die0, not every possible16MiB address.'])
    (OUT/'QUALIFIED.json').write_text(json.dumps(result,indent=2)+'\n')
    game=[r for r in gpu if r['mode']=='fps' and r['traffic']==2 and r['frame']>=360]
    report=['F6 device trial: exact aspect quotient cache plus persistent dedicated texture memory.','',
        '32/32 normal/FPS color/depth comparisons pass; active route and every live upload checked.',
        'No FPGA change, fit or full seed sweep. No measured deviceFPS result.',
        'FPGA resources unchanged; software metadata adds128KiB SDRAM.','']
    for mode in ('normal','fps'):
        costs=cpu[mode]['gameplay_frames360_through720']['est_cycles']
        report.append(f"{mode}: modeled CPU change {100*(costs['combined']/costs['f5']-1):+.4f}%; {(costs['combined']-costs['f5'])/361/100000:+.5f} ms/frame at100MHz. Busy polling minimum is excluded.")
    report+=['',f"Gameplay traffic2 GPU completion saving {min(r['gpu_cycle_saving_percent']for r in game):.3f}–{max(r['gpu_cycle_saving_percent']for r in game):.3f}%.",
        'GPU and CPU component times overlap and must not be added to predictFPS.',
        'The25FPS target remains unverified. The last physical observation is F5 at22.2FPS.']
    (OUT/'REPORT.txt').write_text('\n'.join(report)+'\n')
    print('\n'.join(report));print('Bound evidence files:',len(evidence))


if __name__=='__main__':main()
