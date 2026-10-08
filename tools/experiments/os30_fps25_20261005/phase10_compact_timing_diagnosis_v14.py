#!/usr/bin/env python3
"""Diagnose already-exported v14 paths. No Quartus, source changes or fitting."""
import collections
import json
from pathlib import Path
import re
import shutil
from phase10_experiment import CAMP, ROOT, sha, write

P = CAMP / 'phase10'
A = P / 'compact-timing-audit-v14'
OLD = P / 'compact-timing-audit-v7'
OUT = P / 'compact-timing-diagnosis-v14'


def family(row):
    start, end = row['from_node'], row['to_node']
    if '|gpu_tex_cache:tex_cache|' in end:
        return 'texture_tag_to_request_capture'
    if 'MulPlugin_HIGH' in start:
        return 'cpu_multiply_format_to_rs1_forward'
    if 'execute_ctrl1_up_Decode_UOP_lane0[6]' in start:
        return 'cpu_rs3_enable_hazard_to_decode_and_aligner'
    if 'execute_ctrl1_up_Decode_UOP_lane0[16]' in start:
        return 'cpu_operand_tag_to_rs1_forward'
    if '|fbwq_addr_rtl_0|' in end or '|altdpram:fbwq_addr_rtl_0|' in end:
        return 'gpu_writequeue_address_read_enable'
    if '|io_sdram:isr0|' in end:
        return 'sdram_bank_output'
    raise AssertionError((start, end))


def mapped_path(report, row):
    blocks = re.split(r'(?=Path #\d+: Setup)', report.read_text())[1:]
    block = next(b for b in blocks if row['to_node'] in b.split('Data Arrival Path')[0])
    active, hops = False, []
    for line in block.split('Data Required Path')[0].splitlines():
        fields = [v.strip() for v in line.split(';')]
        if len(fields) > 7 and fields[-2] == 'data path':
            active = True
            continue
        if active and len(fields) > 7 and fields[4] in ('CELL', 'IC'):
            hops.append(dict(increment_ns=float(fields[2]), type=fields[4],
                             fanout=int(fields[5]), element=fields[-2]))
    assert hops
    return dict(path=row, hops=hops)


