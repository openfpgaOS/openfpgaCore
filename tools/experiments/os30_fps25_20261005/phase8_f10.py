#!/usr/bin/env python3
"""Stage F10 KICK diagnostic over the isolated tail-publication candidate.

This does not qualify or deploy a speed improvement. The normal ELF must be
identical to the measured kick128-tail normal ELF; only the panel marker changes.
"""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from phase8_experiment import CAMP, INPUTS, OUT, PARENT, ROOT, canonical, module, sha, simulator, verify_capture, write

LABEL = 'kick128-tail-f10'
BASE = OUT / 'kick128-tail'
DEST = OUT / LABEL
HERE = Path(__file__).resolve().parent


def prepare():
    expected = json.loads((BASE/'build-normal.json').read_text())
    assert sha(BASE/'app.elf') == expected['elf_sha256']
    DEST.mkdir(exist_ok=False)
    before, after = {}, {}
    for name, relative in INPUTS.items():
        assert sha(BASE/name) == expected['inputs'][name]['sha256'] == sha(BASE/'SM64'/relative)
        data = (BASE/name).read_bytes()
        before[name] = sha(BASE/name)
        if name == 'perf_pocket.c':
            assert data.count(b'PERF F9 TRI') == 1
            data = data.replace(b'PERF F9 TRI',b'PERF F10 KICK')
        (DEST/name).write_bytes(data)
        after[name] = sha(DEST/name)
    subprocess.run(['cp','-a','--reflink=auto',str(BASE/'SM64'),str(DEST/'SM64')],check=True)
    write(DEST/'prepared.json',dict(parent='kick128-tail',parent_normal_elf_sha256=expected['elf_sha256'],
        parent_source_sha256=before,candidate_source_sha256=after,recipe_sha256=sha(Path(__file__)),
        changes='Panel marker PERF F9 TRI -> PERF F10 KICK only.',qualified=False,production_changed=False))
    print(DEST)


def build(mode):
    diagnostic = mode == 'diagnostic'
    assert not (DEST/f'build-{mode}.json').exists()
    b=module('f10_builder',HERE/'build.py')
    b.OUT,b.STAGE,b.INPUTS=OUT,DEST/'SM64',INPUTS
    objects=b.STAGE/'.obj'/('sm64-perfdiag' if diagnostic else 'sm64')
    assert objects.parent==DEST/'SM64/.obj' and not objects.is_symlink()
    if objects.exists(): shutil.rmtree(objects)
    sys.argv=[str(Path(__file__)),LABEL,'--diagnostic',str(int(diagnostic))]
    b.main()
    if not diagnostic:
        assert sha(DEST/'app.elf')==sha(BASE/'app.elf'),'Normal ELF changed with panel-only marker'


def capture(panel=False):
    m=module('f10_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT=DEST
    sys.argv=[str(Path(__file__)),'diagnostic-panel' if panel else 'diagnostic',
        '--elf',str(DEST/'diagnostic.elf'),'--exe',str(simulator()),'--geometry','0','--audio']
    if panel:sys.argv+=['--panel','--vectors','640']
    m.main()


def analyze():
    prior=PARENT/'capture-diagnostic'
    current=DEST/'capture-diagnostic'
    a=verify_capture(prior,sha(PARENT/'diagnostic.elf'))
    b=verify_capture(current,sha(DEST/'diagnostic.elf'))
    assert len(a)==len(b)==721
    exact={n:sha(prior/n)==sha(current/n) for n in ('scene.csv','geometry.csv','culling.csv')}
    assert all(exact.values())
    assert [r['gpu_cmd_words'] for r in a]==[r['gpu_cmd_words'] for r in b]
    totals={}
    for key in ('insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict'):
        x,y=(sum(int(r[key]) for r in data[360:]) for data in (a,b))
        totals[key]=dict(baseline=x,candidate=y,delta=y-x,delta_percent=100*(y/x-1))
    v=module('f10_vectors',HERE/'phase7_validate.py')
    changes=[]
    paths=sorted((prior/'gpuvec').glob('*.vec'))
    assert len(paths)==8
    for path in paths:
        old,new=(v.v.vector(p.read_bytes()) for p in (path,current/'gpuvec'/path.name))
        assert canonical(old)[0]==canonical(new)[0],(path.name,'command/MMIO work order changed')
        assert all(old[k]==new[k] for k in ('depth','fence','uploads'))
        assert len(old['texture'])==len(new['texture'])
        diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())
               for i in range(0,len(old['texture']),4) if old['texture'][i:i+4]!=new['texture'][i:i+4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]),texture_changes=diffs,
            baseline_sha256=sha(path),candidate_sha256=sha(current/'gpuvec'/path.name)))
    result=dict(passed=True,all721_scene_geometry_culling_exact=exact,all721_command_word_counts_exact=True,
        all8_command_and_mmio_order_exact_except_CMDS_boundaries=True,texture_flushes_preserved=True,
        modeled_gameplay_frames360_through720=totals,
        vectors=changes,padding_check=v.padding(changes,prior,current,DEST/'SM64'),
        snapshots=v.v.snapshot_checks(current),normal_elf_identical_to_candidate=sha(DEST/'app.elf')==sha(BASE/'app.elf'),
        baseline_capture_sha256=sha(prior/'inputs.json'),candidate_capture_sha256=sha(current/'inputs.json'),
        diagnostic_elf_sha256=sha(DEST/'diagnostic.elf'),recipe_sha256=sha(Path(__file__)),qualified=False,
        scope='Diagnostic correctness only; physical FPS and sustained25FPS unverified.')
    write(DEST/'analysis-diagnostic.json',result)
    print(json.dumps(result,indent=2))


