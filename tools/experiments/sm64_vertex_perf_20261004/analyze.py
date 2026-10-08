#!/usr/bin/env python3
"""Validate P3 accounting, rendering and measured diagnostic model overhead."""
import csv
import hashlib
import json
from pathlib import Path
import statistics

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/sm64-vertex-perf-20261004'
BASE=ROOT/'docs/measurements/sm64-triangle-cache-20261004'
STAGE=OUT/'SM64'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
rows=lambda p:list(csv.DictReader(p.open()))

def main():
    app=sha(STAGE/'.obj/sm64-perfdiag/app.elf')
    normal=sha(STAGE/'.obj/sm64/app.elf')
    control=OUT/'capture-p2-control'
    hidden=OUT/'capture-final-hidden'
    panel=OUT/'capture-final-panel'
    for name in ('frames.csv','scene.csv','geometry.csv'):
        assert (control/name).read_bytes()==(BASE/'capture-final-tagged'/name).read_bytes(),name
    for path in (hidden,panel):
        assert load(path/'inputs.json')['success']
        assert sha(path/'sm64.elf')==app
        assert len(rows(path/'frames.csv'))==721
        assert (path/'scene.csv').read_bytes()==(control/'scene.csv').read_bytes()
        assert load(path/'summary.json')['gpu_sync']['ring_overflows']==0
        valid=[x for x in rows(path/'diagnostic.csv') if x['stat0']=='1']
        assert valid
        for x in valid:
            assert x['stat18']=='3' and int(x['stat19'])>0
            assert 0<=int(x['stat20'])-sum(int(x[f'stat{i}']) for i in range(21,29))<8
            assert 0<=int(x['stat22'])-sum(int(x[f'stat{i}']) for i in range(30,35))<5
    frozen=[x for x in rows(panel/'diagnostic.csv') if 603<=int(x['frame'])<=679]
    assert len(frozen)==77
    assert len({tuple(x[f'stat{i}'] for i in range(39)) for x in frozen})==1
    assert (hidden/'geometry.csv').read_bytes()==(control/'geometry.csv').read_bytes()
    a,b=rows(control/'frames.csv'),rows(hidden/'frames.csv')
    assert [x['gpu_cmd_words'] for x in a]==[x['gpu_cmd_words'] for x in b]
    windows=[]
    for f in range(360,720,60):
        old=statistics.mean(int(x['est_cycles']) for x in a[f:f+60])
        new=statistics.mean(int(x['est_cycles']) for x in b[f:f+60])
        windows.append(dict(first_frame=f,p2_cpu_ms=old/100000,p3_cpu_ms=new/100000,
                            modeled_overhead_percent=100*(new/old-1)))
    old=statistics.mean(int(x['est_cycles']) for x in a[360:720])
    new=statistics.mean(int(x['est_cycles']) for x in b[360:720])
    prior={x['frame']:x for x in load(BASE/'replay-results-final-tagged-final-panel-worker.json')
           if x['label']=='final-tagged'}
    pairs=[]
    replays=load(OUT/'replay-results-final-hidden-final-panel-worker.json')
    assert len(replays)==12
    for x in replays:
        assert x['hardware_sha256']==prior[x['frame'] if x['frame'] in prior else 600]['hardware_sha256']
        if x['label']=='final-hidden' or x['frame'] in (360,720):
            for k in ('framebuffer_sha256','depth_sha256'):
                assert x[k]==prior[x['frame']][k],(x['label'],x['frame'],k)
            pairs.append(dict(label=x['label'],frame=x['frame']))
    assert len(pairs)==10
    timing=load(OUT/'timing-tests.json')
    assert timing['source_sha256']==sha(STAGE/'src/sm64/pocket/perf_pocket.c')
    assert timing['header_sha256']==sha(STAGE/'src/sm64/pocket/perf_pocket.h')
    disabled=load(OUT/'disabled-loadable-check.json')
    assert disabled['disabled_sha256']==normal
    assert disabled['only_assertion_line_numbers_changed'] and disabled['same_segment_layout']
    changed=['src/sm64/pocket/perf_pocket.h','src/sm64/pocket/perf_pocket.c',
        'src/sm64/sm64/src/pc/gfx/gfx_pc.c','src/sm64/sm64/src/pc/gfx/gfx_geometry.h',
        'src/sm64/sm64/src/pc/gfx/gfx_vertex_cull.h','PERFORMANCE-TEST.txt']
    source={rel:sha(STAGE/rel) for rel in changed}
    unchanged={rel:digest for rel,digest in load(OUT/'before.json').items()
               if rel not in changed and not rel.startswith(('.obj/','build/'))}
    for rel,digest in unchanged.items():
        assert sha(STAGE/rel)==digest,rel
    result=dict(diagnostic_app_sha256=app,normal_app_sha256=normal,
        source_sha256=source,unchanged_sha256=unchanged,cpu_windows=windows,
        mean_modeled_overhead_percent=100*(new/old-1),
        baseline_control_identical=True,scene_records_per_capture=721,
        geometry_and_gpu_word_counts_identical=True,paired_framebuffer_depth_comparisons=len(pairs),
        comparisons=pairs,frozen_snapshot_records=len(frozen),snapshot_words=39,
        timing_tests=timing,disabled_loadable_check=disabled,
        fpga_changed=False,full_seed_sweep_run=False,hardware_fps_measured=False,
        limitations=[
            'P3 is instrumentation, not a performance optimization; diagnostic overhead on the Pocket is not yet measured.',
            'CPU fallback includes its own lighting/UV/fog; ATTR is the separate deferred path and shared light-direction setup.',
            'Late vertex reconstruction keeps its original TRI timing attribution. REC counts it without adding it again to VERT.',
            'RAW/CPU/OUT are sampled load routes, not GPU utilization; REC is subsequent work on previously counted vertices.',
            'One-in-eight sampling and independent 0.1ms truncation remain unchanged; REND averages all frames.',
            'The model cannot measure real CPU/GPU/display overlap or hardware FPS.'])
    (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('diagnostic_app_sha256','normal_app_sha256',
        'mean_modeled_overhead_percent','paired_framebuffer_depth_comparisons','frozen_snapshot_records')},indent=2))

if __name__=='__main__':
    main()
