#!/usr/bin/env python3
"""Freeze and check private raw-vertex RTL. Never publishes a device image."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
CAMP = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage', 'simulate', 'fit-report'])
    parser.add_argument('name')
    parser.add_argument('--frontend', action='store_true')
    parser.add_argument('--held-context', action='store_true')
    args = parser.parse_args()
    if Path(args.name).name != args.name:
        parser.error('name must be a single directory component')
    dst = CAMP / args.name
    if args.action == 'stage':
        dst.mkdir(exist_ok=False)
        sources = {
            'GeometryFmaProbe.v': CAMP/'fpu-feasibility/rtl/GeometryFmaProbe.v',
            'geometry_lighting_batch.v': HERE/'phase10_lighting/geometry_lighting_batch_pipelined_shared.v',
            'phase10_float_recip2.v': HERE/'phase10_float_recip2.v',
            'phase10_raw_vtx_dma.v': HERE/'phase10_raw_vtx_dma.v',
            'phase10_raw_load_test.cpp': HERE/'phase10_raw_load_test.cpp',
            'native.bin': CAMP/'raw-load-v1/native.bin',
        }
        for name in ['geometry_color_rtz.v', 'geometry_float_batch_recip.v',
                     'geometry_transform_light.v', 'geometry_raw_load.v']:
            sources[name] = HERE/'phase10_float_batch'/name
        if args.frontend:
            sources['geometry_raw_frontend.v'] = HERE/'phase10_float_batch/geometry_raw_frontend.v'
            sources['phase10_float_cull_mem.v'] = HERE/'phase10_float_cull_mem.v'
        for name, path in sources.items():
            shutil.copyfile(path, dst/name)
        manifest = {name: {'source': str(path.relative_to(ROOT)), 'sha256': sha(dst/name)}
                    for name, path in sources.items()}
        (dst/'configuration.json').write_text(json.dumps({'held_context': args.held_context})+'\n')
        manifest['configuration.json'] = {'source': 'generated', 'sha256': sha(dst/'configuration.json')}
        (dst/'sources.json').write_text(json.dumps(manifest, indent=2)+'\n')
        fit = dst/'fit-v1'
        fit.mkdir()
        for name in sources:
            if name.endswith('.v'):
                shutil.copyfile(dst/name, fit/name)
        (fit/'sources.json').write_text((dst/'sources.json').read_text())
        old = CAMP/'raw-load-scratch-v4/fit-v1'
        for name in ['ap_core.qsf', 'timing.sdc']:
            shutil.copyfile(old/name, fit/name)
        if args.frontend:
            qsf = fit/'ap_core.qsf'
            qsf.write_text(qsf.read_text().replace('TOP_LEVEL_ENTITY geometry_raw_load',
                                                  'TOP_LEVEL_ENTITY geometry_raw_frontend') +
                           'set_global_assignment -name VERILOG_FILE geometry_raw_frontend.v\n' +
                           'set_global_assignment -name VERILOG_FILE phase10_float_cull_mem.v\n')
        if args.held_context:
            with (fit/'ap_core.qsf').open('a') as qsf:
                qsf.write('set_parameter -name CAPTURE_CONTEXT 0\n')
        print(dst)
        return
    manifest = json.loads((dst/'sources.json').read_text())
    for name, entry in manifest.items():
        if sha(dst/name) != entry['sha256']:
            raise RuntimeError(f'frozen source changed: {name}')
    if args.action == 'simulate':
        frontend = 'geometry_raw_frontend.v' in manifest
        top = 'geometry_raw_frontend' if frontend else 'geometry_raw_load'
        held = (dst/'configuration.json').exists() and json.loads((dst/'configuration.json').read_text())['held_context']
        cmd = ['verilator', '--cc', '--exe', '--build', '-j', '4', '-Wno-fatal',
               '--top-module', top, '--Mdir', 'obj',
               '-CFLAGS', '-O2 -std=c++17'+(' -DFRONTEND_TEST' if frontend else '')+
               (' -DHELD_CONTEXT_TEST' if held else '')]
        if held:
            cmd += ['-GCAPTURE_CONTEXT=0']
        cmd += [name for name in manifest if name.endswith('.v')]
        cmd += ['phase10_raw_load_test.cpp']
        with (dst/'build.log').open('w') as log:
            subprocess.run(cmd, cwd=dst, stdout=log, stderr=subprocess.STDOUT, check=True)
        with (dst/'run.log').open('w') as log:
            subprocess.run(['./obj/V'+top, 'native.bin'], cwd=dst,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        print((dst/'run.log').read_text())
    else:
        # Report both slow corners. These are standalone results, not os30 timing.
        out = dst/'fit-v1/output_files'
        for name in ['ap_core.fit.rpt', 'ap_core.sta.rpt']:
            for line in (out/name).read_text(errors='replace').splitlines():
                if ('Logic utilization (in ALMs)' in line or
                    'Total block memory bits' in line or 'Total DSP Blocks' in line or
                    '; M10K blocks ' in line or ' MHz ' in line):
                    print(line)


if __name__ == '__main__':
    main()
