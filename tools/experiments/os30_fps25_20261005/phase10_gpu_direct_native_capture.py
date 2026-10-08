#!/usr/bin/env python3
"""Fresh native-FMA functional baseline/candidate proof; no DMA timing claim."""
import argparse
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from elftools.elf.elffile import ELFFile
from phase10_experiment import CAMP, ROOT, module, sha, write, verify_capture, canonical

HERE = Path(__file__).resolve().parent
OUT = CAMP / 'phase10/gpu-direct-native-functional-v1'
CANDIDATE = CAMP / 'phase10/gpu-direct-driver-v3'
BASELINE = CAMP / 'phase9/checks-state-f11'
FMA = CAMP / 'phase10/qsim-fma26-v1'


def prepare():
    OUT.mkdir(exist_ok=False)
    shutil.copy2(CANDIDATE / 'app.elf', OUT / 'app.elf')
    shutil.copy2(CANDIDATE / 'gfx_pc.c', OUT / 'gfx_pc.c')
    old = module('native_capture_prepare', HERE / 'phase10_gpu_direct_capture.py')
    old.OUT = OUT
    old.prepare()
    q = OUT / 'functional-qsim'
    for name in ('fpu.c', 'fpu_fast.h'):
        shutil.copy2(FMA / name, q / name)
    p = q / 'Makefile'
    p.write_text(p.read_text() + '\nCFLAGS += -DQSIM_RETAINED_FMA26\n')
    p = q / 'main.c'
    source = p.read_text()
    begin = source.index('    snprintf(path,sizeof path,"%s/gdr1-driver-stats.bin",out);')
    end = source.index('    env_free(&E);', begin)
    source = source[:begin] + '''    const char *gd_stats = getenv("GDR1_STATS_ADDR");
    if(gd_stats) {
        snprintf(path,sizeof path,"%s/gdr1-driver-stats.bin",out);
        FILE *lodstats=fopen(path,"wb");if(!lodstats)abort();
        uint32_t gd_address=(uint32_t)strtoul(gd_stats,NULL,0);
        fwrite(qsim_mem_ptr(E.cpu,gd_address,64),64,1,lodstats);fclose(lodstats);
    }
''' + source[end:]
    p.write_text(source)
    with (OUT / 'native-build.log').open('x') as log:
        subprocess.run(['make', 'clean'], cwd=q, stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(['make', '-j4'], cwd=q, stdout=log, stderr=subprocess.STDOUT, check=True)
    write(OUT / 'native-model.json', dict(
        baseline_elf_sha256=sha(BASELINE / 'app.elf'),
        candidate_elf_sha256=sha(OUT / 'app.elf'),
        qsim_sha256=sha(q / 'qsim'),
        source_sha256={p.name: sha(p) for p in q.iterdir() if p.suffix in ('.c', '.h') or p.name == 'Makefile'},
        arithmetic='Both CPU and functional GDR use validated retained FMA26 mode; multiply/add/div remain IEEE.',
        functional_only=True, worker_latency='Synthetic two-poll completion; not cycle-accurate DMA or accelerator performance',
        recipe_sha256=sha(Path(__file__)), fma_proof_sha256=sha(FMA / 'manifest.json')))


def capture(label):
    baseline = label == 'baseline'
    elf = BASELINE / 'app.elf' if baseline else OUT / 'app.elf'
    os.environ['GDR1_MODEL_MODE'] = 'absent' if baseline else 'faults' if label == 'faults' else 'normal'
    os.environ.pop('GDR1_STATS_ADDR', None)
    if not baseline:
        with elf.open('rb') as f:
            symbols = {s.name: s['st_value'] for s in ELFFile(f).get_section_by_name('.symtab').iter_symbols()}
        os.environ['GDR1_STATS_ADDR'] = hex(symbols['load_driver_stats'])
    m = module('native_capture_run', ROOT / 'tools/experiments/sm64_batch_cull_20261005/validation/capture.py')
    m.OUT = OUT
    sys.argv = [str(Path(__file__)), label, '--elf', str(elf), '--exe', str(OUT / 'functional-qsim/qsim'), '--geometry', '0', '--audio']
    m.main()


def analyze(label):
    before = OUT / 'capture-baseline'
    after = OUT / ('capture-' + label)
    a = verify_capture(before, sha(BASELINE / 'app.elf'))
    b = verify_capture(after, sha(OUT / 'app.elf'))
    assert len(a) == len(b) == 721
    exact = {n: sha(before / n) == sha(after / n) for n in ('scene.csv', 'geometry.csv', 'culling.csv')}
    assert all(exact.values()), exact
    assert [r['gpu_cmd_words'] for r in a] == [r['gpu_cmd_words'] for r in b]
    v = module('native_vector_validation', HERE / 'phase7_validate.py')
    changes = []
    for path in sorted((before / 'gpuvec').glob('*.vec')):
        target = after / 'gpuvec' / path.name
        old, new = (v.v.vector(p.read_bytes()) for p in (path, target))
        oc, nc = canonical(old)[0], canonical(new)[0]
        assert [x for x in oc if x[:4] == b'CMD1'] == [x for x in nc if x[:4] == b'CMD1'], path.name
        assert [x for x in oc if x[:4] != b'CMD1'] == [x for x in nc if x[:4] != b'CMD1'], path.name
        assert all(old[k] == new[k] for k in ('depth', 'fence', 'uploads')), path.name
        assert len(old['texture']) == len(new['texture'])
        diffs = [dict(offset=i, before=old['texture'][i:i+4].hex(), after=new['texture'][i:i+4].hex())
                 for i in range(0, len(old['texture']), 4) if old['texture'][i:i+4] != new['texture'][i:i+4]]
        changes.append(dict(frame=int(path.stem.split('_')[1]), texture_changes=diffs,
                            publication_boundaries_exact=old['hardware'] == new['hardware'],
                            combined_command_mmio_order_exact=oc == nc,
                            baseline_sha256=sha(path), candidate_sha256=sha(target)))
    assert len(changes) == 8
    hashes = {k: all(x[k] == y[k] for x, y in zip(a, b)) for k in ('hash_px', 'hash_z')}
    assert all(hashes.values()), hashes
    result = dict(passed=True, mode=label, functional_only=True,
        all721_scene_geometry_culling_exact=exact, all721_wordcounts_exact=True,
        all8_individual_gpu_and_mmio_streams_exact=True, vectors=changes,
        padding_check=v.padding(changes, before, after, CANDIDATE / 'SM64'),
        model=json.loads((after / 'gdr1-model.json').read_text()),
        driver_stats=list(struct.unpack('<16I', (after / 'gdr1-driver-stats.bin').read_bytes())),
        all721_qsim_color_depth_hashes_exact=hashes,
        baseline_capture_sha256=sha(before / 'inputs.json'), candidate_capture_sha256=sha(after / 'inputs.json'),
        native_model_sha256=sha(OUT / 'native-model.json'), qualified_for_deployment=False,
        scope='Fresh same-simulator native-FMA baseline/candidate functional equivalence only. Synthetic worker completion; no FPS or real DMA latency inference.')
    write(OUT / ('result-' + label + '.json'), result)
    print(json.dumps({k: result[k] for k in ('passed', 'mode', 'model', 'driver_stats')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'capture', 'analyze'))
    parser.add_argument('--label', choices=('baseline', 'normal', 'faults'), default='normal')
    args = parser.parse_args()
    if args.action == 'prepare': prepare()
    elif args.action == 'capture': capture(args.label)
    else: analyze(args.label)
