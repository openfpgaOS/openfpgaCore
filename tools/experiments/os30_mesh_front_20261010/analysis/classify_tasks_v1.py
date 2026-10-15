#!/usr/bin/env python3
"""Conservative mesh-task classification from immutable passive native data."""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def write(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def rows(p):
    return [{k:(int(v) if v.isdigit() else v) for k,v in row.items()} for row in csv.DictReader(p.open(),delimiter='\t')]

def main(label):
    out=HERE/label;r=out/'run'
    assert read(out/'passivity.json')['status']=='PASS_EXACT_PASSIVITY'
    inputs=[out/'passivity.json',Path(__file__).resolve()]
    def load(name):
        p=r/name;inputs.append(p);return rows(p)
    entries={x['task']:x for x in load('task-entries.tsv')}
    raw={x['task']:x for x in load('task-raw.tsv')}
    verts=collections.defaultdict(dict);commands=collections.defaultdict(list)
    for x in load('task-raw-vertices.tsv'):verts[x['task']][x['slot']]=x
    for x in load('task-commands.tsv'):commands[x['task']].append(x)
    calls=load('task-calls.tsv');call_entry={x['serial']:x for x in calls if x['event']=='entry'}
    call_return={x['serial']:x for x in calls if x['event']=='return'}
    assert set(call_entry)==set(call_return)
    call_costs=collections.defaultdict(collections.Counter)
    if (r/'task-call-costs.tsv').exists():
        for x in load('task-call-costs.tsv'):call_costs[x['call_serial']][x['function']]+=x['charged_cycles']
    lifetime=collections.defaultdict(collections.Counter)
    phase_totals=collections.Counter()
    for x in load('task-costs.tsv'):
        lifetime[x['task']][x['function']]+=x['charged_cycles'];phase_totals[x['phase']]+=x['charged_cycles']
    # Validate arithmetic consistency against the unchanged global PC profile.
    for phase,total in phase_totals.items():
        pcfile=r/f'pc-phase-{phase}.csv';inputs.append(pcfile)
        expected=sum(int(x['cycles_since_previous_retirement']) for x in csv.DictReader(pcfile.open()))
        assert total==expected,(phase,total,expected)

    summaries={};records=[]
    for phase in (1,2):
        counts=collections.Counter();budget=collections.defaultdict(collections.Counter)
        for ident,t in entries.items():
            if t['phase']!=phase:continue
            counts['load_calls']+=1
            stream=commands[ident]
            assert stream and stream[0]['w0']>>24==1
            assert ((stream[0]['w0']>>12)&255)==t['count']
            assert ((stream[0]['w0']>>1)&127)-t['count']==t['first']
            assert stream[0]['w1']==t['source']
            tris=[]
            for x in stream[1:]:
                op=x['w0']>>24;assert op in (5,6)
                tris.append([((x['w0']>>b)&255)//2 for b in (16,8,0)])
                if op==6:tris.append([((x['w1']>>b)&255)//2 for b in (16,8,0)])
            if not tris:continue
            counts['tight_spans']+=1;counts['tight_triangles']+=len(tris)
            rec=dict(task=ident,phase=phase,dl=t['dl'],source=t['source'],first=t['first'],count=t['count'],
                     triangle_count=len(tris),material_ready=t['material_ready'],material_elig=t['material_elig'],
                     geometry_mode=t['geometry_mode'],triangles=tris,end=stream[-1]['address']+8,
                     raw_success=False,conservative_geometry_supported=False)
            if ident in raw and not raw[ident]['unsupported'] and not raw[ident]['dma_error']:
                rec['raw_success']=True
                counts['raw_success_spans']+=1;counts['raw_success_triangles']+=len(tris)
                assert len(verts[ident])==t['count']==raw[ident]['count']
                assert bool(raw[ident]['all_inside'])==all(v['clip']==0 for v in verts[ident].values())
                exceptions=collections.Counter();trivial=0
                for tri in tris:
                    if any(i<t['first'] or i>=t['first']+t['count'] for i in tri):
                        exceptions['mixed_slot']+=1;continue
                    vv=[verts[ident][i-t['first']] for i in tri]
                    if vv[0]['clip']&vv[1]['clip']&vv[2]['clip']:
                        trivial+=1;continue
                    if any(v['clip'] for v in vv):exceptions['partial_clip_before_cull']+=1;continue
                    if any(not(0x00800000<=v['w']<0x46fffe00 and 0x00800000<=v['invw']<0x7f800000) for v in vv):
                        exceptions['w_or_reciprocal']+=1;continue
                    if vv[0]['w']==vv[1]['w']==vv[2]['w']:exceptions['equal_w_before_cull']+=1
                rec.update(conservative_exceptions=dict(exceptions),trivial_clip_rejected_triangles=trivial,
                           conservative_geometry_supported=not exceptions)
                counts['trivial_clip_reject_triangles']+=trivial
                for why,n in exceptions.items():counts[why+'_triangles']+=n;counts[why+'_tasks']+=1
                if not exceptions:
                    counts['conservative_geometry_supported_tasks']+=1
                    counts['conservative_geometry_supported_triangles']+=len(tris)
                    key='warm_material_nonzero_memo' if t['material_ready'] and t['material_elig'] else 'cold_or_ineligible_material'
                    rec['material_class']=key;counts[key+'_tasks']+=1;counts[key+'_triangles']+=len(tris)
            rec['load_lifetime_disjoint_pc_charges']=dict(lifetime[ident])
            # Exact call intervals associated with the immediate source span.
            # A later state change under the same LOAD does not get credited.
            exact=collections.Counter();included=[]
            for serial,ce in call_entry.items():
                if ce['task']!=ident:continue
                if ce['function_pc']==0x104cf980 or (ce['function_pc']==0x104d2640 and t['dl']+8<=ce['a0']<rec['end']):
                    exact.update(call_costs[serial]);included.append(serial)
            rec['exact_prefix_call_serials']=included
            rec['prefix_call_disjoint_pc_charges']=dict(exact)
            groups=['all_tight']
            if rec['raw_success']:groups.append('raw_success')
            if rec['conservative_geometry_supported']:
                groups+=['conservative_geometry_supported',rec['material_class']]
            for group in groups:budget[group].update(exact)
            records.append(rec)
        summaries[str(phase)]=dict(counts=dict(counts),prefix_call_pc_charges={k:dict(v) for k,v in budget.items()},
                                   whole_phase_charged_cycles=phase_totals[phase])
    report=dict(status='PASS_PASSIVE_NATIVE_TASK_CLASSIFICATION',summaries=summaries,
      scope=[
        'Conservative geometry admission, not exact cull-survivor counts. Trivial clip rejection is recognized; partial clip or equal W may still be culled before requiring CPU fallback.',
        'A nonzero warm material memo is only a necessary proxy; alpha/LOD/material source restrictions are not fully classified by these fields.',
        'Counts include triangles that culling may discard. Shared FP arithmetic can introduce further exceptional repairs.',
        'Whole LOAD-lifetime PC costs include later state changes and are not the bounded task cost.',
        'V2 prefix-call charges include one gfx_sp_vertex call plus indexed calls starting within the immediate span. These selected calls do not nest. Charged retirement gaps are disjoint within this selection, not guaranteed removable wall time.',
        'A gfx_sp_vertex call can submit a previously deferred batch while its raw LOAD runs; those packet charges are reported by function, not falsely reassigned to this task topology.',
        'First-span CPU setup, actual DMA/FMA work, descriptor traffic, exceptional repair and output queue backpressure remain necessary in a hardware implementation.',
        'Only two prepared intervals from the fixed exact diagnostic ELF; no FPS or area/timing prediction.'
      ],inputs_sha256={str(p):sha(p) for p in inputs})
    write(out/'classification.json',report);write(out/'tasks-classified.json',records)
    print(json.dumps({phase:x['counts'] for phase,x in summaries.items()},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('label');a=p.parse_args();main(a.label)
