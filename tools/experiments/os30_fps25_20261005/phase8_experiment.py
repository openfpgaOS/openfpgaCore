#!/usr/bin/env python3
"""Private F9 command-publication experiments; never publish or deploy.

Eager CPU captures establish command equivalence and modeled CPU cost only.
The independent current-A coupled harness measures scheduling separately.
"""
import argparse
import csv
import difflib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from phase7_experiment import CAMP, ROOT, module, sha, simulator, verify_capture, write

OUT = CAMP / 'phase8'
PARENT = CAMP / 'phase7/combined-v1'
INPUTS = json.loads((CAMP / 'phase7/baseline.json').read_text())['inputs']
LABELS = ('kick128', 'kick128-tail')


def prepare(label):
    parent_build = json.loads((PARENT / 'build-normal.json').read_text())
    assert sha(PARENT / 'app.elf') == parent_build['elf_sha256']
    expected = parent_build['inputs']
    directory = OUT / label
    directory.mkdir(parents=True, exist_ok=False)
    before, after, patches = {}, {}, []
    for name, relative in INPUTS.items():
        original = (PARENT / name).read_text()
        assert sha(PARENT / name) == expected[name]['sha256']
        assert sha(PARENT / 'SM64' / relative) == expected[name]['sha256']
        candidate = original
        if name == 'gfx_gpu.c':
            # App-local override: no SDK default change for other games.
            anchor = '#include "of_gpu.h"'
            assert candidate.count(anchor) == 1
            candidate = candidate.replace(anchor,
                '/* Experiment: publish smaller complete CPU command batches. */\n'
                '#define OF_GPU_KICK_MIN_WORDS 128u\n' + anchor)
            if label == 'kick128-tail':
                anchor = '''        of_gpu_kick();
    }
    }
}

/* Raw geometry path.'''
                assert candidate.count(anchor) == 1
                candidate = candidate.replace(anchor, '''        of_gpu_kick();
    }
    }
    /* Completed indexed run: publish ready commands before the next display
     * list/vertex preparation. No packet cursor remains live here. */
    of_gpu_kick();
}

/* Raw geometry path.''')
        target = directory / name
        target.write_text(candidate)
        before[name], after[name] = sha(PARENT / name), sha(target)
        if candidate != original:
            patches.extend(difflib.unified_diff(original.splitlines(True), candidate.splitlines(True),
                fromfile='F9/' + name, tofile=label + '/' + name))
    (directory / 'candidate.patch').write_text(''.join(patches))
    subprocess.run(['cp', '-a', '--reflink=auto', str(PARENT / 'SM64'), str(directory / 'SM64')], check=True)
    write(directory / 'prepared.json', dict(
        parent='phase7/combined-v1/F9', parent_qualified_sha256=sha(CAMP/'phase7/QUALIFIED.json'),
        parent_normal_elf_sha256=parent_build['elf_sha256'],
        parent_source_sha256=before, candidate_source_sha256=after,
        recipe_sha256=sha(Path(__file__)), changes=label,
        scope='Publication schedule only; command contents/order and ownership/fences unchanged.',
        qualified=False, production_changed=False))
    print(directory)


def build(label):
    directory = OUT / label
    assert not (directory / 'build-normal.json').exists()
    builder = module('publication_builder', Path(__file__).with_name('build.py'))
    builder.OUT, builder.STAGE, builder.INPUTS = OUT, directory / 'SM64', INPUTS
    objects = builder.STAGE / '.obj/sm64'
    assert objects.parent == directory / 'SM64/.obj' and not objects.is_symlink()
    if objects.exists():
        shutil.rmtree(objects)
    sys.argv = [str(Path(__file__)), label, '--diagnostic', '0']
    builder.main()


