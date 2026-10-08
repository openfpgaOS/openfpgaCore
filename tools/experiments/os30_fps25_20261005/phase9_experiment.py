#!/usr/bin/env python3
"""Private CPU-preparation experiments over retained F9; no publication actions.

Candidate authors supply all eight flat inputs before staging. Every candidate
gets a separate source/object tree; completed attempts are never overwritten.
"""
import argparse
import csv
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

from phase7_experiment import CAMP, ROOT, module, sha, simulator, verify_capture, write
from phase8_experiment import canonical

HERE = Path(__file__).resolve().parent
OUT = CAMP / 'phase9'
PARENT = CAMP / 'phase7/combined-v1'
INPUTS = json.loads((CAMP/'phase7/baseline.json').read_text())['inputs']


def directory(label):
    assert re.fullmatch(r'[a-z0-9][a-z0-9_-]*', label)
    path = OUT / label
    assert path.is_dir() and not path.is_symlink(), path
    return path


def parent_inputs():
    record = json.loads((PARENT/'build-normal.json').read_text())
    assert sha(PARENT/'app.elf') == record['elf_sha256']
    for name, relative in INPUTS.items():
        digest = record['inputs'][name]['sha256']
        assert sha(PARENT/name) == sha(PARENT/'SM64'/relative) == digest, name
    return record


def flat_inputs(dest):
    result = {}
    for name in INPUTS:
        path = dest/name
        assert path.is_file() and not path.is_symlink(), path
        result[name] = sha(path)
    return result


def prepare(label):
    dest = directory(label)
    parent = parent_inputs()
    inputs = flat_inputs(dest)
    stage = dest/'SM64'
    assert not stage.exists() and not stage.is_symlink(), 'Preserve any existing stage'
    assert not (dest/'harness-stage.json').exists()
    # Bind the exact producing helper now, even if later validation reporting
    # evolves. This snapshot remains evidence rather than an executable entry.
    recipe = dest/'harness-preparation-recipe.py'
    with recipe.open('x') as stream:
        stream.write(Path(__file__).read_text())
    subprocess.run(['cp','-a','--reflink=auto',str(PARENT/'SM64'),str(stage)],check=True)
    write(dest/'harness-stage.json',dict(
        parent='phase7/combined-v1/F9', parent_normal_elf_sha256=parent['elf_sha256'],
        parent_qualified_sha256=sha(CAMP/'phase7/QUALIFIED.json'),
        parent_build_sha256=sha(PARENT/'build-normal.json'),
        parent_source_sha256={name:parent['inputs'][name]['sha256'] for name in INPUTS},
        candidate_source_sha256=inputs,
        preparation_recipe_sha256=sha(recipe), production_changed=False))
    print(stage)


def archive_failed_build(dest, mode):
    record = dest/f'build-{mode}.json'
    assert record.is_file() and not record.is_symlink(), 'No failed build record to retry'
    failure = json.loads(record.read_text())
    assert isinstance(failure.get('returncode'),int) and failure['returncode'] != 0, 'Never archive a successful build'
    elf = dest/('app.elf' if mode == 'normal' else 'diagnostic.elf')
    assert not elf.exists() and not elf.is_symlink(), 'An ELF exists; preserve it and use a new label'
    names = [f'build-{mode}.json',f'build-{mode}.log']
    names += [name for name in (f'build-{mode}-invocation.json',f'build-{mode}-recipe.py')
              if (dest/name).exists()]
    for name in names:
        assert (dest/name).is_file() and not (dest/name).is_symlink(), name
    archive_root = dest/f'failed-build-{mode}'
    assert not archive_root.is_symlink()
    archive_root.mkdir(exist_ok=True)
    attempt = 1
    while (archive_root/f'{attempt:03d}').exists():
        attempt += 1
    archive = archive_root/f'{attempt:03d}'
    archive.mkdir()
    hashes = {name:sha(dest/name) for name in names}
    for name in names:
        shutil.move(str(dest/name),str(archive/name))
        assert sha(archive/name) == hashes[name]
    write(archive/'archive.json',dict(failed_returncode=failure['returncode'],
        archived_sha256=hashes,source_inputs_unchanged=True,no_candidate_elf_present=True,
        retry_helper_sha256=sha(Path(__file__))))


def build(label, mode, retry_failed=False):
    dest = directory(label)
    if not (dest/'SM64').exists():
        prepare(label)
    binding = json.loads((dest/'harness-stage.json').read_text())
    assert flat_inputs(dest) == binding['candidate_source_sha256'], 'Use a new label for source revisions'
    assert sha(dest/'harness-preparation-recipe.py') == binding['preparation_recipe_sha256']
    if retry_failed:
        archive_failed_build(dest,mode)
    assert not (dest/f'build-{mode}.json').exists(), 'Keep prior build evidence; use --retry-failed-build only for failures'
    diagnostic = mode == 'diagnostic'
    recipe = dest/f'build-{mode}-recipe.py'
    with recipe.open('x') as stream:
        stream.write(Path(__file__).read_text())
    write(dest/f'build-{mode}-invocation.json',dict(
        recipe_sha256=sha(recipe),builder_sha256=sha(HERE/'build.py'),
        stage_binding_sha256=sha(dest/'harness-stage.json'),
        candidate_source_sha256=flat_inputs(dest),mode=mode,retry_failed_build=retry_failed))
    b = module('phase9_builder', HERE/'build.py')
    b.OUT, b.STAGE, b.INPUTS = OUT, dest/'SM64', INPUTS
    objects = b.STAGE/'.obj'/('sm64-perfdiag' if diagnostic else 'sm64')
    assert objects.parent == dest/'SM64/.obj' and not objects.is_symlink()
    if objects.exists():
        shutil.rmtree(objects)
    sys.argv = [str(Path(__file__)),label,'--diagnostic',str(int(diagnostic))]
    b.main()


