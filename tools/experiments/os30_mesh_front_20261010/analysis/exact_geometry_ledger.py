#!/usr/bin/env python3
"""Join exact cull-first scene outcomes to bounded native CPU call charges."""
from pathlib import Path
import argparse,collections,csv,hashlib,json
H=Path(__file__).resolve().parent;M=H.parent;T=M/'review/native-task-v3';R=T/'run'
p=argparse.ArgumentParser();p.add_argument('--outcomes',type=Path,required=True);p.add_argument('--out',type=Path,default=H/'exact-geometry-ledger.json');args=p.parse_args();args.outcomes=args.outcomes.resolve();args.out=args.out.resolve()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def rows(p):
 with p.open() as f:return list(csv.DictReader(f,delimiter='\t'))
assert read(T/'passivity.json')['status']=='PASS_EXACT_PASSIVITY'
outcome=read(args.outcomes)
# Explicit expected schema checked once the independently derived report exists.
source_tasks=read(T/'tasks-classified.json');tasks={t['task']:t for t in source_tasks}
raw={int(x['task']):int(x['cycle']) for x in rows(R/'task-raw.tsv')}
start={int(x['task']):int(x['cycle']) for x in rows(R/'task-raw-contexts.tsv')}
call_costs=collections.defaultdict(list)
for row in rows(R/'task-call-costs.tsv'):call_costs[int(row['call_serial'])].append(row)
focus={'gfx_cpu_indexed_run','load_alias_copy','gfx_gpu_vtx_cache_batch_impl','gfx_gpu_cram_vertex_source','gfx_sp_vertex'}
assert outcome['status']=='PASS_EXACT_GEOMETRY_OUTCOMES_BOUND_TO_CHAIN'
for path,want in outcome['inputs_sha256'].items():assert sha(Path(path))==want,path
exact=outcome['tasks'];assert len(exact)==638 and len({t['task'] for t in exact})==638
scene=read(M/'work-core/tests/scene-chain-v1/manifest.json');assert scene['status']=='PASS'
assert sum(t['accepted_geometry'] for t in exact)==scene['result']['accepted_geometry_tasks']
assert sum(t['input_triangles'] for t in exact)==scene['result']['triangles']
serial_owner={};detail=[]
for x in exact:
 t=tasks[x['task']];assert x['phase']==t['phase'] and x['input_triangles']==t['triangle_count'] and t['raw_success']
 for serial in t['exact_prefix_call_serials']:
  assert serial not in serial_owner,(serial,serial_owner.get(serial),x['task']);serial_owner[serial]=x['task']
 charge=collections.Counter();retired=collections.Counter()
 for serial in t['exact_prefix_call_serials']:
  for row in call_costs[serial]:charge[row['function']]+=int(row['charged_cycles']);retired[row['function']]+=int(row['retired'])
 assert dict(charge)==t['prefix_call_disjoint_pc_charges'],x['task']
 d={k:x[k] for k in ('task','phase','accepted_geometry','fallback_reason','input_triangles','cpu_clip_reject','cpu_cull_reject','cpu_kept_before_scope','emitted_triangles','fully_rejected_accepted')}
 d.update(material_group='warm_material_nonzero_memo' if t['material_ready'] and t['material_elig'] else 'cold_or_ineligible_material',material_ready=t['material_ready'],material_elig=t['material_elig'],conservative_geometry_supported=t['conservative_geometry_supported'],newly_accepted_after_culling=x['accepted_geometry'] and not t['conservative_geometry_supported'],raw_start_to_done_cycles=raw[x['task']]-start[x['task']],exact_prefix_call_serials=t['exact_prefix_call_serials'],charges=dict(charge),retired=dict(retired))
 assert not t['conservative_geometry_supported'] or x['accepted_geometry'],x['task']
 detail.append(d)
