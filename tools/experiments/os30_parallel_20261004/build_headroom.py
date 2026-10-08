#!/usr/bin/env python3
"""Freeze a counterfactual CPU-cost experiment with unchanged os30 GPU RTL."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'docs/measurements/os30-parallel-20261004'
PRIOR = ROOT / 'docs/measurements/os30-frontend-20261004'
SOURCE = PRIOR / 'attributes-v6'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def replace(text, old, new):
    assert text.count(old) == 1, (old[:100], text.count(old))
    return text.replace(old, new, 1)


def main():
    dest = OUT / 'headroom'
    dest.mkdir(parents=True, exist_ok=False)
    q = dest / 'qsim'
    q.mkdir()
    for p in (PRIOR / 'qsim').iterdir():
        if p.suffix in ('.c', '.h') or p.name == 'Makefile':
            shutil.copy2(p, q / p.name)
    for name in ['headroom.c', 'headroom.h']:
        shutil.copy2(HERE/name, q/name)
    p = q / 'cpu.c'
    p.write_text('#include "headroom.h"\n' + p.read_text())
    p = q / 'cpu_exec.h'
    p.write_text(replace(p.read_text(), '        size_t _i = (size_t)(e - arena);',
                        '        headroom_instruction(m->cyc, e->pc); \\\n        size_t _i = (size_t)(e - arena);'))
    manifest = json.loads((SOURCE/'manifest.json').read_text())
    for name, digest in manifest['sources'].items():
        assert sha(SOURCE/name) == digest, name
    shutil.copytree(SOURCE/'frozen', dest/'frozen')
    p = dest/'frozen/coupled.cpp'
    s = replace(p.read_text(), '#include "coupled.h"', '#include "coupled.h"\n#include "headroom.h"')
    s = replace(s, 'static uint64_t cpu_time() { return env->cpu->model->cyc-cpu_origin; }',
                'static uint64_t cpu_time() { return env->cpu->model->cyc-cpu_origin'
                '-headroom_credit(env->cpu->model->cyc); }')
    s = replace(s, 'initializing=false;active=true;snapshot("start",0);',
                'headroom_begin(E, out);\n    initializing=false;active=true;snapshot("start",0);')
    s = replace(s, '    fflush(events);fflush(timeline);fflush(publish_log);',
                '    headroom_finish(env->cpu->model->cyc);\n    fflush(events);fflush(timeline);fflush(publish_log);')
    p.write_text(s)
    names = 'cpu fpu cache prof qelf lint gpu app env main headroom'.split()
    with (dest/'build-qsim.log').open('w') as log:
        subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in names]], cwd=q,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    cmd = [a.replace(str(SOURCE), str(dest)).replace(str(PRIOR/'qsim'), str(q))
           for a in manifest['command']]
    cmd[cmd.index('-LDFLAGS')+1] = ' '.join(str(q/'build/qsim'/f'{n}.o') for n in names)+' -lm'
    # Observe the current triangle-setup phase as well as the existing counters.
    p = dest/'frozen/tb_gpu.v'
    s = replace(p.read_text(), '    output wire overlap_raster_active,',
                '    output wire parallel_setup,\n    output wire overlap_raster_active,')
    s = replace(s, '// Simulation-only observations.',
                'assign parallel_setup = gpu.state == gpu.S_TRI_DERIVE;\n// Simulation-only observations.')
    p.write_text(s)
    p = dest/'frozen/coupled.cpp'
    s = replace(p.read_text(), 'static uint32_t max_upload, max_output;',
                'static uint32_t max_upload, max_output;\nstatic uint64_t setup_cycles;')
    s = replace(s, '    tb->clk=1; tb->eval(); cycles++;',
                '    if (!initializing && tb->parallel_setup) setup_cycles++;\n'
                '    tb->clk=1; tb->eval(); cycles++;')
    s = replace(s, '    headroom_finish(env->cpu->model->cyc);',
                '    FILE *setup=fopen((folder+"/setup-cycles.txt").c_str(),"w");assert(setup);\n'
                '    fprintf(setup,"%llu\\n",(unsigned long long)setup_cycles);fclose(setup);\n'
                '    headroom_finish(env->cpu->model->cyc);')
    p.write_text(s)
    manifest.update(command=cmd, scope=__doc__, counterfactual=True,
                    sources={str(p.relative_to(dest)):sha(p)
                             for d in ['frozen','qsim'] for p in (dest/d).rglob('*')
                             if p.is_file() and 'build' not in p.parts})
    (dest/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    with (dest/'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(dest/'obj/Vtb_gpu')


if __name__ == '__main__': main()