def perf():
    m=module('f10_perf',HERE/'phase6_perf_tests.py')
    assert m.HARNESS.count('PERF F8 SEND')==1
    m.HARNESS=m.HARNESS.replace('PERF F8 SEND','PERF F10 KICK')
    m.BASE,m.CAND=DEST,DEST
    m.main()


def replay():
    m=module('f10_replay',HERE/'phase7_replay.py')
    m.e.OUT=OUT
    m.run(LABEL,'diagnostic',[360,600,720])


def panel():
    from PIL import Image
    v=module('f10_validation',HERE/'phase6_validate.py')
    adapter=module('f10_depth',HERE/'phase4_combined_validate.py')
    dest=DEST/'panel'
    dest.mkdir(exist_ok=False)
    cap=DEST/'capture-diagnostic-panel'
    verify_capture(cap,sha(DEST/'diagnostic.elf'))
    assert json.loads((cap/'inputs.json').read_text())['panel']
    snapshots=v.snapshot_checks(cap,panel=True)
    for name in ('scene.csv','geometry.csv','culling.csv'):
        assert sha(cap/name)==sha(PARENT/'capture-diagnostic-panel'/name)
    source,vec=cap/'gpuvec/frame_00640.vec',dest/'frame_00640.vec'
    adapter.with_depth(source,vec)
    exe=v.RTL/'obj/Vtb_gpu'
    assert sha(exe)==json.loads((v.RTL/'build.json').read_text())['simulator_sha256']
    cmd=[str(exe),str(vec),str(dest/'frame.bin'),'+memory_traffic=2','+tagged_tex=1']
    with (dest/'replay.log').open('x') as log:
        subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
    data=(dest/'frame.bin').read_bytes()
    assert len(data)==307212
    pix=struct.unpack_from('<76800H',data)
    im=Image.new('RGB',(320,240))
    im.putdata([(((p>>11)&31)*255//31,((p>>5)&63)*255//63,(p&31)*255//31) for p in pix])
    im.save(dest/'panel.png')
    write(dest/'result.json',dict(snapshot_checks=snapshots,command=cmd,
        capture_inputs_sha256=sha(cap/'inputs.json'),simulator_sha256=sha(exe),
        vector_sha256=sha(vec),output_sha256=sha(dest/'frame.bin'),image_sha256=sha(dest/'panel.png'),
        scope='F10 KICK marker, unchanged55-word version8 snapshot. All displayed counters simulated.'))
    print(dest/'panel.png')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','build','capture','analyze','perf','replay','panel'])
    p.add_argument('--mode',choices=['normal','diagnostic'],default='normal')
    p.add_argument('--panel',action='store_true')
    a=p.parse_args()
    if a.action=='build':build(a.mode)
    elif a.action=='capture':capture(a.panel)
    else:globals()[a.action]()
