#!/usr/bin/env python3
"""Freeze a measured conclusion and assembly selection; never fit or deploy."""
from pathlib import Path
import argparse
import hashlib
import json

C = Path(__file__).resolve().parent.parent
P = C.parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--version', type=int, required=True)
args = parser.parse_args()
assert args.version == 4, 'This conclusion includes the completed F28-specific audit.'
inputs = {}

def read(path):
    path = Path(path).resolve()
    inputs[str(path)] = sha(path)
    return json.loads(path.read_text())

def put(path, value):
    assert not path.exists(), path
    path.write_text(json.dumps(value, indent=2)+'\n')

rows, strict, fixtures = [], {}, []
for v in range(1, args.version+1):
    app_name = 'work-app' if v == 1 else f'work-app-v{v}'
    app = read(C/app_name/f'evidence/app-build-v{v}/build-result.json')
    phases = read(C/f'analysis/phases-v{v}.json')['phases']
    cc = [phase['control']['cycles'] for phase in phases]
    gc = [phase['candidate']['cycles'] for phase in phases]
    checker_version = 1 if v == 1 else (2 if v <= 3 else 4)
    has_single = v <= 2 or (C/f'validation/single-v{v}/run/boundaries.tsv').exists()
    roles = ['control', 'candidate'] + (['single'] if has_single else [])
    for role in roles:
        path = C/f'validation/{role}-v{v}/check-independent-v{checker_version}.json'
        receipt = read(path)
        assert receipt['status'].startswith('PASS'), path
        strict[f'f{24+v}-{role}'] = str(path)
    candidate = read(Path(strict[f'f{24+v}-candidate']))['async_lifetime']
    phases_lifetime = candidate['phases']
    single = None
    if has_single:
        p = C/f'validation/single-v{v}/run/boundaries.tsv'
        inputs[str(p)] = sha(p)
        b = [[int(n) for n in line.split()] for line in p.read_text().splitlines()]
        single = [b[i+1][1]-b[i][1] for i in range(2)]
    row = dict(app=f'F{24+v}', elf_sha256=app['elf_sha256'],
               control_cycles=cc, aj02_single_cycles=single, aj03_group_cycles=gc,
               cycle_change_percent=[100*(g/c-1) for g,c in zip(gc,cc)],
               combined_change_percent=100*(sum(gc)/sum(cc)-1),
               submissions=phases_lifetime,
               original_cpu_load_calls=candidate['original_LOAD_calls'],
               actual_cpu_load_calls=candidate['actual_CPU_LOAD_calls'],
               original_raw_loads=candidate['exact_raw_contexts'],
               legacy_raw_loads=candidate['legacy_raw_contexts'])
    if single:
        row['change_from_same_elf_aj02_percent'] = [100*(g/s-1) for g,s in zip(gc,single)]
    rows.append(row)
    fixtures.append(str(C/f'validation/fixture-v{v}'))

selected = rows[-1]
outcome = 'MEASURED_GAIN' if all(x < 0 for x in selected['cycle_change_percent']) else 'NO_GAIN'
audit_paths = [
    'analysis/admission-v1/result.json',
    'analysis/span-coverage-v1/result.json',
    'analysis/span-coverage-v1/admission-join.json',
    'analysis/caller-liveness-v1/review.json',
    'analysis/caller-liveness-v1/native-traversal/result.json',
    'analysis/state-liveness-v1/source-audit-v1.json',
    'analysis/state-liveness-v1/native-traversal-v2/final.json',
    'analysis/group-start-delay-v1/result.json',
    'design/broad-handback-review-v1/result.json',
    'physical/prepared-v1/inputs.json',
    'physical/review-v1.json',
    'physical/resource-source-audit-v1.json',
    'package/prior-verification-v1/result.json',
]
for name in audit_paths:
    read(C/name)
remaining = C/'analysis/remaining-submission-v1/result.json'
if remaining.exists():
    read(remaining)
