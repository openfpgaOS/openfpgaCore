#!/usr/bin/env python3
"""Read only the final composition's existing top-20 slow setup reports."""
import collections
import hashlib
import json
import re
import shutil
from pathlib import Path
from phase10_timing_paths_parse import parse

R = Path(__file__).resolve().parents[3]
P = R / 'docs/measurements/os30-fps25-20261005/phase10'
F = P / 'rvcpipe-gpu32-forward-texture-fit-v1'
O = P / 'rvcpipe-gpu32-forward-texture-paths-v2'
T = F / 'fit-seed2/job/src/fpga/targets/pocket/timing'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def put(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def family(row):
    a, b = row['from_node'], row['to_node']
    if 'BranchPlugin_logic_alu_btb_BAD_TARGET' in b:
        return 'cpu_branch_target_prediction_compare'
    if 'LsuL1Plugin_logic_WAYS_HITS' in a:
        return 'cpu_load_hit_selection_and_forward'
    if '_up_RD_PHYS_lane0' in a and '_up_float_RS3_lane0' in b:
        return 'cpu_float_rs3_tag_and_forward'
    if '|gpu_core:gpu|cmd_is_draw_indexed_tri' in a and '|gpu_core:gpu|sp_sZ' in b:
        return 'gpu_indexed_triangle_start_depth_load'
    if '|gpu_core:gpu|state.S_FRAG_PIPE' in a and '|gpu_core:gpu|dsp_a' in b:
        return 'gpu_fragment_state_dsp_operand_load'
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


assert sha(F / 'manifest.json') == 'e299f2c3b3c1842750a1fe2ce1172e16536eac015de3b2aa3fb3e309af64e5ea'
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
    previous_load_data_launch_absent_from_top20=True,
    previous_texture_tag_launch_absent_from_top20=True,
    complete_below90_census=False,
    limits=[
        'These are only the existing 20 worst setup paths per slow corner; both lists stop below90MHz, so this is not an exhaustive failure census.',
        'The previous load-data and texture-tag launches are absent from the exported top20; their new endpoint-specific timing has not been measured by this audit.',
        'Current limiting families include branch-target comparison, load-hit selection, floating RS3 forwarding, and GPU control-to-operand loads. No remedy, extra fit, seed or source edit is selected by this report.',
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
