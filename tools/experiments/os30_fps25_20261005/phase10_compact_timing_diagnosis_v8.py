#!/usr/bin/env python3
"""Read existing v7/v8 STA exports and seal the endpoint-family comparison."""
import collections
import json
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
OUT = P / 'compact-timing-diagnosis-v8'


def family(row):
    a, b = row['from_node'], row['to_node']
    if '|spanprod_count_rtl_0|' in a or 'altsyncram:spanprod_count_rtl_0|' in a:
        assert any(n in b for n in ('fbwq_req_', 'z_acc_mask'))
        return 'GPU span-count RAM to write-request/depth-accumulator control'
    if a.endswith('|spanprod_subpix_y'):
        assert '|gpu_edge_walker:g_tri_walker.tri_walker|y_start[' in b
        return 'GPU subpixel offset to edge-walker start'
    if a.endswith('|fb_acc_addr[11]'):
        assert '|cb_dst_r[' in b
        return 'GPU framebuffer accumulator match to blend destination'
    if '|LsuL1Plugin_logic_banks_1_byp_hit[' in b:
        return 'CPU LSU effective address to cache bypass match'
    if '|audio_mixer:audio_mixer_inst|' in b:
        return 'Audio voice-table bypass to loop validity'
    if '|periph_rd_data_r[' in b:
        return 'Peripheral read-address decode'
    if b.endswith('|m_wlast'):
        return 'CPU SDRAM write adapter finality'
    if '|input_prev_' in b:
        return 'Peripheral input-history update'
    if 'FpuCmpPlugin_' in b:
        return 'CPU FPU minimum/maximum operand selection'
    raise AssertionError(row)