def capture(label, mode, panel=False):
    dest = directory(label)
    assert not panel or mode == 'diagnostic'
    build_record = json.loads((dest/f'build-{mode}.json').read_text())
    elf = dest/('diagnostic.elf' if mode == 'diagnostic' else 'app.elf')
    assert sha(elf) == build_record['elf_sha256']
    assert flat_inputs(dest) == {n:v['sha256'] for n,v in build_record['inputs'].items()}
    m = module('phase9_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT = dest
    sys.argv = [str(Path(__file__)),mode + ('-panel' if panel else ''),
                '--elf',str(elf),'--exe',str(simulator()),'--geometry','0','--audio']
    if panel:
        sys.argv += ['--panel','--vectors','640']
    m.main()


def analyze(label, mode):
    dest = directory(label)
    before, current = PARENT/('capture-'+mode), dest/('capture-'+mode)
    filename = 'app.elf' if mode == 'normal' else 'diagnostic.elf'
    a = verify_capture(before,sha(PARENT/filename))
    b = verify_capture(current,sha(dest/filename))
    assert len(a) == len(b) == 721
    exact = {name:sha(before/name) == sha(current/name)
             for name in ('scene.csv','geometry.csv','culling.csv')}
    assert all(exact.values()), 'Scene or geometry behavior changed'
    assert [r['gpu_cmd_words'] for r in a] == [r['gpu_cmd_words'] for r in b]
    totals = {}
    for key in ('insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict'):
        x,y = (sum(int(row[key]) for row in data[360:]) for data in (a,b))
        totals[key] = dict(baseline=x,candidate=y,delta=y-x,saving_percent=100*(1-y/x))
    windows = []
    for lo,hi in ((360,480),(480,600),(600,721)):
        x,y = (sum(int(row['est_cycles']) for row in data[lo:hi]) for data in (a,b))
        windows.append(dict(first=lo,last=hi-1,baseline=x,candidate=y,saving_percent=100*(1-y/x)))
    v = module('phase9_vector_validation',HERE/'phase7_validate.py')
    paths = sorted((before/'gpuvec').glob('*.vec'))
    assert len(paths) == 8
    changes = []
    for path in paths:
        target = current/'gpuvec'/path.name
        old,new = (v.v.vector(p.read_bytes()) for p in (path,target))
        assert canonical(old)[0] == canonical(new)[0], (path.name,'GPU command/MMIO order changed')
        assert all(old[k] == new[k] for k in ('depth','fence','uploads'))
        assert len(old['texture']) == len(new['texture'])
        diffs = [dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())
                 for i in range(0,len(old['texture']),4) if old['texture'][i:i+4] != new['texture'][i:i+4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]),texture_changes=diffs,
            publication_boundaries_exact=old['hardware'] == new['hardware'],
            baseline_sha256=sha(path),candidate_sha256=sha(target)))
    result = dict(candidate=label,mode=mode,passed=True,
        scene_geometry_culling_exact=exact,all721_command_word_counts_exact=True,
        all8_ordered_command_mmio_streams_exact_except_CMDS_boundaries=True,
        all8_publication_boundaries_exact=all(r['publication_boundaries_exact'] for r in changes),
        texture_flush_order_preserved=True,vectors=changes,
        padding_check=v.padding(changes,before,current,dest/'SM64'),
        gameplay_frames360_through720=totals,windows=windows,
        baseline_capture_sha256=sha(before/'inputs.json'),candidate_capture_sha256=sha(current/'inputs.json'),
        candidate_source_sha256=flat_inputs(dest),harness_sha256=sha(Path(__file__)),
        canonical_helper_sha256=sha(HERE/'phase8_experiment.py'),
        qualified_for_deployment=False,
        scope='Eager GPU checks work and modeled CPU cost. Physical FPS, real CPU memory contention and live GPU timing require separate evidence.')
    if mode == 'diagnostic':
        result['snapshots'] = v.v.snapshot_checks(current)
    write(dest/f'analysis-{mode}.json',result)
    print(json.dumps(dict(candidate=label,mode=mode,passed=True,totals=totals,windows=windows),indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','build','capture','analyze'])
    p.add_argument('candidate')
    p.add_argument('--mode',choices=['normal','diagnostic'],default='normal')
    p.add_argument('--panel',action='store_true')
    p.add_argument('--retry-failed-build',action='store_true',
                   help='Archive a recorded failed build with no ELF, then rebuild the same frozen candidate')
    a = p.parse_args()
    assert not a.retry_failed_build or a.action == 'build', '--retry-failed-build only applies to build'
    if a.action == 'prepare':prepare(a.candidate)
    elif a.action == 'build':build(a.candidate,a.mode,a.retry_failed_build)
    elif a.action == 'capture':capture(a.candidate,a.mode,a.panel)
    else:analyze(a.candidate,a.mode)
