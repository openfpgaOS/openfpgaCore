#!/usr/bin/env python3
"""Validate the final paired windows, source manifests and saved-seed fit."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-frontend-20261004'
PRIOR=ROOT/'docs/measurements/sm64-overlap-20261004'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
rows=lambda p:list(csv.DictReader(p.open()))

def result(path):
    manifest=read(path/'inputs.json');assert manifest['success']
    assert not (path/'INVALID.txt').exists()
    for name,digest in manifest['sha256'].items():assert sha(Path(name))==digest,name
    status=read(path/'coupled.json')
    assert status['frames']==status['presented']==8 and not status['ring_overflow']
    assert status['memory_errors']==0 and status['frame_completion_checked']
    data=rows(path/'counters.csv')
    for row in data:
        n=lambda key:int(row['all_'+key])
        assert sum(n(k) for k in ['input_starved','output_blocked','vertex_math','cull_math','worker_other'])==n('total')
        assert n('math_fragment_overlap')<=n('math_raster_overlap')<=n('vertex_math')+n('cull_math')
        assert int(row['max_upload'])<=4095 and int(row['max_output'])<=511
    submit=[r for r in data if r['kind']=='submit'];assert len(submit)==8
    delta={k:int(submit[-1][k])-int(submit[0][k]) for k in submit[0] if k!='kind'}
    return dict(frame_ms=delta['cycle']/700000,cpu_renderer_ms=delta['all_cpu_render']/700000,
                gpu_busy_ms=delta['all_gpu_busy']/700000,worker_math_ms=(delta['all_vertex_math']+delta['all_cull_math'])/700000,
                uploaded_words_per_frame=delta['uploaded_words']/7,counters=delta,
                max_upload=int(submit[-1]['max_upload']),max_output=int(submit[-1]['max_output']))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--model',default='attributes-v6')
    parser.add_argument('--label',default='release')
    parser.add_argument('--fit',default='physical-wide-v11')
    parser.add_argument('--accept',default='mixer-pipe-v6')
    parser.add_argument('--mixer',default='mixer-checks-serial-v2')
    parser.add_argument('--queue-words',type=int,choices=[256,512],default=512)
    args=parser.parse_args()
    report=dict(scope='SM64 os30 raw-attribute frontend; deterministic Bob-omb Battlefield windows',comparisons=[])
    for start,traffic in [(360,1),(500,1),(600,1),(360,2)]:
        base=PRIOR/f'run-w{start}-t{traffic}'
        final=OUT/f'run-{args.label}-{start}-t{traffic}'
        old,new=result(base),result(final)
        assert new['max_output']<args.queue_words
        assert rows(base/'scene.csv')==rows(final/'scene.csv')
        frames={}
        for frame in range(start,start+8):
            a=(base/f'frame_{frame:05}.bin').read_bytes()
            b=(final/f'frame_{frame:05}.bin').read_bytes()
            assert len(a)==len(b)==307200 and a==b,(start,traffic,frame)
            frames[frame]=dict(color=hashlib.sha256(b[:153600]).hexdigest(),depth=hashlib.sha256(b[153600:]).hexdigest())
        report['comparisons'].append(dict(start=start,traffic=traffic,baseline=old,candidate=new,
            throughput_gain_percent=100*(old['frame_ms']/new['frame_ms']-1),frames=frames))
    legacy=OUT/'run-p4-legacy-360';report['legacy_fallback']=result(legacy)
    for frame in range(360,368):
        assert (legacy/f'frame_{frame:05}.bin').read_bytes()==(PRIOR/f'run-w360-t1/frame_{frame:05}.bin').read_bytes()
    normal=OUT/f'run-{args.label}-normal-360-t1';report['normal_build']=result(normal)
    assert rows(normal/'scene.csv')==rows(PRIOR/'run-w360-t1/scene.csv')
    for frame in range(360,368):
        assert (normal/f'frame_{frame:05}.bin').read_bytes()==(PRIOR/f'run-w360-t1/frame_{frame:05}.bin').read_bytes()
    for model in [args.model,'worker']:
        manifest=read(OUT/model/'manifest.json')
        for rel,digest in manifest['sources'].items():assert sha(OUT/model/rel)==digest,(model,rel)
    report['unit_attributes']=read(OUT/'unit/verified.json')
    report['unit_legacy']=read(OUT/'unit-legacy/verified.json')
    for name in ('unit_attributes','unit_legacy'):
        assert report[name]['passed']
        for rel,digest in report[name]['sources'].items():assert sha(ROOT/rel)==digest,rel
    report['source_sha256']=read(OUT/'app-source-sha256.json')
    for rel,digest in report['source_sha256'].items():assert sha(OUT/'SM64'/rel)==digest,rel
    report['diagnostic_app_sha256']=sha(OUT/'SM64/.obj/sm64-perfdiag/app.elf')
    report['normal_app_sha256']=sha(OUT/'SM64/.obj/sm64/app.elf')
    assert report['diagnostic_app_sha256']==sha(OUT/'attributes-p4.elf')
    report['mixer']=read(OUT/args.mixer/'results.json')
    assert report['mixer']['passed'] and report['mixer']['exact_pcm_and_cycle_match']
    assert report['mixer']['candidate_sha256']==sha(OUT/'rtl/audio_mixer.v')
    for path,digest in report['mixer']['sources'].items():assert sha(Path(path))==digest,path
    report['acceptance']=read(OUT/('accept-'+args.accept)/'results.json')
    for case in report['acceptance']['os30'].values():
        assert case['exit_code']==0 and case['failed']==0 and case['passed']>0
    report['bounds']=read(OUT/'bounds-oracle.json')
    report['visibility']=read(OUT/'visibility-checks.json')
    report['lighting_precision']=(OUT/'lighting-precision.log').read_text().strip()
    fit=OUT/('fit-'+args.fit)/'results/pocket-os30-s2'
    report['fit']=read(fit/'result.json')
    assert report['fit']['valid']
    frozen=OUT/('fit-'+args.fit)/'frozen'
    for rel,digest in read(frozen.parent/'frozen-sources.json').items():
        assert sha(frozen/rel)==digest,rel
    for name in ('gpu_core.v','gpu_geometry_worker.v','gpu_edge_walker.v'):
        assert sha(frozen/'src/fpga/common'/name)==sha(OUT/'rtl'/name)
        assert sha(OUT/args.model/'frozen/common'/name)==sha(OUT/'rtl'/name)
        assert sha(OUT/('accept-'+args.accept)/'frozen/src/fpga/common'/name)==sha(OUT/'rtl'/name)
    assert sha(frozen/'src/fpga/common/audio_mixer.v')==report['mixer']['candidate_sha256']
    report['fit_label']=args.fit
    settings=frozen.parent/'frontend-settings.json'
    report['physical_optimization']=read(settings)['physical'] if settings.exists() else False
    report['output_queue_words']=args.queue_words
    sta=Path(report['fit']['directory'])/'output_files/ap_core.sta.rpt'
    main_clock='ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk'
    fmax=[]
    for line in sta.read_text(errors='replace').splitlines():
        fields=[x.strip() for x in line.split(';')]
        if len(fields)>4 and fields[3]==main_clock and re.fullmatch(r'[0-9.]+ MHz',fields[1]):
            fmax.append(float(fields[1].split()[0]))
    assert fmax;report['fit']['minimum_main_fmax_mhz']=min(fmax)
    report['validation']=dict(final_coupled_frames=32,legacy_fallback_frames=8,
        normal_build_frames=8,
        color_and_depth_identical=True,scene_trace_identical=True,ring_overflows=0,memory_errors=0,
        snapshot_batches=10000,large_coordinate_bounds=1000000,moving_frusta=144000,
        gpu_acceptance_checks=sum(c['passed'] for c in report['acceptance']['os30'].values()),
        mixer_pcm_and_cycles_identical=True,mixer_ramp_inputs=16777216)
    report['limitations']=[
        'CPU/cache timings are estimated, not CPU RTL or Pocket FPS. Fixed one game step per submitted frame.',
        'GPU, worker and SDRAM execute real RTL at 100 MHz. External traffic is synthetic; no actual CPU memory-stall feedback or kernel PCM/interrupt cost.',
        'Q30 lighting can differ from CPU float lighting by one RGB8 value (one RGB565 channel step at a quantization boundary). The tested full-game frames are exact; the separate million-sample precision test deliberately includes exact axes.',
        'Multiple directional lights and texture generation retain CPU paths. CPU still traverses display lists, prepares matrices/bounds/materials, culls and clips exceptional geometry.',
        'Saved-seed fit success does not imply 100 MHz timing closure. Refer to all_checks_pass, setup/hold and Fmax.',
        'No full seed sweep and no SD deployment. The edge-overflow repair from the previous task is included.'
    ]
    (OUT/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['SM64/os30 raw-attribute frontend — 2026-10-04','',
        'Lighting/UV preparation now runs in the independent geometry worker.',
        'Raw vertices use four upload words; matrices use seventeen payload words.',
        'CPU culling is retained: moving it wholesale to hardware regressed the entrance window.',
        'Immutable face-plane caching was also dropped: its extra complexity bought only about 0.2%.','',
        'Fixed-workload model results (not device FPS):',
        'Start  Traffic  Baseline ms  Candidate ms  Throughput gain']
    for c in report['comparisons']:
        lines.append(f"{c['start']:5d}  {c['traffic']:7d}  {c['baseline']['frame_ms']:11.4f}  {c['candidate']['frame_ms']:12.4f}  {c['throughput_gain_percent']:14.3f}%")
    f=report['fit']
    lines+=['',f"Saved seed 2: {f['alms']} ALMs, {f['m10ks']} M10Ks; minimum main Fmax {f['minimum_main_fmax_mhz']:.2f} MHz.",
        f"Worst setup {f['worst']['setup']:.3f} ns; hold {f['worst']['hold']:.3f} ns; configured clock remains 100 MHz.",
        f"Geometry output queue: {args.queue_words} words. Physical optimization/retiming: {report['physical_optimization']}.",
        'The mixer ramp pipeline preserves all tested PCM values, emission cycles and throughput.',
        '',json.dumps(report['validation'],indent=2),'','Lighting precision:',report['lighting_precision'],'','Limits:']
    lines.extend('  '+x for x in report['limitations'])
    (OUT/'REPORT.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:17]))

if __name__=='__main__':main()