def main():
    OUT.mkdir(exist_ok=False)
    audit = P / 'compact-timing-audit-v8'
    manifest = json.loads((audit / 'manifest.json').read_text())
    for name, digest in manifest['files'].items():
        assert sha(audit / name) == digest, name
    for name, digest in manifest['evidence_sha256'].items():
        assert sha(ROOT / name) == digest, name
    paths = json.loads((audit / 'paths.json').read_text())
    before = json.loads((P / 'compact-timing-audit-v7/module-worst.json').read_text())
    after = json.loads((audit / 'module-worst.json').read_text())
    closures = [json.loads((P / f'compact-lod5-core-v{i}/prepared.json').read_text())
                ['source_sha256'] for i in (7, 8)]
    assert closures[0].keys() == closures[1].keys()
    changes = [n for n in closures[0] if closures[0][n] != closures[1][n]]
    assert changes == ['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
    qsf = [P / f'compact-lod5-core-v{i}/fit-seed2/jobs/pocket-os30-s2/'
           'src/fpga/targets/pocket/ap_core.qsf' for i in (7, 8)]
    assert sha(qsf[0]) == sha(qsf[1])
    groups = {}
    comparisons = {}
    for corner in ('slow_85', 'slow_0'):
        bins = collections.defaultdict(list)
        rows = paths[corner + '_below90']
        assert len(rows) == 75
        assert not any('AlignerPlugin_logic_buffer_mask' in r['to_node']
                       or 'AlignerPlugin_logic_buffer_last' in r['to_node'] for r in rows)
        for row in rows:
            bins[family(row)].append(row)
        groups[corner] = {k: dict(count=len(v), worst=min(v, key=lambda r: r['slack_ns']),
                                paths=v) for k, v in bins.items()}
        comparisons[corner] = {
            name: dict(v7_mhz=before[corner][name]['period_bound_mhz'],
                       v8_mhz=row['period_bound_mhz'], v8_path=row)
            for name, row in after[corner].items()}
    result = dict(passed_readonly_analysis=True, only_source_changes=changes,
                  identical_effective_qsf_sha256=sha(qsf[0]),
                  below90_families=groups, module_comparison=comparisons,
                  prior_mask_last_endpoints_below90=False,
                  separate_enable_vs_reservation_causality_established=False,
                  new_sta_invoked=False, fitter_invoked=False,
                  settings_or_sources_changed=False)
    write(OUT / 'result.json', result)
    report = '''Compact-v8 timing diagnosis: target repaired, unrelated placement regressed

The complete census finds 75 register endpoints below 90 MHz at each slow
corner, versus 3/0 in v7. No v8 sub90 path terminates at the old aligner
mask/last endpoints. The worst reported dispatch-source family is now
91.449 MHz at 85 C and 91.558 MHz at 0 C; v7 was 90.050/91.199 MHz.
The all-register census establishes removal of the old failing endpoints;
the finite targeted dispatch report does not assign their exact new margins.

Only the CPU RTL changed. Effective QSF bytes are identical. Both local
rewrites were composed in this one source candidate, so this fit cannot
attribute the wider placement effect separately to enable absorption or
reservation factoring. It is not evidence of a new functional defect.

Exact endpoint families (85 C / 0 C):
  GPU span-count RAM to write-request/depth-accumulator control: 42 / 47
  GPU subpixel offset to edge-walker start:                     14 / 14
  GPU framebuffer accumulator match to blend destination:       5 / 5
  CPU LSU effective address to cache bypass match:              5 / 5
  Audio voice-table bypass to loop validity:                    1 / 1
  Peripheral read-address decode:                              1 / 1
  CPU SDRAM write-adapter finality:                             1 / 1
  Peripheral input-history update:                             6 / 0
  CPU FPU minimum/maximum operand selection:                    0 / 1
  TOTAL:                                                     75 / 75

At 85 C, the worst path is GPU fb_acc_addr[11] to cb_dst_r[2],
85.756 MHz, -1.661 ns at configured 100 MHz. It has seven logic levels,
11.696 ns data delay and 7.431 ns routing (64%). The routed path crosses
accumulator-address equality, FBSS selection and the blend operand input.
This GPU source is unchanged from v7.

At 0 C, the worst path is CPU execute_ctrl2_up_early0_SRC2[1] to
LsuL1Plugin_logic_banks_1_byp_hit[3], 85.426 MHz and -1.706 ns.
It crosses the effective-address adder, LSU address arbiter, cache address
selection and bypass equality. Six logic levels, 11.387 ns data delay and
6.656 ns routing (58%). It does not cross either revised ready expression.
The source of this address/bypass logic is unchanged. Its 85 C bound is
86.543 MHz. Treat this as a newly limiting placement path, not a reason to
cut cache capacity or change LSU behavior without new evidence.

The numerous span-count paths launch from the M10K's named internal port-B
register and pass RAM data, count predicates and the existing write-source
selection. Their slow-0 worst endpoint is fbwq_req_data[24]: eight logic
levels, 8.968 ns data delay, 4.756 ns routing and -1.572 ns slack. The large
negative reported clock skew includes the memory register/output timing;
it must not be discarded or masked with a false-path constraint.

Other module endpoint bounds, 85 C / 0 C MHz:
  LOAD:        93.659 / 92.259
  Render cache:94.082 / 97.523
  SDRAM:       92.524 / 93.967
  RVC decode:  92.704 / 92.132
All remain above 90 MHz in this census. No additional fallback was composed.

Area shifted from 17,956 to 18,052 needed ALMs (+96), 17,997 to 18,090
placed ALMs (+93), and 1,842 to 1,844 LABs (+2). Memory and DSP counts remain
307 M10Ks and 45 DSPs. The placement is still almost full. The available
evidence supports rejecting v8's routed result despite the local ready-path
improvement; it does not establish that either subpatch alone would win.

Configured 100 MHz is not closed: setup -1.706 ns, hold +0.028 ns. The
comparative greater-than-90-MHz target also fails. This diagnosis only reads
the sealed copied-netlist audit and existing source/fit records. No further
STA process, fitter, source, clock, seed or physical setting was changed.
'''
    (OUT / 'REPORT.txt').write_text(report)
    evidence = [audit / 'manifest.json', P / 'compact-timing-audit-v7/manifest.json',
                P / 'compact-lod5-core-v7/prepared.json',
                P / 'compact-lod5-core-v8/prepared.json',
                P / 'compact-lod5-core-v7/final-manifest.json',
                P / 'compact-lod5-core-v8/final-manifest.json', *qsf, Path(__file__)]
    write(OUT / 'manifest.json', dict(
        files={str(p.relative_to(OUT)): sha(p) for p in sorted(OUT.iterdir()) if p.is_file()},
        evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in evidence}))
    print(sha(OUT / 'manifest.json'))


if __name__ == '__main__':
    main()
