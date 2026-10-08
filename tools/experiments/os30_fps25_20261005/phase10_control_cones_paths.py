#!/usr/bin/env python3
"""Read only the control-cone composition's existing top-20 slow setup reports."""
import collections
import hashlib
import json
import re
import shutil
from pathlib import Path
from phase10_timing_paths_parse import parse

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
F = P / 'control-cones-fit-v1'
O = P / 'control-cones-paths-v1'
T = F / 'fit-seed2/job/src/fpga/targets/pocket/timing'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def put(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def family(row):
    a, b = row['from_node'], row['to_node']
    if '_up_RD_PHYS_lane0' in a and '_up_float_RS1_lane0' in b:
        return 'cpu_float_rs1_tag_and_forward'
    if '|gpu_core:gpu|fbwq_rd_ptr' in a:
        return 'gpu_write_queue_read_enable_and_awlength'
    if '|gpu_color_depth_cache:gpu_render_cache|' in a:
        return 'gpu_render_cache_bank_read_address'
    if '|gpu_core:gpu|p1_light' in a and '|gpu_core:gpu|p2_color' in b:
        return 'gpu_fragment_light_color_modulation'
    raise AssertionError((a, b))


def detail(report, row):
    blocks = re.split(r'(?=Path #\d+: Setup)', report.read_text())[1:]
    block = next(b for b in blocks if row['from_node'] in b.split('Data Arrival Path')[0]
                 and row['to_node'] in b.split('Data Arrival Path')[0])
    active = False
    hops = []
    for line in block.split('Data Required Path')[0].splitlines():
        cells = [x.strip() for x in line.split(';')][1:-1]
        if len(cells) >= 7 and cells[-1] == 'data path':
            active = True
            continue
        if active and len(cells) >= 7 and cells[3] in ('CELL', 'IC'):
            hops.append(dict(increment_ns=float(cells[1]), type=cells[3],
                             fanout=int(cells[4]), location=cells[5], element=cells[-1]))
    assert hops
    return dict(path=row, hops=hops)


assert sha(F / 'manifest.json') == 'cdb86dc7f3ba8c7e7a59263baa18bbb1067e074b77a4fadb87b091bb31ffea5d'
for name, expected in json.loads((F / 'manifest.json').read_text())['evidence_sha256'].items():
    assert sha(R / name) == expected, name
fit = json.loads((F / 'result.json').read_text())
assert not fit['comparative_physical_gate_pass'] and fit['m10k_budget_pass']
O.mkdir(exist_ok=False)
paths, families, details = {}, {}, {}
for corner in ('slow_85', 'slow_0'):
    report = T / (corner + '_setup.rpt')
    rows = parse(report)
    assert len(rows) == 20
    paths[corner] = rows
    buckets = collections.defaultdict(list)
    for row in rows:
        buckets[family(row)].append(row)
    families[corner] = {
        name: dict(exported_paths=len(group),
                   exported_below90=sum(r['period_bound_mhz'] < 90 for r in group),
                   worst=min(group, key=lambda r: r['slack_ns']))
        for name, group in buckets.items()
    }
    details[corner] = {name: detail(report, value['worst'])
                       for name, value in families[corner].items()}
put(O / 'paths.json', paths)
put(O / 'mapped-cones.json', details)
put(O / 'result.json', dict(
    readonly=True, new_sta_invoked=False, new_fit_invoked=False,
    source_changes=False, fit_manifest_sha256=sha(F / 'manifest.json'),
    physical_result=fit, top20_families=families,
    previous_specific_target_families_absent_from_top20=True,
    complete_below90_census=False,
    limits=[
        'These are only the existing 20 worst setup paths per slow corner; both lists stop below90MHz, so this is not an exhaustive failure census.',
        'The previous branch, load-hit, RS3-tag, frontend-ready and GPU owner-control families are absent from these top20 reports. This audit does not establish their endpoint-specific timing.',
        'Current exported limiting families are floating RS1 forwarding, GPU queue read/AWLEN controls, banked-cache read address, and fragment light modulation. No further fit, seed or source edit is selected by this report.',
        'The larger GPU cache physically fits:304M10Ks. Resource success does not qualify the failed >90MHz comparative timing gate or configured100MHz timing.'
    ]))
shutil.copy2(__file__, O / 'recipe.py')
files = [p for p in O.iterdir() if p.is_file()] + [
    Path(__file__), R / 'tools/experiments/os30_fps25_20261005/phase10_timing_paths_parse.py',
    F / 'manifest.json', F / 'result.json', T / 'slow_85_setup.rpt', T / 'slow_0_setup.rpt'
]
put(O / 'manifest.json', dict(readonly=True, timing_closed=False,
    evidence_sha256={str(p.relative_to(R)): sha(p) for p in files}))
print(json.dumps(families, indent=2))
print('MANIFEST', sha(O / 'manifest.json'))