read(C/'analysis/remaining-submission-v2/result.json')
for version in (1, 2, 3, 5):
    negative = read(C/f'validation/independent-negative-v{version}/result.json')
    assert negative['status'].startswith('PASS') and negative['measured_inputs_unchanged']
assert len(negative['cases']) == 22
inputs[str(Path(__file__).resolve())] = sha(Path(__file__))
conclusion = dict(
    outcome=outcome, selected_app=selected['app'], comparisons=rows,
    denominators='GROUP counts replace eligible async JOB packets, not every GPU command or every CPU vertex LOAD.',
    physical_fit=False, deployed=False,
    limits=[
        'Two bounded SM64 native CPU/raster/game/audio intervals with synthetic scan/audio services. No sustained Pocket FPS or Quake2/all-level claim.',
        'Each row uses the identical ELF, fixture and workload for control and candidate. Different rows have different ELF placement and are not causal software A/B measurements.',
        'Function attribution includes preceding retirement gaps. Overlapping hardware state counters must not be added into predicted savings.',
        'Physical overlay is prepared only. Baseline timing and resource counts do not qualify AJ03.',
    ], inputs_sha256=inputs)
put(C/'analysis/conclusion-v1.json', conclusion)

lines = [
    'AJ03 cached geometry groups: implementation and measured decision',
    '',
    f'Outcome: {outcome}. Selected source revision: {selected["app"]}.',
    'The CPU submits one cached descriptor list and a shared context for up to32',
    'consecutive admissible vertex/triangle jobs. Hardware fetches member descriptors,',
    'restores context after legacy preemption, transforms/culls, and sends results',
    'through the existing GPU command path. The producer skips internal CPU LOAD',
    'handlers. Source reuse still waits for true final completion.',
    '',
    'Measured native frame intervals (cycles; cold / warm)',
    'App     Same-ELF baseline core     AJ02 individual             AJ03 groups',
]
for row in rows:
    fmt = lambda values: ' / '.join(f'{x:,}' for x in values) if values else 'not run'
    lines.append(f'{row["app"]:7} {fmt(row["control_cycles"]):26} {fmt(row["aj02_single_cycles"]):26} {fmt(row["aj03_group_cycles"])}')
lines += ['', f'{selected["app"]} versus its same-ELF control: ' +
          ', '.join(f'{name} {v:+.3f}%' for name,v in zip(('cold','warm'),selected['cycle_change_percent'])) +
          f'; combined {selected["combined_change_percent"]:+.3f}%. Positive is slower.',
          'Only comparisons within one row isolate the hardware path.', '',
          'Submission reduction and its scope']
for row in rows:
    desc = []
    for phase, name in [('1','cold'),('2','warm')]:
        p = row['submissions'][phase]
        desc.append(f'{name}: {p["jobs"]} logical jobs in {p["CPU_requests"]} CPU requests ({p["groups"]} GROUP + {p["single_jobs"]} single)')
    lines.append(row['app']+': '+'; '.join(desc)+'.')
lines += [
    'F27 preserves all174 admitted logical jobs across both intervals while reducing',
    'the eligible warm requests76->27 and cold98->21. This is64.5% and78.6% fewer',
    'eligible requests. It still leaves250 legacy hardware LOADs warm. The warm CPU',
    'LOAD callback count is354->305 (13.8% fewer), so the larger percentages must',
    'not be presented as eliminating that fraction of all CPU graphics work.',
    '',
    'Implemented software refinements',
    'F25 adds GROUP descriptors, source-generation fences and exact group caching.',
    'F26 invalidates only groups intersecting a new repair hint and keeps the cold',
    'descriptor compiler out of the hot path. F27 publishes a complete GROUP',
    'immediately instead of waiting for the ordinary512-word lazy kick threshold.',
]
if args.version >= 4:
    lines += [
        'F28 adds a bounded, caller-specific dead-slot proof. It can follow one',
        'actual immutable caller continuation and nested immutable display lists,',
        'looking past16 audited vertex-independent state opcodes. Actual dispatch',
        'still executes every state command; GROUP still ends at state/list boundaries.',
        'The proof and dependent groups are keyed by the exact continuation.',
        'Mutable commands, later canonical reads, a second external return, scan/depth',
        'overflow, and SETOTHERMODE fog-flush operations remain rejection barriers.',
        'Runtime proves55 additional caller-dependent jobs across both intervals.',
        'Warm eligible submissions103->33 (68.0% fewer); actual CPU vertex callbacks',
        '354->284 (19.8% fewer). Legacy hardware LOADs remain223. Total CPU GPU-ring',
        'upload falls23,000->18,590 words versus the same-ELF baseline core.',
        'Warm frame time differs by only241 cycles (0.0052%): effectively equal.',
        'Cold frame time is12.904% slower. No worthwhile speedup is established.',
    ]
