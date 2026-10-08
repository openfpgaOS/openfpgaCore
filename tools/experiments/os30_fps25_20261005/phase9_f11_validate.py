#!/usr/bin/env python3
"""Private F11 accounting, rendered panel and retained-A GPU replay checks."""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import struct
import subprocess

from phase9_experiment import ROOT,CAMP,PARENT,module,sha,verify_capture,write,flat_inputs

HERE=Path(__file__).resolve().parent
LABEL='checks-state-f11'
DEST=CAMP/'phase9'/LABEL
RTL=CAMP/'phase4/texmem/extended/model'
MARKER='PERF F11 PREP'


def ready(mode):
    elf=DEST/('app.elf' if mode=='normal' else 'diagnostic.elf')
    build=DEST/f'build-{mode}.json'
    record=json.loads(build.read_text())
    assert record['returncode']==0 and sha(elf)==record['elf_sha256']
    assert flat_inputs(DEST)=={n:v['sha256'] for n,v in record['inputs'].items()}
    return elf


def perf():
    m=module('phase9_f11_accounting',HERE/'phase6_perf_tests.py')
    assert m.HARNESS.count('PERF F8 SEND')==1
    m.HARNESS=m.HARNESS.replace('PERF F8 SEND',MARKER)
    m.BASE,m.CAND=DEST,DEST
    m.main()
    write(DEST/'perf-invocation.json',dict(recipe_sha256=sha(Path(__file__)),
        accounting_helper_sha256=sha(HERE/'phase6_perf_tests.py'),
        candidate_source_sha256=flat_inputs(DEST),result_sha256=sha(DEST/'perf-tests.json'),
        adaptation='Only expected marker changes to PERF F11 PREP; all existing accounting checks retained.'))


def capture_panel():
    ready('diagnostic')
    m=module('phase9_f11_panel_capture',HERE/'phase9_experiment.py')
    m.capture(LABEL,'diagnostic',panel=True)


def replay(mode):
    elf=ready(mode)
    capture=DEST/('capture-'+mode)
    verify_capture(capture,sha(elf))
    assert json.loads((DEST/f'analysis-{mode}.json').read_text())['passed']
    prior_rows=json.loads((PARENT/f'replay-{mode}.json').read_text())
    prior_rows={(r['traffic'],r['frame']):r for r in prior_rows}
    exe=RTL/'obj/Vtb_gpu'
    assert sha(exe)==json.loads((RTL/'build.json').read_text())['simulator_sha256']
    adapter=module('phase9_f11_depth',HERE/'phase4_combined_validate.py')

    def one(job):
        traffic,frame=job
        source=capture/'gpuvec'/f'frame_{frame:05d}.vec'
        directory=DEST/'replays'/mode/str(traffic);directory.mkdir(parents=True,exist_ok=True)
        stem=directory/source.stem
        assert not any(stem.with_suffix(suffix).exists() for suffix in ['.vec','.log','.bin'])
        commands,depth,tags=adapter.with_depth(source,stem.with_suffix('.vec'));assert depth
        command=[str(exe),str(stem.with_suffix('.vec')),str(stem.with_suffix('.bin')),
            f'+memory_traffic={traffic}','+tagged_tex=1']
        with stem.with_suffix('.log').open('x') as log:
            subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
        raw=stem.with_suffix('.bin').read_bytes();assert len(raw)==307212
        stats={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',stem.with_suffix('.log').read_text())}
        assert stats['cram_errors']==0 and stats['words']==stats['readback_words']
        prior=prior_rows[traffic,frame]
        gold=PARENT/'replays'/mode/str(traffic)/stem.with_suffix('.bin').name
        assert sha(gold)==prior['output_sha256']
        old=gold.read_bytes()
        return dict(traffic=traffic,frame=frame,commands=commands,tags=tags,command=command,stats=stats,
            color_exact=raw[:153600]==old[:153600],depth_exact=raw[153612:]==old[153612:],
            fence_exact=raw[153600:153604]==old[153600:153604],
            gpu_cycle_change_percent=100*(stats['cycles']/prior['stats']['cycles']-1),
            simulator_sha256=sha(exe),source_vector_sha256=sha(source),
            adapted_vector_sha256=sha(stem.with_suffix('.vec')),output_sha256=sha(stem.with_suffix('.bin')),
            log_sha256=sha(stem.with_suffix('.log')),reference_output_sha256=sha(gold),
            reference_path=str(gold.relative_to(ROOT)),candidate_elf_sha256=sha(elf),
            recipe_sha256=sha(Path(__file__)))

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(one,[(t,f) for t in (1,2) for f in (360,600,720)]))
    write(DEST/f'replay-{mode}.json',results)
    assert all(r['color_exact'] and r['depth_exact'] and r['fence_exact'] for r in results)
    print(json.dumps(dict(mode=mode,comparisons=len(results),passed=True,
        gpu_cycle_changes=[r['gpu_cycle_change_percent'] for r in results])))


def panel():
    from PIL import Image
    elf=ready('diagnostic')
    validation=module('phase9_f11_snapshot',HERE/'phase6_validate.py')
    adapter=module('phase9_f11_panel_depth',HERE/'phase4_combined_validate.py')
    capture=DEST/'capture-diagnostic-panel';verify_capture(capture,sha(elf))
    assert json.loads((capture/'inputs.json').read_text())['panel']
    snapshots=validation.snapshot_checks(capture,panel=True)
    for name in ('scene.csv','geometry.csv','culling.csv'):
        assert sha(capture/name)==sha(PARENT/'capture-diagnostic-panel'/name)
    directory=DEST/'panel';directory.mkdir(exist_ok=False)
    source=capture/'gpuvec/frame_00640.vec';vector=directory/source.name
    adapter.with_depth(source,vector)
    exe=RTL/'obj/Vtb_gpu';assert sha(exe)==json.loads((RTL/'build.json').read_text())['simulator_sha256']
    command=[str(exe),str(vector),str(directory/'frame.bin'),'+memory_traffic=2','+tagged_tex=1']
    with (directory/'replay.log').open('x') as log:
        subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    raw=(directory/'frame.bin').read_bytes();assert len(raw)==307212
    pixels=struct.unpack_from('<76800H',raw);im=Image.new('RGB',(320,240))
    im.putdata([(((p>>11)&31)*255//31,((p>>5)&63)*255//63,(p&31)*255//31) for p in pixels])
    im.save(directory/'panel.png')
    write(directory/'result.json',dict(passed=True,snapshot_checks=snapshots,command=command,
        candidate_elf_sha256=sha(elf),capture_inputs_sha256=sha(capture/'inputs.json'),
        simulator_sha256=sha(exe),source_vector_sha256=sha(source),vector_sha256=sha(vector),
        output_sha256=sha(directory/'frame.bin'),image_sha256=sha(directory/'panel.png'),
        recipe_sha256=sha(Path(__file__)),
        scope='F11 PREP marker; unchanged55-word version8 snapshot accounting. Displayed timing is simulated, not physical FPS.'))
    print(directory/'panel.png')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['perf','capture-panel','replay','panel'])
    p.add_argument('--mode',choices=['normal','diagnostic'],default='normal')
    args=p.parse_args()
    if args.action=='replay':replay(args.mode)
    elif args.action=='capture-panel':capture_panel()
    else:globals()[args.action]()
