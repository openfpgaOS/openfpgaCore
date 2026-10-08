#!/usr/bin/env python3
"""Freeze and build the mesh GPU/physical SDRAM + estimated CPU timing model.

The installed attributes-v6 executable is preserved for paired comparison.
CPU cache/instruction latency is estimated, external memory traffic synthetic;
GPU arithmetic, queues, DMA, rasterization, fences and SDRAM timing are RTL.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = ROOT / 'docs/measurements/os30-mesh-20261004'
BASE = OUT / 'whole-gpu-attr-v1/build'
QBASE = ROOT / 'docs/measurements/os30-frontend-20261004/qsim'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def rep(s, old, new):
    assert s.count(old) == 1, (old[:100], s.count(old))
    return s.replace(old, new, 1)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--label', default='coupled-v1')
    parser.add_argument('--queue-bits', type=int, default=10)
    parser.add_argument('--dcache-kib', type=int, choices=[64,128], default=128)
    parser.add_argument('--texture-queue-m10k', action='store_true')
    a = parser.parse_args()
    dest = OUT / a.label
    dest.mkdir(exist_ok=False)
    q = dest / 'qsim'
    q.mkdir()
    for p in QBASE.iterdir():
        if p.suffix in ('.c', '.h') or p.name == 'Makefile':
            shutil.copy2(p, q / p.name)
    p = q / 'gpu.c'
    p.write_text(rep(p.read_text(), '(getenv("SM64_ATTRIBUTES_CAP") ? 0x1000u : 0)',
        '(getenv("SM64_ATTRIBUTES_CAP") ? 0x1000u : 0)\n'
        '             | (getenv("SM64_MESH_CAP") ? 0x2000u : 0)'))
    p = q / 'env.c'
    p.write_text(rep(p.read_text(), '0x2913775bu',
        '(getenv("SM64_MESH_CAP") ? (0x2913775bu & ~((1u<<20)|(1u<<22)|(1u<<26)|(1u<<29))) : 0x2913775bu)'))
    if a.dcache_kib==64:
        p=q/'cache.c';p.write_text(rep(p.read_text(),'p->dc_sets = 1024;','p->dc_sets = 512;'))
    names = 'cpu fpu cache prof qelf lint gpu app env main'.split()
    with (dest / 'build-qsim.log').open('w') as log:
        subprocess.run(['make', '-j3', *[f'build/qsim/{n}.o' for n in names]],
                       cwd=q, stdout=log, stderr=subprocess.STDOUT, check=True)
    original = json.loads((BASE / 'manifest.json').read_text())
    for rel, digest in original['sources'].items():
        assert sha(BASE / rel) == digest, rel
    shutil.copytree(BASE / 'frozen', dest / 'frozen')
    spec = importlib.util.spec_from_file_location('mesh_prepare', HERE.parent / 'prepare.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.stage(dest / 'rtl', compact=1, enable_dot=0, extended_isa=0,
                  code_pages=2, queue_bits=a.queue_bits)
    for p in (dest / 'rtl').glob('*.v'):
        if p.name != 'core_top.v':
            shutil.copy2(p, dest / 'frozen/common' / p.name)
    top = dest / 'frozen/tb_gpu.v'
    s = rep(top.read_text(), '    output wire        busy,',
        '    output wire [2:0] overlap_worker_kind,\n'
        '    output wire overlap_raster_active, overlap_fragment, overlap_fragment_stall,\n'
        '    output wire overlap_write_take,\n'
        '    output wire [11:0] overlap_upload_words, overlap_output_words,\n'
        '    output wire        busy,')
    pos = s.rindex('endmodule')
    top.write_text(s[:pos] + (HERE / 'coupled_probes.v').read_text() + '\n' + s[pos:])
    shutil.copy2(HERE / 'coupled.cpp', dest / 'frozen/coupled.cpp')
    cmd = [v.replace(str(BASE), str(dest)) for v in original['command']]
    cmd[cmd.index('-CFLAGS') + 1] += ' -I' + str(q)
    cmd = [v.replace('/frozen/replay.cpp', '/frozen/coupled.cpp') for v in cmd]
    if a.texture_queue_m10k:
        cmd=[v for v in cmd if v!='+define+INCLUDE_TEX_QUEUE_MLAB']
    for source in sorted((dest/'frozen/common').glob('gpu_mesh*.v')):
        if str(source) not in cmd:cmd.append(str(source))
    cmd += ['-LDFLAGS', ' '.join(str(q / 'build/qsim' / f'{n}.o') for n in names) + ' -lm']
    manifest = dict(scope=__doc__, command=cmd, fifo_words=1 << a.queue_bits,
                    code_pages=2, cpu_data_cache_kib=a.dcache_kib,
                    texture_queue_m10k=a.texture_queue_m10k,
                    sources={str(p.relative_to(dest)): sha(p) for folder in ('frozen', 'qsim')
                             for p in (dest / folder).rglob('*')
                             if p.is_file() and 'build' not in p.relative_to(dest / folder).parts})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (dest / 'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(dest / 'obj/Vtb_gpu', flush=True)

if __name__ == '__main__':
    main()
