#!/usr/bin/env python3
"""Summarize passive, cycle-identical LOD6 lighting costs."""
import csv,json,re
from pathlib import Path
import phase10_lod6_light_profile as m
O=m.OUT;P=m.P;sha=m.sha;write=m.write
a=m.OLD/'run-window-8';b=O/'run-window-8'
for n in ('coupled.json','geometry-operations.csv','scene.csv','geometry.csv','culling.csv','frames.csv',*[f'frame_{i:05}.bin'for i in range(360,368)]):assert(a/n).read_bytes()==(b/n).read_bytes(),n
prepared=json.loads((O/'prepared.json').read_text())
for n,h in prepared['sources'].items():assert sha(O/n)==h,n
inv=json.loads((b/'inputs.json').read_text());assert inv['success']
for n,h in inv['results_sha256'].items():assert sha(b/n)==h,n
assert'FATAL'not in(b/'run.log').read_text()
pc={int(r['pc'],16):{k:int(v)for k,v in r.items()if k!='pc'}for r in csv.DictReader((b/'lighting-pc.csv').open())}
asm={}
for line in(P/'lod6-driver-v1/lighting-client.asm').read_text().splitlines():
 x=re.match(r'^([0-9a-f]{8}):\s+[0-9a-f]+\s+([^\s]+)',line)
 if x and int(x[1],16)in pc:asm[int(x[1],16)]=x[2]
assert all(k in asm for k,v in pc.items()if v['count'])
groups={}
for addr,v in pc.items():
 if not v['count']:continue
 op=asm[addr]
 if op.startswith('fmadd'):g='FMA'
 elif op.startswith('fmul'):g='FMUL'
 elif op.startswith('fcvt'):g='float_conversion'
 elif op.startswith(('fl','fs','fmv')):g='other_float'
 elif op in('lw','lb','lbu','lh','lhu'):g='integer_load'
 elif op in('sw','sb','sh'):g='integer_store'
 elif op.startswith('b')or op in('j','ret'):g='branch_jump'
 else:g='integer_alu'
 q=groups.setdefault(g,dict(instructions=0,penalty_cycles=0))
 q['instructions']+=v['count'];q['penalty_cycles']+=v['penalty']
c=json.loads((b/'lighting-counters.json').read_text());base=sum(v['count']for v in pc.values());assert base+sum(c[k]for k in c if k.startswith('pen_'))==c['cyc']
miss=pc[0x104ce71a]['count'];hit=pc[0x104ce852]['count'];positive=pc[0x104ce758]['count'];assert hit+miss==pc[0x104ce6e4]['count']
result=dict(passed=True,passive_run_cycle_and_render_identical=True,helper_calls=c['calls'],lit_vertices=hit+miss,memo_hits=hit,memo_misses=miss,memo_hit_percent=hit*100/(hit+miss),positive_dot_misses=positive,base_instruction_ms_per_frame=base/800000,total_ms_per_frame=c['cyc']/800000,penalty_ms_per_frame={k:c[k]/800000 for k in c if k.startswith('pen_')},instruction_groups=groups,source_first_byte=dict(pc='0x104ce6e4',opcode=asm[0x104ce6e4],dcache_misses=pc[0x104ce6e4]['dc_miss'],all_attributed_penalty_cycles=pc[0x104ce6e4]['penalty'],all_attributed_penalty_ms_per_frame=pc[0x104ce6e4]['penalty']/800000),all_helper_dcache_misses=sum(v['dc_miss']for v in pc.values()),limits=['All penalties are qsim model estimates, not direct native performance counters.','Per-PC penalty at first normal-byte load includes any dependency penalty at that instruction; 0.910734ms is an upper bound for eliminating its memory wait, not a guaranteed speedup.','No code, cache capacity, hardware arithmetic or ordering changed; both timings and all eight actual outputs match the sealed candidate.'])
write(O/'result.json',result)
(O/'REPORT.txt').write_text('''LOD6 unchanged-ELF lighting diagnosis (eight frames360..367).

Passive entry/exit model-counter snapshots and per-PC counts preserve every
cycle, operation trace, scene record and actualcolor/depth output ofLOD6.
CPU lightcost3.81246375ms =1.8747075ms instructionbase +0.93331375ms Dcache
+0.4359925ms redirects +0.52855ms dependency waits +0.02775ms float-to-int
+0.01215ms Icache. No native hardware performance-counter claim.

30336 litvertices:18232 memohits(60.10%),12104misses;7400positive-dotmisses.
The originalmemoalreadyeliminatesmostarithmetic. Remaining46408FMAs/8frames
include2dotFMAs/miss+3colorFMAs/positivemiss. The first source normalbyte
load accountsfor10731of11056Dcachemisses; its entire attributedpenalty is
0.91073375ms/frame. Thus randomnormalmemo accesses are notthememoryproblem:
lighting reintroducesthe24B-stride rawsource reads removedbyLOD5.

Even eliminatingallpenaltiesatthatfirstload leaves~0.519ms ofthe1.430ms
measuredregression, beforeanynewlookup/packingwork orchangedGPUoverlap.
A packednormalsidecarwouldneednewsourcecache/ownershipandcannotbyitself
justifyanotherclientbranch. Preservehardwarelightingwhilereducingitsarea
is the more direct target. No optimization wasimplementedinthisobserver.
''')
write(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts and p.name!='manifest.json'},helper_sha256={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},native_correctness_manifest_sha256='b1e8a9c7467b8371404439475128549ac2737fea867ea9b85f46ca4537cdb627',qualified_for_deployment=False))
print(json.dumps(result,indent=2));print('manifest',sha(O/'manifest.json'))