def main():
    assert not OUT.exists()
    data = json.loads((A / 'paths.json').read_text())
    old = json.loads((OLD / 'paths.json').read_text())
    summary = json.loads((A / 'result.json').read_text())
    assert summary['below_90_counts'] == dict(slow_85=18, slow_0=6)
    assert not summary['equal_slack_launch_ties']
    groups, details, targets, baseline = {}, {}, {}, {}
    for corner in ('slow_85', 'slow_0'):
        buckets = collections.defaultdict(list)
        for row in data[corner + '_below90']:
            buckets[family(row)].append(row)
        groups[corner] = {name: dict(endpoints=len(rows), worst=min(rows, key=lambda r: r['slack_ns']))
                          for name, rows in buckets.items()}
        details[corner] = {name: mapped_path(A / 'reports' / f'{corner}_below90.rpt', value['worst'])
                           for name, value in groups[corner].items()}
        targets[corner] = {}
        for label in ('cpu_lane_mask', 'cpu_rs3_mask', 'cpu_rs3_fetch', 'cpu', 'gpu_internal',
                      'geometry', 'render_cache', 'sdram', 'ring_status', 'texture_ram',
                      'audio_accum', 'cpu_writeback'):
            rows = [r for r in data[corner + '_' + label] if r['relationship_ns'] == 10]
            assert rows
            targets[corner][label] = min(rows, key=lambda r: r['slack_ns'])
        for label in ('cpu_lane_mask', 'cpu_rs3_mask'):
            details[corner][label] = mapped_path(A / 'reports' / f'{corner}_{label}.rpt', targets[corner][label])
        rs3 = [r for r in old[corner + '_dispatch'] if 'RS3_PHYS' in r['from_node'] and
               ('buffer_mask' in r['to_node'] or 'buffer_last' in r['to_node'])]
        assert rs3
        baseline[corner] = dict(rs3_mask=min(rs3, key=lambda r: r['slack_ns']))
        lane = [r for r in old[corner + '_cpu'] if 'execute_ctrl3_up_LANE_SEL_lane0' in r['from_node'] and
                ('buffer_mask' in r['to_node'] or 'buffer_last' in r['to_node'])]
        if lane:
            baseline[corner]['lane_mask_exported_worst'] = min(lane, key=lambda r: r['slack_ns'])
    before = json.loads((P / 'compact-lod5-core-v7/prepared.json').read_text())['source_sha256']
    after = json.loads((P / 'compact-lod5-core-v14/prepared.json').read_text())['source_sha256']
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert changed == ['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
    OUT.mkdir()
    write(OUT / 'result.json', dict(readonly=True, fit_invoked=False, rtl_changed=False,
          complete_census=summary['below_90_counts'], families=groups,
          selected_cones=targets, v7_exported_selected_cones=baseline,
          source_delta_from_v7=changed, timing_closed=False,
          conclusion='The original lane/reservation-to-aligner cone improved, but the RS3 downFire route remains marginal and unrelated mapped paths now dominate.'))
    write(OUT / 'mapped-cones.json', details)
    (OUT / 'REPORT.txt').write_text('''Compact-v14 routed timing diagnosis

The original v7 LANE_SEL -> aligner mask/last path improved substantially:
98.736 MHz hot and101.000 MHz cold in the explicitly requested direct query,
versus the old hot89.654 MHz path. This does not improve complete timing:
v14 is85.962 MHz hot /87.207 MHz cold, setup-1.633 ns, hold+0.115 ns.
The complete census finds18 hot /6 cold endpoints below90 MHz (v7:3/0).
Both searches examined1000 endpoints and ended above the threshold; there
are no equal-slack launch ties or empty reports. Configured100 MHz closure
and the comparative greater-than90 MHz gate both remain false.

Effect of the direct CPU change
-------------------------------
The exact ctrl3 LANE_SEL query matches2 launch registers and5 mask/last
endpoints in each corner. The worst path has7 levels,9.868 ns data and
5.738 ns routing hot. It now traverses WideOr119/comb13/comb3/early_can,
decode1-ready, mask~0/~5 and the register enable, without the old shared
PLRU-valid/capture route. The measured source-to-endpoint bound improved;
unchanged level count and changed placement prevent assigning all gain to
one Boolean simplification alone.

The exact RS3_PHYS query matches8 launch registers and the same5 endpoints.
Its worst path is89.614/92.439 MHz,8 levels, hot10.887 ns data/6.206 ns route.
The v7 exported RS3 path was90.050/91.199 MHz and7 levels. The current path
traverses hazard6 compare, WideOr117, WideOr118, original decode1-ready
(fanout132), and mask~0/~5 through the original consumption/downFire path.
It therefore remains below90 hot even though the final-fetch-only factor is
correct. A per-endpoint worst-path census reports the still slower UOP[6]
launch at those same endpoints, so this targeted RS3 path is not an extra
census endpoint and must not be added to the18 count.

New complete-census families
---------------------------
1. Texture request capture:1 endpoint in both corners, tag RAM -> pipe_wide_a,
   85.962/87.207 MHz,7 levels. The hot path is10.216 ns data/6.317 ns route,
   with pipe_hit_a~2/~3/hit -> resp_valid~0 -> comb~9/~10 -> pipe_addr_a~0
   and the capture enable. This is the new whole-core worst path, distinct
   from the tag-to-data-RAM address path (90.342/91.141 MHz).
2. Integer multiply-result forwarding:2 endpoints in both corners,
   MulPlugin_HIGH -> SRC1[27]/duplicate,87.321/88.668 MHz,5 levels. The path
   crosses sign selection, integer writeback mux/merge and RS1 forwarding.
   Routing is8.148 ns of11.188 ns hot data delay (73%).
3. RS3 enable/hazard into decode and aligner:6 hot /2 cold endpoints.
   UOP[6] -> WideOr27 -> RS3 hazard19 -> WideOr116/118 -> decode1-ready
   -> mask/last enables,87.512/89.262 MHz. Hot has7 levels,11.161 ns data,
   6.479 ns route; the ready net has fanout132. Five hot endpoints are
   mask/last, and the sixth is a predicted-PC capture. This source differs
   from both the repaired LANE_SEL path and the explicit RS3_PHYS query.
4. Operand tag into RS1 forwarding:4 hot /1 cold endpoints. UOP[16] travels
   through float bypass-enable/tag reduction, integer bypass selection and
   RS1 capture, worst87.781/89.008 MHz. The hot worst has6 levels and
   7.622 ns routing out of11.069 ns data. Other hot bits end at88.786,
   89.606 and89.880 MHz.
5. GPU writequeue address read enable:4 hot endpoints at88.700 MHz.
   fbwq_rd_ptr[0] -> address/link mux -> burst3/burst4 qualification ->
   start_burst_words encoder -> aw_ahead_issue -> m_wr_awaddr mux ->
   fbwq_addr MLAB enable. Eight levels,6.819 ns routing/10.408 ns data.
6. SDRAM bank output:1 hot endpoint, burst_defer_word -> phy_ba[0],
   88.778 MHz,5 levels. always1/phy_ba selection and Selector64 chain;
   routing is8.338 ns of11.889 ns data (70%). The cold SDRAM worst is
   word_rd_queue -> phy_ba[0] at90.090 MHz.

Other measured endpoints remain above90: LOAD92.713/91.366, render-cache
94.349/94.180, ring STATUS104.243/108.096, audio112.133/110.705,
cached integer writeback100.685/103.983 MHz. These bounds are per exported
endpoint family, not100 MHz closure or physical Pocket FPS measurements.

Only the generated CPU source differs from compact-v7. Texture, GPU queue,
SDRAM, cache, LOAD, clocks and physical options are byte-identical. Their
new criticality reflects this implementation's mapping/placement/routing,
not newly added functionality. V14 needs18,005 ALMs, places18,010, and uses
1,841/1,848 LABs,307/308 M10Ks and45 DSPs. Smaller local logic or a spared
LAB alone did not preserve the v7 near-threshold routing outcome.

Exactly one authorized quartus_sta process exported a private database copy.
Original database/input hashes were rechecked. This diagnosis only parsed
the completed evidence: no fitting, source edit, physical-option change,
production promotion or device write occurred.
''')
    shutil.copyfile(__file__, OUT / 'recipe.py')
    files = [*OUT.iterdir(), Path(__file__), A / 'manifest.json', OLD / 'manifest.json',
             P / 'compact-timing-audit-v13/manifest.json', P / 'compact-lod5-core-v14/final-manifest.json',
             P / 'compact-lod5-core-v14/prepared.json', P / 'compact-lod5-core-v7/prepared.json',
             P / 'cpu-fetch-v7-source-review-v12/manifest.json']
    write(OUT / 'manifest.json', dict(passed_readonly_audit=True, timing_closed=False,
          fit_invoked=False, evidence_sha256={str(p.relative_to(ROOT)): sha(p) for p in files}))
    print(sha(OUT / 'manifest.json'))
    print(json.dumps({corner: {name: value['endpoints'] for name, value in rows.items()}
                      for corner, rows in groups.items()}, indent=2))


if __name__ == '__main__':
    main()
