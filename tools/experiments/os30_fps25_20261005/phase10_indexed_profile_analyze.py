#!/usr/bin/env python3
"""Account exact unchanged-ELF indexed renderer instructions by audited blocks."""
import collections,csv,json,re
from pathlib import Path
import phase10_indexed_profile as m
O=m.OUT;P=m.P;sha=m.sha;write=m.write;a=m.OLD/'run-warm';b=O/'run-warm'
for n in ('coupled.json','geometry-operations.csv','scene.csv','geometry.csv','culling.csv','frames.csv',*[f'frame_{i:05}.bin'for i in range(360,368)]):assert(a/n).read_bytes()==(b/n).read_bytes(),n
for n,h in json.loads((O/'prepared.json').read_text())['sources'].items():assert sha(O/n)==h,n
inv=json.loads((b/'inputs.json').read_text());assert inv['success']and'FATAL'not in(b/'run.log').read_text()
for n,h in inv['results_sha256'].items():assert sha(b/n)==h,n
spec='''
1c00 1c60 entry_exit
1c60 1cd0 decode
1cd0 1d24 slot_clip
1d24 1db8 winding
1db8 1e16 eligibility
1e16 1e6a slot_clip
1e6a 1ef8 winding
1ef8 1f50 eligibility
1f50 2022 material
2022 2110 dirty_jobs
2110 2126 decode
2126 2196 prepare_scan
2196 21b2 uv
21b2 21b6 color
21b6 21bc quantize
21bc 21d8 depth_clamp
21d8 220c quantize
220c 2228 payload_store
2228 2232 prepare_scan
2232 2270 backend_dispatch
2270 22b8 entry_exit
22b8 22c4 uv
22c4 2308 color
2308 2376 fallback_clip
2376 2408 fallback_winding
2408 245a fallback_clip
245a 24e0 fallback_winding
24e0 2508 fallback_dispatch
2508 2510 eligibility
2510 2514 fallback_dispatch
2514 2528 slot_clip
2528 2534 dirty_jobs
2534 259c color
259c 25ac eligibility
25ac 25e0 color
25e0 2686 fallback_dispatch
2686 26b2 winding
26b2 26b6 fallback_clip
26b6 26c6 winding
26c6 26cc color
26cc 26d2 prepare_scan
26d2 2700 fallback_winding
2700 2704 fallback_dispatch
2704 2d10 material
'''
ranges=[(0x104d0000+int(l,16),0x104d0000+int(h,16),name)for l,h,name in (line.split()for line in spec.strip().splitlines())]
assert ranges[0][0]==0x104d1c00 and ranges[-1][1]==0x104d2d10
assert all(x[1]==y[0]for x,y in zip(ranges,ranges[1:]))
pc={int(r['pc'],16):{k:int(v)for k,v in r.items()if k!='pc'}for r in csv.DictReader((b/'indexed-pc.csv').open())};asm={}
for line in(O/'indexed.asm').read_text().splitlines():
 q=re.match(r'^([0-9a-f]{8}):\s+([^\s]+)\s*(.*)',line)
 if q:asm[int(q[1],16)]=dict(op=q[2],args=q[3])
assert all(k in asm for k in pc)
fields=list(next(iter(pc.values())).keys());stages={};opgroups={};categorized=[]
for addr,r in pc.items():
 name=next(n for lo,hi,n in ranges if lo<=addr<hi);d=stages.setdefault(name,collections.Counter());d.update(r)
 op=asm[addr]['op']
 if op.startswith(('fmadd','fmsub','fnmadd','fnmsub')):kind='fused_multiply_add'
 elif op.startswith('fmul'):kind='float_multiply'
 elif op.startswith('fcvt'):kind='float_convert'
 elif op.startswith(('flt','fle','feq','fclass')):kind='float_compare'
 elif op.startswith(('fadd','fsub')):kind='float_add_sub'
 elif op.startswith(('flw','fsw')):kind='float_memory'
 elif op.startswith('f'):kind='float_other'
 elif op in('lw','lh','lhu','lb','lbu'):kind='integer_load'
 elif op in('sw','sh','sb'):kind='integer_store'
 elif op.startswith('b')or op in('j','jal','jalr','ret'):kind='branch_call_return'
 else:kind='integer_alu'
 opgroups.setdefault(kind,collections.Counter()).update(r)
 categorized.append(dict(pc=f'{addr:08x}',stage=name,**asm[addr],**r))
