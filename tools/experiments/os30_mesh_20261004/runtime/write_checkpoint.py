#!/usr/bin/env python3
"""Record the selected private app, source dependencies and paired measurements."""
from pathlib import Path
import hashlib,json,shlex
ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/'docs/measurements/os30-mesh-20261004'
APP=OUT/'SM64';BUILD=APP/'.obj/sm64-perfdiag';WORK=APP/'src/sm64'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
selected=OUT/'hybrid-reviewed-v5.elf'
assert sha(BUILD/'app.elf')==sha(selected)=='9c4f2ccb1fab13381b33af343b5df59000e7efab4cd9de1078817cf33fd43276'
dependencies=set()
for path in BUILD.rglob('*.d'):
    rule=path.read_text().replace('\\\n',' ').splitlines()[0].split(':',1)[1]
    for name in shlex.split(rule):
        source=Path(name);source=source if source.is_absolute() else WORK/source
        source=source.resolve();assert source.is_file(),source
        dependencies.add(source)
for name in ('Makefile','../sdk/sdk.mk','../sdk/app.ld','../../tools/sdk-container.sh',
             '../../tools/docker/Dockerfile.firmware'):
    source=(WORK/name).resolve();assert source.is_file();dependencies.add(source)
for source in (APP/'src/sdk/musl/lib').iterdir():
    if source.is_file() and source.suffix in ('.a','.o'):dependencies.add(source)
source_manifest=dict(elf=str(selected.relative_to(ROOT)),sha256=sha(selected),
    restored_build_elf_sha256=sha(BUILD/'app.elf'),
    compiler=(BUILD/'.sdk-ccid').read_text().strip(),container='openfpgaos-firmware',
    command='make -C docs/measurements/os30-mesh-20261004/SM64/src/sm64 -j3 PERF_DIAG=1 '+str(BUILD/'app.elf'),
    flags={'architecture':'rv32imafc','abi':'ilp32f','optimization':'-O3 -ffast-math',
           'PERF_DIAG':1,'MESH_LAZY_FAST_DEPTH':0,'MESH_LAZY_NOINLINE':0},
    program_words=410,projected_fallback_entry=404,
    dependencies={str(p.relative_to(ROOT)):sha(p) for p in sorted(dependencies)})
manifest=OUT/'selected-v5-source-manifest.json';manifest.write_text(json.dumps(source_manifest,indent=2)+'\n')
measurements=[]
for version in range(3,10):
    path=OUT/f'coupled-comparison-hybrid-v{version}.json';data=json.loads(path.read_text())
    info=json.loads((OUT/f'hybrid-review-v{version}.json').read_text())
    assert all(f['color_differences']==f['depth_differences']==0 for f in data['frames'])
    assert sha(OUT/f'hybrid-reviewed-v{version}.elf')==info['sha256']
    measurements.append(dict(version=version,elf=f'hybrid-reviewed-v{version}.elf',sha256=info['sha256'],
        mean_frame_interval_ms=data['candidate']['mean_submit_interval_ms'],
        throughput_gain_percent=data['throughput_gain_percent'],
        cpu_render_ms=data['candidate']['counter_ms_per_frame']['cpu_render'],
        change=info['changes'],all_eight_color_depth_frames_identical=True,
        memory_errors=data['candidate']['validation']['memory_errors'],mesh_fault=data['candidate']['validation']['mesh_fault']))
baseline=data['baseline']
report=dict(scope='Private os30/SM64 experimental complete-frame comparison; no deployment.',
    frames=list(range(360,368)),clock_hz=100000000,external_memory_traffic_schedule=1,
    baseline={'variant':'installed attributes-v6/P4 reference','mean_frame_interval_ms':baseline['mean_submit_interval_ms'],
              'cpu_data_cache_kib':128,'frozen_run':'coupled-run-baseline-p4-360-t1'},
    candidate_hardware={'frozen_model':'coupled-hybrid-v1','cpu_data_cache_kib':64,'output_fifo_words':1024,
                        'code_pages':2,'texture_queue':'M10K','program_words':410},
    measurements=measurements,selected_version=5,selected_source_manifest=manifest.name,
    selected_source_rebuild_byte_identical=True,substantial_performance_improvement_achieved=False,
    production_unchanged_by_this_experiment=True,deployed=False,full_seed_sweep_run=False,
    coverage_report='runtime-coverage-v1/coverage-summary.json',
    conclusions=['Fastest verified private candidate v5 is only0.425% faster in this model, effectively unchanged for the user goal.',
                 'All seven corrected variants reproduce every reference color/depth pixel in the eight-frame window.',
                 'Retained-context and reciprocal experiments remain optional archives; normal private source was restored to v5.',
                 'Decal and texture-alpha material paths retain exact original CPU fallback.'],
    limitations=['CPU instructions/cache timing are estimated; GPU/DMA/raster/SDRAM timing run in RTL.',
                 'Fixed-step captured scene and synthetic external traffic do not measure Pocket FPS.',
                 'These results do not establish hardware fit, timing closure, other-scene performance or deployment qualification.'])
(OUT/'performance-checkpoint-v1.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'report':'performance-checkpoint-v1.json','source_manifest':manifest.name,
                  'dependencies':len(dependencies),'selected_sha256':sha(selected)},indent=2))
