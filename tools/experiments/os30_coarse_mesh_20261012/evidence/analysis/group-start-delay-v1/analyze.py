#!/usr/bin/env python3
"""Passive AJ03 publication delay; no counterfactual cycle credits."""
import bisect
import collections
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
C=HERE.parents[1]
V=C/'validation'
R=V/'candidate-v1/run'
HOOK=V/'independent_lifetime_v1.py'
spec=importlib.util.spec_from_file_location('checker',HOOK)
check=importlib.util.module_from_spec(spec);spec.loader.exec_module(check)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert json.loads((R.parent/'check-independent-v1.json').read_text())['status'].startswith('PASS_AJ03_')
events=check.rows(R/'async-events.tsv')
calls=check.rows(R/'vtx-calls.tsv')
commands=check.uploads(R)
mmio=check.mmio(R)
pubs=[cy for cy,k,a,v in mmio if k=='GPU' and a==0 and v==8]
acquires=[cy for cy,k,a,v in mmio if k=='GEO' and a==0x2d and v==1]
takes={r['group_request_seq']:r for r in events if r['group_take']}
members={r['job_seq']:r for r in events if r['job_take']}
raws={r['descriptor_serial']:r for r in events if r['raw_start'] and not r['legacy']}
with (R/'gpu-profile-timeline.csv').open() as f:
    timeline=[{k:int(v) for k,v in r.items()} for r in csv.DictReader(f)]

def idle_bounds(lo,hi):
    lower=upper=0
    for r in timeline:
        n=max(0,min(hi,r['end'])-max(lo,r['begin']))
        if not n:continue
        lower+=max(0,r['idle_empty']-(r['cycles']-n))
        upper+=min(r['idle_empty'],n)
    return lower,upper

def state_overlap(lo,hi):
    counts=collections.Counter()
    for a,b in zip(events,events[1:]):
        n=max(0,min(hi,b['cycle'])-max(lo,a['cycle']))
        if n:
            if a['gpu_state']==0:counts['raster_state_IDLE']+=n
            if not a['active'] and not a['pending'] and not a['output_pending'] and not a['group_active']:counts['worker_without_job']+=n
            if a['lease_granted']:counts['legacy_lease_granted']+=n
    return dict(counts)

records=[]
for n,u in enumerate(commands):
    if u['op']!=0x65:continue
    seq,pointer,count,*_=u['data'];member=members[seq];take=takes[seq]
    candidates=[(i,c) for i,c in enumerate(calls) if c['phase']==member['phase'] and
        (c['a0'],c['a1'],c['a2'],c['s1']+8)==(member['job_vtx_w0']>>12&255,0,member['job_cpu_vertex'],member['job_cpu_commands'])]
    assert len(candidates)==1
    i,head=candidates[0];next_call=calls[i+1]['cycle'] if i+1<len(calls) else None
    assert commands[n-1]['op']==0x63
    context_start=commands[n-1]['cycle']
    publish=pubs[bisect.bisect_right(pubs,u['end'])]
    assert head['cycle']<context_start<u['cycle']<u['end']<publish<take['cycle']<member['cycle']<raws[seq]['cycle']
    delayed=next_call is not None and next_call<context_start
    record={'phase':member['phase'],'firstseq':seq,'members':count,'CPU_call':i,
        'CPU_call_cycle':head['cycle'],'next_CPU_call_cycle':next_call,
        'context_upload_start':context_start,'GROUP_upload_start':u['cycle'],'GROUP_upload_end':u['end'],
        'publish_cycle':publish,'group_take_cycle':take['cycle'],'first_member_take':member['cycle'],'first_raw_start':raws[seq]['cycle'],
        'head_to_group_take':take['cycle']-head['cycle'],
        'head_to_first_raw':raws[seq]['cycle']-head['cycle'],
        'publish_to_group_take':take['cycle']-publish,
        'group_take_to_first_raw':raws[seq]['cycle']-take['cycle'],
        'group_unuploaded_after_next_CPU_call':delayed}
    if delayed:
        record['known_staged_delay_before_upload']=context_start-next_call
        record['known_staged_delay_before_publish']=publish-next_call
        record['idle_empty_during_known_staged_delay_bounds']=idle_bounds(next_call,publish)
        record['overlap_during_known_staged_delay']=state_overlap(next_call,publish)
        following=acquires[bisect.bisect_right(acquires,publish)]
        record['next_legacy_acquire_cycle']=following
        record['publish_to_legacy_acquire']=following-publish
    records.append(record)

summary={}
for phase in (1,2):
    rs=[r for r in records if r['phase']==phase]
    delayed=[r for r in rs if r['group_unuploaded_after_next_CPU_call']]
    summary[str(phase)]={'groups':len(rs),'unuploaded_after_next_CPU_call':len(delayed)}
    for field in ('head_to_group_take','head_to_first_raw','publish_to_group_take','group_take_to_first_raw'):
        values=sorted(r[field] for r in rs)
        summary[str(phase)][field]={'sum_per_group_latencies':sum(values),'median':values[len(values)//2],'max':max(values)}
    for field in ('known_staged_delay_before_upload','known_staged_delay_before_publish','publish_to_legacy_acquire'):
        values=sorted(r[field] for r in delayed)
        summary[str(phase)][field]={'sum_per_group_latencies':sum(values),'median':values[len(values)//2] if values else None,'max':max(values) if values else None}
    # Merge overlapping waits before attributing any raster idle duration.
    intervals=[]
    for r in sorted(delayed,key=lambda r:r['next_CPU_call_cycle']):
        lo,hi=r['next_CPU_call_cycle'],r['publish_cycle']
        if intervals and lo<=intervals[-1][1]:intervals[-1][1]=max(hi,intervals[-1][1])
        else:intervals.append([lo,hi])
    summary[str(phase)]['known_staged_delay_union_cycles']=sum(hi-lo for lo,hi in intervals)
    summary[str(phase)]['idle_empty_within_delay_union_bounds']=[sum(idle_bounds(lo,hi)[i] for lo,hi in intervals) for i in (0,1)]

inputs=[Path(__file__),HOOK,R.parent/'check-independent-v1.json',R/'async-events.tsv',R/'vtx-calls.tsv',R/'mmio.tsv',R/'commands.bin',R/'gpu-profile-timeline.csv',C/'work-app/src/sdk/include/of_gpu.h',C/'work-app/src/sm64/sm64/src/pc/gfx/gfx_gpu.c']
result={'status':'PASS_PASSIVE_GROUP_PUBLICATION_TIMING','phases':summary,'groups':records,
    'limits':['The CPU LOAD-entry to hardware-entry interval includes real CPU group construction and intervening work, not just publication delay.',
              'A following actual CPU LOAD call before any context upload proves the preceding GROUP was already staged but unsent. The interval begins after preparation, so it is a lower bound on staging delay.',
              'GPU idle_empty bounds use4096-cycle timeline bins; raster_state_IDLE additionally includes cache maintenance.',
              'Per-group latencies can overlap. Only merged-union counts avoid double counting.',
              'A near-following legacy acquire plus the frozen acquire helper supports attribution to its forced kick; MMIO itself does not record the caller PC.',
              'No time here is automatically recoverable: earlier publication may still wait behind older raster work or a legacy lease.'],
    'inputs_sha256':{str(p):sha(p)for p in inputs}}
(HERE/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(summary,indent=2))