for d in list(stages.values())+list(opgroups.values()):
 assert d['cyc']==d['count']+sum(v for k,v in d.items()if k.startswith('pen_'))
 assert d['pen_dep']==sum(v for k,v in d.items()if k.startswith('dep_'))
 d['ms_per_frame']=d['cyc']/800000;d['base_ms_per_frame']=d['count']/800000
 d['branch_ms_per_frame']=(d['pen_mispredict']+d['pen_btb_taken'])/800000
 d['dcache_ms_per_frame']=d['pen_dcache']/800000;d['icache_ms_per_frame']=d['pen_icache']/800000;d['dependency_ms_per_frame']=d['pen_dep']/800000;d['f2i_extra_ms_per_frame']=d['pen_f2i']/800000
# Bound only the explicit sign test instructions: W loads, zero constant,
# three comparisons, XOR and sign-choice branch (not normal cull-mode branch).
sign=[]
for lo,hi in [(0x104d1d50,0x104d1d60),(0x104d1d70,0x104d1d7c),(0x104d1d80,0x104d1d82),(0x104d1d86,0x104d1d8a),(0x104d1e96,0x104d1ea6),(0x104d1eb6,0x104d1ec2),(0x104d1ec6,0x104d1ec8),(0x104d1ecc,0x104d1ed0)]:sign.extend(addr for addr in pc if lo<=addr<hi)
sign_tot=collections.Counter()
for addr in sign:sign_tot.update(pc[addr])
total=collections.Counter()
for r in pc.values():total.update(r)
old={r['function']:r for r in csv.DictReader((b/'profile-0360.tsv').open(),delimiter='\t')};new={r['function']:r for r in csv.DictReader((b/'profile.tsv').open(),delimiter='\t')};f='gfx_cpu_indexed_run';assert total['count']==int(new[f]['self_insns'])-int(old[f]['self_insns']);assert total['cyc']==int(new[f]['self_cycles'])-int(old[f]['self_cycles'])
write(O/'result.json',dict(passed=True,passive_cycle_and_actual_render_identical=True,window=dict(first=360,last=367,frames=8),total=dict(total),total_ms_per_frame=total['cyc']/800000,stages={k:dict(v)for k,v in stages.items()},instruction_groups={k:dict(v)for k,v in opgroups.items()},positive_w_sign_logic_upper_bound=dict(counters=dict(sign_tot),ms_per_frame=sign_tot['cyc']/800000,pcs=[f'{p:08x}'for p in sign],limits='Only an impossible zero-cost-removal bound; current winding runs before eligibility, so negativeW/clipped/fallback cases require exact sign handling. A new guard/reorder cannot assume allverticespositive.'),limits=['Stage grouping is an audited basic-block partition, not a compiler source-line attribution. Instructions shared between operations stay in their containing block.','Every instruction/base/penalty reconciles exactly to functionself. External callees including rendering/textureupload are excluded; call/return setup remains assigned to its caller block.','Dependencies are charged to their consuming instruction. Removing that instruction need not remove the full producer latency; upper bounds are not independent additive savings.','Unchanged ELF and exactcycle/output match prove passive instrumentation; modeledCPU/cache penalties are not nativeperformancecounters.']))
write(O/'ranges.json',[dict(begin=f'{lo:08x}',end_exclusive=f'{hi:08x}',stage=n)for lo,hi,n in ranges])
with(O/'categorized-pc.csv').open('x')as f:w=csv.DictWriter(f,fieldnames=list(categorized[0]));w.writeheader();w.writerows(categorized)
print('TOTAL',total['cyc']/800000)
for k,v in sorted(stages.items(),key=lambda kv:-kv[1]['cyc']):print(k,*(f'{v[n]:.6f}'for n in ['ms_per_frame','base_ms_per_frame','dependency_ms_per_frame','dcache_ms_per_frame','icache_ms_per_frame','branch_ms_per_frame','f2i_extra_ms_per_frame']))
print('SIGN_CEILING',sign_tot['cyc']/800000)
