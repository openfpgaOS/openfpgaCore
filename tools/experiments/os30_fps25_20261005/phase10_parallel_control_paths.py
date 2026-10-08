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
F = P / 'parallel-control-fit-v1'
O = P / 'parallel-control-paths-v1'
T = F / 'fit-seed2/job/src/fpga/targets/pocket/timing'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()


def put(p, value):
    with p.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')


def family(row):
    a, b = row['from_node'], row['to_node']
    if '|GSharePlugin_logic_mem_' in b or 'altsyncram:GSharePlugin_logic_mem_' in b:
        return 'cpu_gshare_read_address'
    if 'AlignerPlugin_logic_buffer_' in b:
        return 'cpu_hazard_ready_to_aligner'
    if '|gpu_core:gpu|m_wr_awaddr' in a and '|gpu_color_depth_cache:gpu_render_cache|slot' in b:
        return 'gpu_render_cache_aw_hit_to_slot_enable'
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


assert sha(F / 'manifest.json') == 'fb50ae4d110ac75bb22e7d5b5607a730d372b48172cb4c64c4a2af7228b49b7d'
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
    prior_cache_valid_feedback_and_gpu_light_queue_absent_from_top20=True,
    complete_below90_census=False,
    limits=[
        'Only the existing20 worst setup paths per slow corner; hot list still stops below90 while cold ranks13+ exceed90. Complete endpoint census belongs to the parent.',
        'Current top20 families are frontend hazard/ready to aligner, GShare read address and live GPU AW tag to render-cache slot enable.',
        'The cache path has5logic levels,10.792ns data delay and7.239ns routing. Its late endpoint is the slot FF enable; hot1 comparison and shared lookup-address mux precede it.',
        'Prior speculative-read and color-prefix families are absent from these top20 reports; no unreported path timing claim is made.',
        'No additional CAD or source changes were performed by this audit. Configured100MHz remains separate from the failed>90 comparative gate.'
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