def capture(label):
    m = module('publication_capture', ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT = OUT / label
    sys.argv = [str(Path(__file__)), 'normal', '--elf', str(OUT/label/'app.elf'),
                '--exe', str(simulator()), '--geometry', '0', '--audio']
    m.main()


def canonical(vector):
    """Ignore publication boundaries, preserving every command/MMIO event order."""
    output, batches = [], []
    for raw in vector['hardware']:
        if raw[:4] == b'CMDS':
            count, = struct.unpack_from('<I', raw, 4)
            batches.append(count)
            pos = 8
            while pos < len(raw):
                header, = struct.unpack_from('<I', raw, pos)
                end = pos + 4 * (1 + (header & 8191))
                output.append(b'CMD1' + raw[pos:end])
                pos = end
            assert pos == len(raw) == 8 + 4*count
        else:
            output.append(raw)
    return output, batches


def analyze(label):
    directory = OUT / label
    baseline, current = PARENT/'capture-normal', directory/'capture-normal'
    a = verify_capture(baseline, sha(PARENT/'app.elf'))
    b = verify_capture(current, sha(directory/'app.elf'))
    assert len(a) == len(b) == 721
    exact = {name: sha(baseline/name) == sha(current/name)
             for name in ('scene.csv', 'geometry.csv', 'culling.csv')}
    exact_words = [r['gpu_cmd_words'] for r in a] == [r['gpu_cmd_words'] for r in b]
    totals = {}
    for key in ('insns', 'est_cycles', 'gpu_cmd_words', 'icache_miss_sdram', 'dcache_miss', 'mispredict'):
        x, y = (sum(int(r[key]) for r in data[360:]) for data in (a, b))
        totals[key] = dict(baseline=x, candidate=y, delta=y-x, saving_percent=100*(1-y/x))
    windows = []
    for lo, hi in ((360,480), (480,600), (600,721)):
        x, y = (sum(int(r['est_cycles']) for r in data[lo:hi]) for data in (a,b))
        windows.append(dict(first=lo, last=hi-1, baseline=x, candidate=y, saving_percent=100*(1-y/x)))
    validator = module('publication_vectors', Path(__file__).with_name('phase7_validate.py'))
    changes = []
    vectors = sorted((baseline/'gpuvec').glob('*.vec'))
    assert len(vectors) == 8
    for path in vectors:
        old, new = (validator.v.vector(p.read_bytes()) for p in (path,current/'gpuvec'/path.name))
        old_work, old_batches = canonical(old)
        new_work, new_batches = canonical(new)
        assert old_work == new_work, (path.name, 'command/MMIO work or order changed')
        assert all(old[k] == new[k] for k in ('depth','fence','uploads'))
        assert len(old['texture']) == len(new['texture'])
        differences = [dict(offset=i, before=old['texture'][i:i+4].hex(), after=new['texture'][i:i+4].hex())
                       for i in range(0,len(old['texture']),4) if old['texture'][i:i+4] != new['texture'][i:i+4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]), texture_changes=differences,
                            baseline_batches=old_batches, candidate_batches=new_batches,
                            baseline_sha256=sha(path), candidate_sha256=sha(current/'gpuvec'/path.name)))
    result = dict(candidate=label, scene_geometry_culling_exact=exact,
                  all721_command_word_counts_exact=exact_words,
                  all8_ordered_command_mmio_streams_exact_except_publication_boundaries=True,
                  vectors=changes, padding_check=validator.padding(changes,baseline,current,directory/'SM64'),
                  gameplay_frames360_through720=totals, windows=windows,
                  baseline_capture_sha256=sha(baseline/'inputs.json'), candidate_capture_sha256=sha(current/'inputs.json'),
                  source_sha256={n:sha(directory/n) for n in INPUTS}, qualified=False,
                  scope='Eager GPU verifies work and modeled CPU cost; independent coupled GPU timing required for scheduling.')
    assert all(exact.values()) and exact_words
    write(directory/'analysis-normal.json',result)
    print(json.dumps(dict(candidate=label, passed=True, totals=totals, windows=windows),indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','build','capture','analyze'])
    p.add_argument('candidate',choices=LABELS)
    args = p.parse_args()
    globals()[args.action](args.candidate)