lines += [
    '', 'Why fewer requests alone did not make F27 faster',
    'Remaining legacy LOADs force repeated ownership and context setup. The warm',
    'trace proves overlap (CPU legacy LOADs start while prior output is draining),',
    'but removing publication calls alone does not remove geometry/material work',
    'or downstream GPU backpressure. A whole-group raw lock would delay existing',
    'CPU handoffs; terminal canonical handback also needs per-slot validity and',
    'failed-LOAD recovery because smaller LOADs preserve earlier high slots.',
    'Warm F27 performs4,912 geometry register writes versus2,459 for its control.',
    'Matrix/scalar context reprogramming adds2,269 writes; collapsing ACQUIRE to',
    'one transaction would remove only206 writes. Actual lease wait is15,267 cycles,',
    'so ownership residence must not be mistaken for CPU blocked time.',
    'F28 reduces that burden, but still makes4,575 geometry writes versus2,459:',
    'matrix2,496 vs1,136; scalar1,022 vs345; LOAD669 vs978; lease388 vs0.',
    'Its97 warm leases wait20,328 cycles in total. More buffering or fewer kick',
    'calls alone does not remove the remaining CPU admission/context work.',
    '', 'Validation and delivery',
    f'All{len(strict)} selected full native replay receipts pass exact image/depth,',
    'draw payload/state/order, source lifetime, physical descriptor/context/snapshot',
    'and true-completion checks. Known timestamp-only normalization is documented',
    'in the strict receipts. Tests and historical failures remain separately bound.',
    'Focused RTL:160 cases /133,895 checks, including malformed requests, reset,',
    'DMA errors, output backpressure, repair and priority preemption.',
    'Actual extracted-C ownership/cache tests and independent corruption tests are',
    'included with their exact source/build receipts. They do not replace hardware tests.',
    'F28 rejects all22 deliberately corrupted ownership/source/caller witnesses.',
    'F28 checker v3 initially could not distinguish four repeated immutable list',
    'sources. Those failures are preserved. Strengthened v4 requires a unique',
    'match against the complete independently consumed baseline raw context, then',
    'retains exact source, caller, sequence and all652 raw-context checks.',
    '',
    'No Quartus fit, full seed sweep, production update or Pocket deployment.',
    'The physical overlay retains90MHz/seed3/effort3 from the qualified baseline,',
    'but AJ03 area/timing are unmeasured. The baseline itself had only311 spare',
    'ALMs and4 spare M10Ks; estimated memory usage is not a fit result.',
    '', 'Limits', *conclusion['limits'], '',
    'Reproduce evidence inventory: python3 verify.py --patches --evidence',
    'Run portable tests outside this bundle: python3 run_tests.py --output /tmp/aj03-tests',
    'Native replay requires the separately retained, hash-bound game fixtures.',
]
report = C/'REPORT.txt'
assert not report.exists()
report.write_text('\n'.join(lines)+'\n')
put(C/'package/selection-final.json', dict(outcome=outcome, strict_receipts=strict,
    fixtures=fixtures, report=str(report), conclusion=str(C/'analysis/conclusion-v1.json'),
    physical_fit=False, deployed=False))
print(json.dumps(dict(outcome=outcome, selected=selected['app'],
    cycle_change_percent=selected['cycle_change_percent'], strict_receipts=len(strict)), indent=2))
