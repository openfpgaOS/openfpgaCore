#!/usr/bin/env python3
"""Keep potential task coverage separate from necessary hardware service."""
import collections
import csv
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
OUT=HERE/'native-task-v3';RUN=OUT/'run'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def rows(p):return list(csv.DictReader(p.open(),delimiter='\t'))

assert read(OUT/'passivity.json')['status']=='PASS_EXACT_PASSIVITY'
tasks=read(OUT/'tasks-classified.json')
raw={int(x['task']):int(x['cycle']) for x in rows(RUN/'task-raw.tsv')}
start={int(x['task']):int(x['cycle']) for x in rows(RUN/'task-raw-contexts.tsv')}
assert set(raw)==set(start)
call_costs=collections.defaultdict(list)
for row in rows(RUN/'task-call-costs.tsv'):call_costs[int(row['call_serial'])].append(row)
focus={'gfx_cpu_indexed_run','load_alias_copy','gfx_gpu_vtx_cache_batch_impl',
       'gfx_gpu_cram_vertex_source','gfx_sp_vertex'}
summaries={}
for phase in (1,2):
    groups={}
    for group in ('warm_material_nonzero_memo','cold_or_ineligible_material'):
        selected=[t for t in tasks if t['phase']==phase and t.get('material_class')==group]
        ids={t['task'] for t in selected}
        serials={serial for t in selected for serial in t['exact_prefix_call_serials']}
        charged=collections.Counter();retired=collections.Counter()
        for serial in serials:
            for row in call_costs[serial]:
                charged[row['function']]+=int(row['charged_cycles'])
                retired[row['function']]+=int(row['retired'])
        groups[group]={
          'tasks':len(selected),'input_triangles':sum(t['triangle_count'] for t in selected),
          'prefix_call_charged_cycles':sum(charged.values()),
          'prefix_call_retired':sum(retired.values()),
          'raw_start_to_done_cycles':sum(raw[i]-start[i] for i in ids),
          'raw_driver_charged_cycles':charged['load_alias_vertex'],
          'raw_driver_retired':retired['load_alias_vertex'],
          'focused_cpu_charged_cycles_excluding_raw_driver':sum(charged[f] for f in focus),
          'focused_cpu_retired_excluding_raw_driver':sum(retired[f] for f in focus),
          'focused_by_function':{f:{'charged_cycles':charged[f],'retired':retired[f]} for f in sorted(focus)},
          'all_functions':{f:{'charged_cycles':charged[f],'retired':retired[f]} for f in sorted(charged)}
        }
    summaries[str(phase)]=groups
inputs=[Path(__file__).resolve(),OUT/'passivity.json',OUT/'classification.json',OUT/'tasks-classified.json',
        RUN/'task-raw.tsv',RUN/'task-raw-contexts.tsv',RUN/'task-call-costs.tsv',RUN/'task-calls.tsv']
report={
 'status':'PASS_BOUND_TASK_COST_LEDGER_NOT_A_SPEEDUP_ESTIMATE',
 'summaries':summaries,
 'interpretation':[
  'The selected functions are distinct PC ranges and the selected gfx_sp_vertex/indexed-call intervals do not nest. Canonical copy, decoding/admission/preparation and packet construction are real CPU work covered by a broader task boundary.',
  'The complete load_alias_vertex function is excluded from the focused CPU ledger so raw completion polling is not presented as arithmetic work that disappears.',
  'Raw start-to-done service remains necessary in the proposed worker. It is reported separately, never subtracted from CPU totals: raw service overlaps submission of an older prepared batch.',
  'Task-local function costs are charged retirement gaps and include memory and FIFO waits. The CPU need not execute those instructions after offload, but the resulting frame-time improvement depends on hardware service, contention and downstream raster progress.',
  'Pending GPU submission can execute inside the next gfx_sp_vertex. Those packet charges are classified by execution interval; they are not proof that the packet data belongs to the selected task.',
  'Material readiness plus nonzero eligibility is only a necessary proxy. Alpha/LOD restrictions and exact culling can still change actual accepted task coverage.',
  'The cold-or-ineligible group includes texture/material work that a geometry worker cannot simply delete. Full function charge minus raw driver is not an acceleration estimate.',
  'The data supports evaluating a broader functional task interface; it does not justify physical promotion or a promised FPS gain.'
 ],
 'inputs_sha256':{str(p):sha(p) for p in inputs}
}
(OUT/'cost-ledger.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'warm_groups':{k:{f:v[f] for f in ('tasks','focused_cpu_charged_cycles_excluding_raw_driver','focused_cpu_retired_excluding_raw_driver','raw_start_to_done_cycles')} for k,v in summaries['2'].items()}},indent=2))
