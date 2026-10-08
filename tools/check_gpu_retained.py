#!/usr/bin/env python3
"""Compare retained GPU pixel results and per-case cycles against saved RTL.

Use an os20 or os30 manifest from check_gpu_target_matrix.py and a baseline tree
containing src/fpga/{common/gpu_core.v,test/tb_gpu.v}. The candidate must still
match the manifest's GPU sources. Both runs use the same independent pixel
oracles and the same memory-latency scenarios. No game FPS claim is made.

This legacy fixture uses common modules directly and does not resolve variant
source maps. See check_gpu_target_matrix.py for the selected os30 qualification
scope; matching this fixture alone does not validate common/os30 snapshots.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    variant = manifest['variant']['variant']
    assert variant in ('os20', 'os30')
    expected_cases = 23 if variant == 'os20' else 38
    if variant == 'os20':
        assert not manifest['parameters']['INCLUDE_PERSPECTIVE']
        assert not manifest['parameters']['INCLUDE_DEPTH']
    else:
        assert not manifest['parameters']['INCLUDE_PARAM_SPAN_LIST']
        assert manifest['parameters']['INCLUDE_DIRECT_COLOR']
        assert manifest['parameters']['INCLUDE_VTX_CACHE']
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    frozen = out / 'frozen'
    common = frozen / 'src/fpga/common'
    test = frozen / 'src/fpga/test'
    shutil.copytree(ROOT / 'src/fpga/common', common)
    test.mkdir(parents=True)
    for source in [ROOT / 'src/fpga/test/tb_gpu.v',
                   ROOT / 'src/fpga/test/tb_gpu_acceptance_main.cpp',
                   ROOT / 'src/fpga/test/tb_gpu_retained_main.cpp',
                   *sorted((ROOT / 'src/fpga/test').glob('*.h'))]:
        shutil.copy2(source, test / source.name)
    for name, expected in manifest['sources'].items():
        relative = Path(name).relative_to(ROOT)
        if (frozen / relative).is_file():
            assert hashlib.sha256((frozen / relative).read_bytes()).hexdigest() == expected, name
    baseline_core = out / 'baseline-gpu_core.v'
    baseline_fixture = out / 'baseline-tb_gpu.v'
    shutil.copy2(args.baseline / 'src/fpga/common/gpu_core.v', baseline_core)
    shutil.copy2(args.baseline / 'src/fpga/test/tb_gpu.v', baseline_fixture)
    hashes = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(out.rglob('*')) if p.is_file()}
    (out / 'sources.json').write_text(json.dumps(hashes, indent=2) + '\n')
    scenarios = [('quiet', []), ('delayed', ['+gpu_rd_latency=24',
                 '+gpu_rd_latency_var=1', '+gpu_wr_latency=17']),
                 ('gapped', ['+gpu_rd_latency=24', '+gpu_rd_latency_var=1',
                             '+gpu_wr_latency=17', '+gpu_rd_beat_gap=7'])]

    def run(label):
        dest = out / label
        dest.mkdir()
        fixture = baseline_fixture if label == 'baseline' else test / 'tb_gpu.v'
        core = baseline_core if label == 'baseline' else common / 'gpu_core.v'
        forwarded = set(re.findall(r'parameter\s+(\w+)\s*=', fixture.read_text()))
        flags = [flag for flag in manifest['flags'] if flag[2:].split('=')[0] in forwarded]
        sources = [fixture, core, common / 'gpu_edge_walker.v',
                   common / 'gpu_tex_cache.v', common / 'gpu_color_depth_cache.sv',
                   test / 'tb_gpu_retained_main.cpp']
        command = ['verilator', '--cc', '--exe', '--build', '--trace', '-j', '2',
                   '-Wall', '-Wno-fatal', '-Wno-BADVLTPRAGMA', '--top-module', 'tb_gpu',
                   '--Mdir', str(dest / 'obj'), '-I' + str(common),
                   '-CFLAGS', ' '.join(manifest['cflags']),
                   *['+define+' + d for d in manifest['variant']['defs'].split()],
                   *flags, *map(str, sources)]
        with (dest / 'build.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        results = {}
        for scenario, plus in scenarios:
            with (dest / (scenario + '.log')).open('w') as log:
                subprocess.run([str(dest / 'obj/Vtb_gpu'), *plus], cwd=dest,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            text = (dest / (scenario + '.log')).read_text()
            result = re.search(r'Acceptance Results: (\d+) passed, (\d+) failed', text)
            assert result and int(result[1]) > 0 and int(result[2]) == 0
            cycles = dict((name, int(value)) for name, value in
                          re.findall(r'^CYCLES (\w+) (\d+)$', text, re.M))
            assert len(cycles) == expected_cases
            results[scenario] = dict(passed=int(result[1]), cycles=cycles)
        return label, results

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = dict(pool.map(run, ['baseline', 'candidate']))
    matches = results['baseline'] == results['candidate']
    (out / 'results.json').write_text(json.dumps(
        dict(identical_pixels_and_cycles=matches, runs=results), indent=2) + '\n')
    assert matches, 'Retained workload results or cycle counts changed'
    print(f'PASS: {expected_cases} retained workloads have identical pixels and cycles in all 3 latency scenarios')


if __name__ == '__main__':
    main()
