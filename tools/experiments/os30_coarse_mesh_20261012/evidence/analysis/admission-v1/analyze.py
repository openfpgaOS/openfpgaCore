#!/usr/bin/env python3
"""Read-only source/call classification of the frozen F24 native replay."""
import collections,csv,hashlib,json,struct
from pathlib import Path
from elftools.elf.elffile import ELFFile

HERE=Path(__file__).resolve().parent
B=HERE.parents[2]/'priority-mesh-v1'
R=B/'validation/candidate-v2/run'
C=B/'validation/control-v2/run'
ELF=B/'work-app-v2/.obj/sm64-priority-mesh-v2-perfdiag/app.elf'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rows(p):
    with p.open() as f:return [{k:int(v,0) for k,v in r.items()} for r in csv.DictReader(f,delimiter='\t')]
with ELF.open('rb') as f:
    elf=ELFFile(f)
    segments=[(s['p_vaddr'],s.data()) for s in elf.iter_segments() if s['p_type']=='PT_LOAD']
    sy=elf.get_section_by_name('.symtab')
    low=sy.get_symbol_by_name('_start')[0]['st_value']
    high=sy.get_symbol_by_name('__EH_FRAME_BEGIN__')[0]['st_value']
def pair(a):
    if a<low or a>high-8 or a&7:return None
    for first,data in segments:
        if first<=a<=first+len(data)-8:return struct.unpack_from('<II',data,a-first)
    return None
def topology(p):
    v=pair(p)
    if not v:return 'nonimmutable',{}
    a,b=v;n=a>>12&255;end=a>>1&127
    if not n or n>32 or end!=n:return 'load_shape',{}
    ptr=p+8;commands=triangles=0
    while (v:=pair(ptr)) and v[0]>>24 in (5,6):
        a,b=v;num=1 if a>>24==5 else 2
        if commands==64 or triangles+num>64:return 'task_capacity',{}
        for w in ([a] if num==1 else [a,b]):
            if any((w>>s&255)//2>=n for s in (16,8,0)):return 'triangle_uses_survivor',{}
        commands+=1;triangles+=num;ptr+=8
    if not commands:return 'no_adjacent_triangles',{}
    if not v:return 'topology_end_range',{}
    meta={'next':ptr,'triangles':triangles,'commands':commands,'count':n}
    pending=set(range(n));q=ptr
    for steps in range(256):
        meta.update(suffix_stop=q,suffix_steps=steps,pending_slots=sorted(pending))
        v=pair(q)
        if not v:return 'suffix_range',meta
        a,b=v;op=a>>24
        if op==1:
            count=a>>12&255;end=a>>1&127
            if not count or count>32 or end<count or end>32:return 'suffix_load_shape',meta
            pending.difference_update(range(end-count,end))
            if not pending:return 'dead',meta
        elif op in (5,6):
            for w in ([a] if op==5 else [a,b]):
                if any((w>>s&255)//2>=32 or (w>>s&255)//2 in pending for s in (16,8,0)):
                    return 'suffix_reads_pending',meta
        else:
            meta['stop_opcode']=op
            return 'suffix_opcode',meta
        q+=8
    return 'suffix_limit',meta

calls=rows(R/'vtx-calls.tsv');reference_calls=rows(C/'vtx-calls.tsv')
assert [[c[k] for k in ('phase','a0','a1','a2','s1','ra')] for c in calls]==[[c[k] for k in ('phase','a0','a1','a2','s1','ra')] for c in reference_calls]
events=rows(R/'async-events.tsv')
jobs={r['job_seq']:r for r in events if r['job_take']}
repair_sequences={r['descriptor_serial'] for r in events if r['repair_pending']}
repair_keys={jobs[s]['job_cpu_commands']-8 for s in repair_sequences}
job_calls={(r['phase'],r['job_cpu_commands']-8):r for r in jobs.values()}
assert len(job_calls)==len(jobs)
raw_calls=set();call_index=0
for line in (C/'mmio.tsv').read_text().splitlines():
    cycle,kind,address,value=line.split();cycle=int(cycle)
    while call_index+1<len(reference_calls) and reference_calls[call_index+1]['cycle']<=cycle:call_index+=1
    if kind=='GEO' and int(address)==8 and int(value)&1:
        assert call_index not in raw_calls
        raw_calls.add(call_index)
details=[];cache=[None]*512;lookup_counts=collections.Counter()
for i,c in enumerate(calls):
    reason,meta=topology(c['s1'])
    route=reason
    lookup='not_immutable'
    if pair(c['s1']) is not None and c['s1']<=high-16:
        h=((c['s1']>>3)^(c['s1']>>13))&511
        lookup='probe_exhausted'
        for probe in range(16):
            slot=(h+probe)&511
            if cache[slot]==c['s1']:
                lookup='hit';break
            if cache[slot] is None:
                lookup='insert';cache[slot]=c['s1'];break
        lookup_counts[lookup]+=1
        if lookup=='probe_exhausted':route='topology_cache_probe_exhausted'
    if (c['phase'],c['s1']) in job_calls:route='async'
    elif c['phase']==2 and c['s1'] in repair_keys:route='repair_hint'
    details.append({'call':i,'phase':c['phase'],'command':c['s1'],'source':c['a2'],
        'count':c['a0'],'first':c['a1'],'raw_reference':i in raw_calls,
        'topology_reason':reason,'lookup':lookup,'route':route,**meta})
stats=struct.unpack('<16I',(R/'async-stats.bin').read_bytes())
mesh_stats=struct.unpack('<16I',(R/'mesh-stats.bin').read_bytes())
assert lookup_counts['hit']==mesh_stats[1]
assert stats[3]==0,'Dynamic LOAD/source rejection must be separately classified'
assert sum(r['route']=='dead' for r in details)==stats[2]
assert sum(r['route']=='suffix_opcode' for r in details)==stats[1]
for r in details:
    if r['route']=='dead':r['route']='material_admission'
summary={}
for phase in (1,2):
    d=[r for r in details if r['phase']==phase]
    summary[str(phase)]={
        'all_calls':dict(collections.Counter(r['route'] for r in d)),
        'raw_calls':dict(collections.Counter(r['route'] for r in d if r['raw_reference'])),
        'input_triangles':dict(collections.Counter({k:sum(r.get('triangles',0) for r in d if r['route']==k) for k in {r['route'] for r in d}})),
        'suffix_stop_opcodes':dict(collections.Counter(hex(r['stop_opcode']) for r in d if r['route']=='suffix_opcode')),
    }
inputs=[Path(__file__),ELF,R/'vtx-calls.tsv',C/'vtx-calls.tsv',C/'mmio.tsv',R/'async-events.tsv',R/'async-stats.bin',R/'async-hint-stats.bin',R/'mesh-stats.bin',B/'validation/candidate-v2/check-independent-v2.json']
result={'status':'PASS_READ_ONLY_ELF_AND_NATIVE_CALL_CLASSIFICATION','summary':summary,
    'material_route_meaning':'The remaining statically dead topology not queued is material admission: exact final counter equals96 and LOAD/source rejection counter is zero. Per-call cold/not-eligible/LOD/alpha subtype was not recorded and is not inferred.',
    'lookup_counts':dict(lookup_counts),
    'repair_keys':sorted(repair_keys),'details':details,'inputs_sha256':{str(p):sha(p) for p in inputs}}
(HERE/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(summary,indent=2))
