#!/usr/bin/env python3
"""Couple the retained SM64 CPU model to the installed os30 GPU/SDRAM RTL.

The game clock stays at one fixed step per submitted frame, keeping the route
comparable. GPU MMIO, queue pressure, fences and 60 Hz presentation run in RTL
time. CPU caches/latencies are estimates; scanout/audio/CPU bus traffic remains
the retained synthetic sensitivity schedule. This is not a CPU RTL simulation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'docs/measurements/os30-frontend-20261004'
BASE = ROOT / 'docs/measurements/os30-worker-20261003/replay-worker-v6'
QBASE = ROOT / 'docs/measurements/sm64-vertex-perf-20261004/qsim'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()

def rep(s, old, new):
    assert s.count(old) == 1, (old[:100], s.count(old))
    return s.replace(old, new, 1)

def prepare_qsim():
    q = OUT / 'qsim'
    q.mkdir(parents=True, exist_ok=False)
    for p in QBASE.iterdir():
        if p.suffix in ('.c', '.h') or p.name == 'Makefile':
            shutil.copy2(p, q / p.name)
    shutil.copy2(HERE / 'coupled.h', q / 'coupled.h')
    s = (q / 'env.c').read_text()
    s = rep(s, '#include "env.h"', '#include "env.h"\n#include "coupled.h"')
    s = rep(s, '    E->hw_display = idx & 3;', '    if (!coupled_active()) E->hw_display = idx & 3;')
    s = rep(s, '    E->flip_pub[E->nflip_pub].token = p[1];',
            '    coupled_submit(p[0], p[1], rd32(E, E->host_framecount_addr));\n'
            '    E->flip_pub[E->nflip_pub].token = p[1];')
    s = rep(s, '        if (E->gpu) v = gpu_mmio_read(E->gpu, off & ~3u);',
            '        if (coupled_active()) v = coupled_read(off & ~3u);\n'
            '        else if (E->gpu) v = gpu_mmio_read(E->gpu, off & ~3u);')
    s = rep(s, '        if (E->gpu) {\n            if (E->vec_nframes)',
            '        if (coupled_active()) coupled_write(off, val);\n'
            '        if (E->gpu) {\n            if (E->vec_nframes)')
    s = rep(s, '    E->svc_calls[i]++;', '    coupled_sync();\n    E->svc_calls[i]++;')
    s = rep(s, '        int just = (int32_t)a0;',
            '        if (coupled_active()) { r0 = coupled_acquire((int32_t)a0, a1); break; }\n'
            '        int just = (int32_t)a0;')
    s = rep(s, '    case 92: { /* video timing, functional only */',
            '    case 92: { /* video timing */\n'
            '        if (coupled_active()) { coupled_video_timing(a0); break; }')
    s = rep(s, 'void env_free(qenv *E)\n{', 'void env_free(qenv *E)\n{\n    coupled_close();')
    (q / 'env.c').write_text(s)
    s = (q / 'main.c').read_text()
    s = rep(s, '#include "env.h"', '#include "env.h"\n#include "coupled.h"')
    s = rep(s, '    for (;;) {\n        uint64_t left = max_insns',
            '    for (;;) {\n        coupled_start(&E, out);\n        uint64_t left = max_insns')
    s = rep(s, '    clock_gettime(CLOCK_MONOTONIC, &t1);\n    double wall',
            '    coupled_finish();\n    clock_gettime(CLOCK_MONOTONIC, &t1);\n    double wall')
    (q / 'main.c').write_text(s)
    s = (q / 'cpu.c').read_text()
    (q / 'cpu.c').write_text('#include "coupled.h"\n' + s)
    s = (q / 'cpu_exec.h').read_text()
    s = rep(s, '        size_t _ti = (size_t)((te) - arena);',
            '        if ((tpc) == coupled_render_pc) coupled_render_enter(); \\\n        size_t _ti = (size_t)((te) - arena);')
    s = rep(s, '            memcpy(sd_uc + _o, &_t, sizeof _t);',
            '            coupled_sync(); \\\n            memcpy(sd_uc + _o, &_t, sizeof _t);')
    (q / 'cpu_exec.h').write_text(s)
    # Software GPU keeps its original memory pointer. The CPU switches to the
    # RTL memory only between interpreter runs. Its functional GPU is retained
    # solely for command/frame accounting; it cannot write RTL framebuffer RAM.
    s=(q/'gpu.c').read_text()
    s=rep(s, '(getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)',
          '(getenv("SM64_GEOMETRY_CAP") ? 0x400u : 0)\n             | (getenv("SM64_WORKER_CAP") ? 0x800u : 0)\n             | (getenv("SM64_ATTRIBUTES_CAP") ? 0x1000u : 0)')
    (q/'gpu.c').write_text(s)
    names = 'cpu fpu cache prof qelf lint gpu app env main'.split()
    cmd = ['make', '-j3', *[f'build/qsim/{n}.o' for n in names]]
    with (OUT / 'build-qsim.log').open('w') as log:
        subprocess.run(cmd, cwd=q, stdout=log, stderr=subprocess.STDOUT, check=True)
    (OUT / 'qsim-sources.json').write_text(json.dumps({p.name:sha(p) for p in q.iterdir()
        if p.is_file()}, indent=2)+'\n')
    return q, names

def build(kind, q, names):
    dest = OUT / kind
    dest.mkdir(exist_ok=False)
    manifest = json.loads((BASE / 'manifest.json').read_text())
    for rel, digest in manifest['sources'].items():
        assert sha(BASE / rel) == digest, rel
    shutil.copytree(BASE / 'frozen', dest / 'frozen')
    for p in (dest/'frozen').rglob('*.v'):
        staged=OUT/'rtl'/p.name
        if staged.exists():shutil.copy2(staged,p)
    top = dest / 'frozen/tb_gpu.v'
    s = top.read_text()
    s = rep(s, '    output wire        busy,', '''    output wire [2:0] overlap_worker_kind,
    output wire overlap_raster_active, overlap_fragment, overlap_fragment_stall,
    output wire overlap_write_take,
    output wire [11:0] overlap_upload_words, overlap_output_words,
    output wire        busy,''')
    pos = s.rindex('endmodule')
    s = s[:pos] + (HERE / 'probes.v').read_text() + '\n' + s[pos:]
    top.write_text(s)
    shutil.copy2(HERE / 'coupled.cpp', dest / 'frozen/coupled.cpp')
    cmd = [v.replace(str(BASE), str(dest)) for v in manifest['command']]
    cmd[cmd.index('-CFLAGS')+1] += ' -I'+str(q)
    cmd = [v.replace('/frozen/replay.cpp', '/frozen/coupled.cpp') for v in cmd]
    cmd += ['-LDFLAGS', ' '.join(str(q / 'build/qsim' / f'{n}.o') for n in names)+' -lm']
    if kind.startswith('attributes'):cmd.append('+define+INCLUDE_GPU_VERTEX_ATTRIBUTES')
    if kind == 'direct':
        cmd = [v for v in cmd if v not in ('+define+INCLUDE_GPU_GEOMETRY_WORKER', '+define+EXCLUDE_GPU_XFORM_MAC')]
        cmd[cmd.index('-GINCLUDE_GPU_XFORM_MAC=0')] = '-GINCLUDE_GPU_XFORM_MAC=1'
    manifest.update(scope=__doc__, kind=kind, command=cmd, sources={str(p.relative_to(dest)):sha(p)
        for p in (dest/'frozen').rglob('*') if p.is_file()})
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    with (dest/'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(dest/'obj/Vtb_gpu', flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=['worker','attributes-v2','attributes-v3','attributes-v4','attributes-v5','attributes-v6','attributes-v7','attributes-v8'])
    a=p.parse_args()
    if not (OUT/'qsim').exists(): q,names=prepare_qsim()
    else: q,names=OUT/'qsim','cpu fpu cache prof qelf lint gpu app env main'.split()
    build(a.kind,q,names)

if __name__=='__main__': main()