def summarize(selected):
 c=collections.Counter();r=collections.Counter()
 for t in selected:c.update(t['charges']);r.update(t['retired'])
 return {'tasks':len(selected),'input_triangles':sum(t['input_triangles'] for t in selected),'emitted_triangles':sum(t['emitted_triangles'] for t in selected),'fully_rejected_accepted_tasks':sum(t['fully_rejected_accepted'] for t in selected),'fully_rejected_accepted_input_triangles':sum(t['input_triangles'] for t in selected if t['fully_rejected_accepted']),'newly_accepted_after_culling_tasks':sum(t['newly_accepted_after_culling'] for t in selected),'newly_accepted_after_culling_input_triangles':sum(t['input_triangles'] for t in selected if t['newly_accepted_after_culling']),'raw_start_to_done_cycles':sum(t['raw_start_to_done_cycles'] for t in selected),'raw_driver_charged_cycles':c['load_alias_vertex'],'raw_driver_retired':r['load_alias_vertex'],'prefix_call_charged_cycles':sum(c.values()),'prefix_call_retired':sum(r.values()),'focused_cpu_charged_cycles_excluding_raw_driver':sum(c[f] for f in focus),'focused_cpu_retired_excluding_raw_driver':sum(r[f] for f in focus),'focused_by_function':{f:{'charged_cycles':c[f],'retired':r[f]} for f in sorted(focus)}}
summaries={}
for phase in (1,2):
 summaries[str(phase)]={}
 for outcome_name in ('accepted_geometry','fallback_partial_clip','fallback_W'):
  condition=lambda t:t['accepted_geometry'] if outcome_name=='accepted_geometry' else t['fallback_reason']==(8 if outcome_name=='fallback_partial_clip' else 9)
  selected=[t for t in detail if t['phase']==phase and condition(t)]
  summaries[str(phase)][outcome_name]={'total':summarize(selected),**{group:summarize([t for t in selected if t['material_group']==group]) for group in ('warm_material_nonzero_memo','cold_or_ineligible_material')}}
inputs=[Path(__file__).resolve(),args.outcomes,T/'passivity.json',T/'tasks-classified.json',T/'cost-ledger.json',R/'task-raw.tsv',R/'task-raw-contexts.tsv',R/'task-call-costs.tsv',R/'task-calls.tsv',M/'work-core/tests/scene-chain-v1/manifest.json']
report={'status':'PASS_EXACT_GEOMETRY_CPU_COST_JOIN_NOT_A_SPEEDUP_ESTIMATE','summaries':summaries,'selected_tasks':len(detail),'distinct_prefix_calls':len(serial_owner),'focused_functions':sorted(focus),'task_details':[{k:v for k,v in t.items() if k not in ('charges','retired')}|{'focused_cpu_charged_cycles':sum(t['charges'].get(f,0) for f in focus),'focused_cpu_retired':sum(t['retired'].get(f,0) for f in focus)} for t in detail],'interpretation':[
 'Geometry admission follows the exact current CPU cull oracle before testing surviving triangles for clip/W scope. Passing scene-chain RTL independently compares every task outcome and survivor order; prior conservative pre-cull scope counts are refined here.',
 'Material readiness and nonzero eligibility at LOAD are necessary proxies only. Actual shader, alpha, LOD, texture side effects, viewport and fallback bindings remain unresolved; accepted geometry is not full renderer admission.',
 'Focused functions occupy distinct PC ranges in disjoint selected gfx_sp_vertex/indexed-call intervals. Charges are retirement gaps and include memory/FIFO stalls, not pure compute cycles or causal frame-time savings.',
 'The entire load_alias_vertex driver is excluded from focused work. Raw start-to-done service is reported separately; it remains necessary and can overlap CPU submission, so it must not be subtracted from or added to the focused CPU charge as a speedup estimate.',
 'A selected next-LOAD call can submit an older pending batch. Packet-building charges are associated with execution intervals, not proof that every packet logically belongs to the accepted task.',
 'Fully rejected accepted tasks emit no draws and can avoid material preparation, but their listed LOAD-time material memo is retained only for consistent grouping.',
 'Cold/ineligible material tasks require work that the geometry worker cannot simply delete. No integrated application, full-frame worker timing, FPGA fit or FPS improvement is demonstrated by this ledger.'
 ],'inputs_sha256':{str(p):sha(p) for p in inputs}}
assert not args.out.exists(),f'Refusing to overwrite frozen {args.out}'
args.out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'warm':summaries['2']['accepted_geometry']},indent=2))
