#!/usr/bin/env python3
"""Freeze and validate GDR1 arithmetic/ordering integration; no publication."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('name')
    args = ap.parse_args()
    assert Path(args.name).name == args.name
    dst = OUT / args.name
    dst.mkdir(exist_ok=False)
    paths = [HERE / name for name in (
        'phase10_gpu_direct_mmio.v', 'phase10_raw_vtx_dma.v',
        'phase10_float_recip2.v', 'phase10_float_cull_mem.v',
        'phase10_float_fixed_pipe.v', 'phase10_gpu_direct_test.cpp',
        'phase10_run_dma_test.cpp', 'phase10_run_precheck_test.cpp',
        'phase10_gpu_direct_abi.h')]
    paths += [HERE / 'phase10_float_batch' / name for name in (
        'geometry_raw_frontend.v', 'geometry_raw_load.v',
        'geometry_transform_light.v', 'geometry_float_batch_recip.v',
        'geometry_color_rtz.v', 'geometry_run_precheck.v',
        'geometry_run_dma.v', 'geometry_run_commit.v',
        'geometry_vertex_packet.v')]
    paths += [HERE / 'phase10_lighting/geometry_lighting_batch_pipelined_shared.v',
              OUT / 'fpu-feasibility/rtl/GeometryFmaProbe.v',
              OUT / 'raw-load-v1/native.bin']
    for path in paths:
        shutil.copyfile(path, dst / path.name)
    shutil.copyfile(Path(__file__), dst / 'recipe.py')
    source_hashes = {p.name: {'origin': str(p.relative_to(ROOT)),
                            'sha256': sha(dst / p.name)} for p in paths}
    (dst / 'sources.json').write_text(json.dumps(source_hashes, indent=2)+'\n')
    variants = {
        'precheck': ('geometry_run_precheck', ['geometry_run_precheck.v'],
                     'phase10_run_precheck_test.cpp', []),
        'command-dma': ('geometry_run_dma', ['geometry_run_dma.v',
                         'geometry_run_precheck.v', 'phase10_raw_vtx_dma.v'],
                        'phase10_run_dma_test.cpp', []),
        'integrated': ('geometry_direct_mmio',
                       [p.name for p in paths if p.suffix == '.v'],
                       'phase10_gpu_direct_test.cpp', ['native.bin']),
    }
    results = {}
    for label, (top, rtl, test, argv) in variants.items():
        cmd = ['verilator', '--cc', '--exe', '--build', '-j', '4',
               '-Wno-fatal', '--top-module', top, '--Mdir', 'obj-'+label,
               '-CFLAGS', '-O2 -std=c++17', *rtl, test]
        with (dst / (label+'-build.log')).open('x') as log:
            subprocess.run(cmd, cwd=dst, stdout=log, stderr=subprocess.STDOUT, check=True)
        exe = dst / ('obj-'+label) / ('V'+top)
        with (dst / (label+'-run.log')).open('x') as log:
            subprocess.run([str(exe), *argv], cwd=dst, stdout=log,
                           stderr=subprocess.STDOUT, check=True)
        result = json.loads((dst / (label+'-run.log')).read_text().splitlines()[-1])
        assert result['passed']
        result.update(binary_sha256=sha(exe), build_command=cmd)
        results[label] = result
    for name, entry in source_hashes.items():
        assert sha(dst/name) == entry['sha256'], name
    results.update(qualified_for_deployment=False,
                   scope='Native arithmetic, RAM ownership and ordered packet integration. '
                         'Random-latency behavioral AXI and transactional ring responder; '
                         'not full-core timing, real memory contention or Pocket FPS.',
                   sources_sha256=sha(dst/'sources.json'), recipe_sha256=sha(dst/'recipe.py'))
    (dst/'result.json').write_text(json.dumps(results, indent=2)+'\n')
    print(json.dumps({name: row for name, row in results.items()
                      if name != 'scope'}, indent=2))


if __name__ == '__main__':
    main()
