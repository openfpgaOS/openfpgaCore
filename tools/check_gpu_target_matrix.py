#!/usr/bin/env python3
"""Run GPU acceptance with parameters extracted from every target/variant.

Preprocesses the production top level using its Make-resolved feature macros.
This avoids hand-maintained copies silently diverging from the shipped GPU.
Uses the existing pixel oracles and disabled-command drain tests, with low and
variable memory latency. Fast texture memory has separate controller tests.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from sweep_target_matrix import inventory

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--include-diagnostics', action='store_true')
    parser.add_argument('--variant', help='One variant for a diagnostic trial')
    parser.add_argument('--extra-macro', action='append', default=[])
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    results = {}
    variants = inventory(include_diagnostics=args.include_diagnostics or bool(args.variant))
    if args.variant:
        variants = [row for row in variants if row['variant'] == args.variant]
        if not variants:
            parser.error('Unknown variant')
    if args.extra_macro:
        if len(variants) != 1 or any(not re.fullmatch(r'[A-Z_][A-Z_0-9]*', m) for m in args.extra_macro):
            parser.error('Diagnostic macros require one variant and uppercase identifiers')
        variants[0]['defs'] += ' ' + ' '.join(args.extra_macro)

    # Freeze before starting workers: an RTL edit during a long matrix must
    # not make later variants test different source bytes from earlier ones.
    frozen = out / 'frozen'
    common, test = frozen / 'src/fpga/common', frozen / 'src/fpga/test'
    shutil.copytree(ROOT / 'src/fpga/common', common)
    test.mkdir(parents=True)
    for source in [ROOT / 'src/fpga/test/tb_gpu.v',
                   ROOT / 'src/fpga/test/tb_gpu_acceptance_main.cpp',
                   *sorted((ROOT / 'src/fpga/test').glob('*.h'))]:
        shutil.copy2(source, test / source.name)
    ignored = shutil.ignore_patterns('bld', 'db', 'incremental_db', 'output_files',
        '*.rpt', '*.summary', '*.log', '*.sof', '*.rbf', '*.rbf_r', '*.qws', '*.smsg')
    for name in sorted({row['target'] for row in variants}):
        shutil.copytree(ROOT / 'src/fpga/targets' / name,
                        frozen / 'src/fpga/targets' / name, ignore=ignored)
    hashes = {str(p.relative_to(frozen)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(frozen.rglob('*')) if p.is_file()}
    (out / 'frozen-sources.json').write_text(json.dumps(hashes, indent=2) + '\n')

    def run(row):
        variant = row['variant']
        dest = out / variant
        dest.mkdir()
        target = frozen / 'src/fpga/targets' / row['target']
        top = target / ('emu.sv' if row['target'] == 'mister' else 'core_top.v')
        definitions = ['+define+' + d for d in row['defs'].split()]
        command = ['verilator', '-E', '-P', '-I' + str(target), '-I' + str(target / 'apf'),
                   *definitions, str(top)]
        pre = subprocess.run(command, capture_output=True, text=True, check=True)
        (dest / 'preprocessed.v').write_text(pre.stdout)
        match = re.search(r'gpu_core\s*#\((.*?)\)\s*gpu\s*\(', pre.stdout, re.S)
        if not match:
            raise ValueError('GPU instance not found in ' + variant)
        block = re.sub(r'//[^\n]*', '', match[1])
        params = dict((name, int(value)) for name, value in re.findall(r'\.(\w+)\(\s*(\d+)\s*\)', block))
        if len(params) != block.count('.'):
            raise ValueError('GPU parameters contain an unhandled expression')
        # Fill omitted overrides from the real module defaults, not the
        # convenience defaults of tb_gpu.
        core = (common / 'gpu_core.v').read_text()
        core_params = core[core.index('module gpu_core'):core.index('// AXI4 Read Master')]
        defaults = dict((name, int(value)) for name, value in
                        re.findall(r'parameter\s+(\w+)\s*=\s*(\d+)\s*[,\n]', core_params))
        defaults.update(params)
        fixture = (test / 'tb_gpu.v').read_text()
        forwarded = re.findall(r'parameter\s+(\w+)\s*=', fixture[:fixture.index('input  wire')])
        flags = ['-G' + name + '=' + str(defaults[name]) for name in forwarded]
        cflags = ['-std=c++17', '-O2']
        if not defaults['INCLUDE_COMPACT_SPAN'] and not defaults['INCLUDE_COLUMN_LIST']:
            cflags += ['-DGPU_TEST_OS30_LEAN']
        gates = [('INCLUDE_DIRECT_COLOR', 'GPU_TEST_TRUECOLOR', True),
                 ('INCLUDE_XFORM_RGB', 'GPU_TEST_XFORM', True),
                 ('INCLUDE_PARAM_TRI', 'GPU_TEST_NO_PARAM_TRI', False),
                 ('INCLUDE_VERT_TRI', 'GPU_TEST_NO_VERT_TRI', False),
                 ('INCLUDE_PALETTE', 'GPU_TEST_NO_PALETTE', False),
                 ('INCLUDE_PARAM_SPAN_Q29', 'GPU_TEST_NO_PARAM_SPAN_Q29', False),
                 ('INCLUDE_COMMAND_DMA', 'GPU_TEST_NO_COMMAND_DMA', False),
                 ('INCLUDE_CLIP_TRI', 'GPU_TEST_NO_CLIP_TRI', False),
                 ('INCLUDE_GPU_XFORM_MAC', 'GPU_TEST_NO_MAC', False)]
        for feature, define, enabled in gates:
            if bool(defaults[feature]) == enabled:
                cflags.append('-D' + define)
        sources = [test / 'tb_gpu.v', common / 'gpu_core.v', common / 'gpu_edge_walker.v',
                   common / 'gpu_tex_cache.v', common / 'gpu_color_depth_cache.sv',
                   test / 'tb_gpu_acceptance_main.cpp']
        manifest = dict(variant=row, parameters=defaults, flags=flags, cflags=cflags,
            sources={str(ROOT / p.relative_to(frozen)):hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [top, *sources]})
        (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        command = ['verilator', '--cc', '--exe', '--build', '--trace', '-j', '2',
                   '-Wall', '-Wno-fatal', '-Wno-BADVLTPRAGMA', '--top-module', 'tb_gpu',
                   '--Mdir', str(dest / 'obj'), '-I' + str(common), '-CFLAGS', ' '.join(cflags),
                   *definitions, *flags, *map(str, sources)]
        with (dest / 'build.log').open('w') as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
        result = {}
        scenarios = [('quiet', []), ('delayed', ['+gpu_rd_latency=24',
                      '+gpu_rd_latency_var=1', '+gpu_wr_latency=17'])]
        if 'INCLUDE_Z_EARLY_RESTART' in row['defs'].split():
            # Restarted pixels must remain coherent while later fill beats
            # pause, and command completion must wait for the burst tail.
            scenarios.append(('gapped', ['+gpu_rd_latency=24',
                              '+gpu_rd_latency_var=1', '+gpu_wr_latency=17',
                              '+gpu_rd_beat_gap=7']))
        for label, plus in scenarios:
            with (dest / (label + '.log')).open('w') as log:
                p = subprocess.run([str(dest / 'obj/Vtb_gpu'), *plus], cwd=dest,
                                   stdout=log, stderr=subprocess.STDOUT, timeout=300)
            text = (dest / (label + '.log')).read_text()
            match = re.search(r'Acceptance Results: (\d+) passed, (\d+) failed', text)
            result[label] = dict(exit_code=p.returncode,
                                 passed=int(match[1]) if match else 0,
                                 failed=int(match[2]) if match else None)
            print(variant, label, result[label], flush=True)
        (dest / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        return variant, result

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for variant, result in pool.map(run, variants):
            results[variant] = result
            (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    if any(run['exit_code'] or run['failed'] != 0 for row in results.values() for run in row.values()):
        raise SystemExit('GPU matrix failed; inspect per-variant logs')
    print('PASS: every declared GPU variant', flush=True)


if __name__ == '__main__':
    main()
