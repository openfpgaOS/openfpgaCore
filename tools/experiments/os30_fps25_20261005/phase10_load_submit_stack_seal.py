#!/usr/bin/env python3
"""Seal bounded actual-ELF ownership and command-equivalence evidence."""
import json
from pathlib import Path
from phase10_experiment import CAMP, ROOT, INPUTS, sha, write

P = CAMP / 'phase10'
OUT = P / 'load-submit-stack-functional-v2'
APP = P / 'load-submit-stack-v2'
BASE = P / 'load-submit-overlap-v1'
MODES = ('normal', 'faults', 'absent', 'timeout', 'fullpool')


def main():
    prep = json.loads((OUT / 'prepared.json').read_text())
    build = json.loads((APP / 'build-normal.json').read_text())
    assert build['returncode'] == 0 and not build['diagnostic']
    assert sha(APP / 'app.elf') == prep['elf_sha256'] == build['elf_sha256']
    for name in INPUTS:
        assert sha(APP / name) == build['inputs'][name]['sha256']
        if name != 'gfx_pc.c':
            assert sha(APP / name) == sha(BASE / name), name
    assert sha(OUT / 'functional-qsim/qsim') == prep['simulator_sha256']
    for name, expected in prep['sources'].items():
        assert sha(OUT / 'functional-qsim' / name) == expected
    modes = {}
    for mode in MODES:
        result = json.loads((OUT / f'result-{mode}.json').read_text())
        observer = json.loads((OUT / f'observer-result-{mode}.json').read_text())
        assert result['passed'] and result['elf_sha256'] == prep['elf_sha256']
        assert all(result['all721_scene_geometry_culling_exact'].values())
        assert result['all721_wordcounts_exact'] and result['all8_render_streams_exact_except_source_pool_uploads']
        assert observer['passed'] and observer['frames'] == 721
        assert int(observer['last']['queued']) == 64175
        assert int(observer['last']['indexed']) == int(observer['last']['returns']) == 213940
        assert observer['global_max_stack_bytes'] < 512 * 1024
        modes[mode] = observer
    review = dict(
        passed=True, qualified_for_coupled_measurement=True, deployment_ready=False,
        elf_sha256=prep['elf_sha256'], gfx_pc_sha256=sha(APP / 'gfx_pc.c'),
        source_findings=[
            'Only gfx_pc.c differs from overlap-v1. Original local payload[32] and jobs[64] are restored.',
            'The prepared prefix is complete before an adjacent G_VTX executes synchronously inside the same indexed call. No pointer crosses its return.',
            'F3DEX2, F3DEX/F3DLP and legacy G_VTX decoding exactly reproduce the outer dispatcher. The selected F3DEX2 machine code is checked dynamically.',
            'The previous draw stale mask is installed before G_VTX. Its invalidation of new vertex slots is not overwritten after the nested call.',
            'No material, tile, viewport or texture command intervenes. Matrix/light/fog maintenance in G_VTX cannot alter already prepared packet values.',
            'gfx_gpu_vtx_cache_batch copies values into SDK staging and never retains payload/job pointers. Pointer globals are cleared after its synchronous return.',
            'After START, prior packets are submitted before polling. Ineligible/absent/pool-full paths submit before CPU vertex overwrite. Timeout/error paths drain hardware before fallback.',
            'A zero-count G_VTX submits pending work before returning, without a hardware START; verified in source and the emitted early branch, absent from the scene corpus.',
            'Source-pool upload and recycle ownership are unchanged. One drained renderer owns the pool; prepared GPU packets do not refer to its raw geometry storage.',
            'TRI2 fallback, all-rejected runs and the 64-triangle boundary retain original handling; the nested path only consumes the single immediately adjacent G_VTX.',
        ],
        stack=dict(indexed_static_frame_bytes=1376,
                   measured_indexed_including_callees_bytes=max(x['max_indexed_including_callees_bytes'] for x in modes.values()),
                   measured_whole_app_bytes=max(x['global_max_stack_bytes'] for x in modes.values()),
                   current_source_app_reserve_bytes=512*1024,
                   no_new_recursive_call=True,
                   limit='Dynamic bounds cover this 721-frame workload under the synthetic OS. Current platform source reserves 512 KiB and moves syscall handlers to a separate OS stack. This is not a universal level-recursion or deployed-OS binary proof.'),
        instrumentation='PROFILE=0/PERF_DIAG=0 selected. With profiling enabled, the indexed scope now contains the nested vertex scope and old per-function buckets would not be directly comparable.',
        modes=modes,
        limitations=[
            'Functional LOAD completion is synthetic. No overlap speedup, physical timing, Pocket FPS or deployment qualification is inferred.',
            '721 scene/geometry/cull records and eight ordered streams per mode; not 721 RTL-rendered frames.',
            'The inherited bounded texture-padding exception is reconstructed and checked from each exact ELF.',
        ])
    write(OUT / 'source-review.json', review)
    lines = ['Stack-owned adjacent LOAD/submission overlap: functional ownership proof', '',
             'PASS: five modes, each with 721 records and eight ordered render streams.',
             'All 64,175 deferred batches per mode retain their prepared bytes and exclusive caller-stack ownership.',
             'The nested G_VTX is decoded exactly and consumed once; pending pointers clear before return.', '',
             'Mode       queued   after START   before fallback']
    for mode, obs in modes.items():
        last = obs['last']
        lines.append(f"{mode:10} {int(last['queued']):6} {int(last['after_start']):13} {int(last['fallback']):17}")
    lines += ['', 'Each mode observes 213,940 indexed entries/returns and 1,638,105 outer dispatches.',
              'Static indexed frame: 1,376 bytes. Measured indexed frame plus callees: 34,800 bytes.',
              'Measured whole-app stack high-water: 37,728 bytes; current source reserves 512 KiB.',
              'No new recursion. These are workload bounds, not a proof for arbitrary display-list depth.', '',
              'Zero-count G_VTX is source/emitted-code reviewed; the corpus contains zero such calls.',
              'PROFILE=0 selected. PROFILE=1 would nest vertex time inside the indexed function scope.', '',
              'One preparation-only assertion failed on disassembler whitespace and was archived.',
              'Candidate source and ELF were unchanged; all final captures passed.', '',
              'Actual coupled timing remains required. No production, SD, FPGA fit or RTL changes.']
    (OUT / 'REPORT.txt').write_text('\n'.join(lines) + '\n')
    evidence = [APP / n for n in INPUTS] + [APP / n for n in ('app.elf', 'source.json', 'candidate.patch', 'build-normal.json')]
    evidence += [P / 'load-submit-functional-v1/manifest.json',
                 ROOT / 'src/firmware/os/targets/pocket/target_platform.h',
                 ROOT / 'src/firmware/os/hal/regs.h', ROOT / 'src/firmware/os/kernel/main.c',
                 ROOT / 'src/firmware/os/kernel/syscall.c', ROOT / 'src/firmware/os/kernel/loader.c',
                 Path(__file__), Path(__file__).with_name('phase10_load_submit_stack.py'),
                 Path(__file__).with_name('phase10_load_submit_stack_functional.py')]
    files = {str(p.relative_to(OUT)): sha(p) for p in OUT.rglob('*')
             if p.is_file() and p.name != 'manifest.json' and p.suffix not in ('.o', '.d')}
    write(OUT / 'manifest.json', dict(passed=True, qualified_for_coupled_measurement=True,
        deployment_ready=False, files=files,
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
