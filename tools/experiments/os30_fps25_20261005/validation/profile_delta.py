#!/usr/bin/env python3
"""Explain a paired candidate's modeled CPU change by function and penalty."""
from common import *
from profile_breakdown import counters, functions
import argparse

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('label');p.add_argument('--baseline',default='baseline-normal');a=p.parse_args()
paths={'baseline':capture_path(a.baseline),'candidate':capture_path(a.label)}
profiles={};evidence={}
for label,path in paths.items():
 check_capture(path)
 start=counters(path/'profile-0360.txt');end=counters(path/'profile-0720.txt')
 count={k:end[k]-v for k,v in start.items()}
 fs,fe=functions(path/'profile-0360.tsv'),functions(path/'profile-0720.tsv')
 functions_delta={name:{k:int(row[k])-int(fs.get(name,{}).get(k,0)) for k in ['self_insns','self_cycles','icache_miss','dcache_miss','mispredict','calls','tailcalls']} for name,row in fe.items()}
 profiles[label]={'counters':count,'functions':functions_delta}
 for name in ['profile-0360.txt','profile-0720.txt','profile-0360.tsv','profile-0720.tsv']:
  evidence[str((path/name).relative_to(ROOT))]=sha(path/name)
base,cand=profiles['baseline'],profiles['candidate'];keys=set(base['functions'])|set(cand['functions']);rows=[]
for name in keys:
 b,c=base['functions'].get(name,{}),cand['functions'].get(name,{})
 rows.append({'function':name,'baseline_self_cycles':b.get('self_cycles',0),'candidate_self_cycles':c.get('self_cycles',0),'self_cycles_saved':b.get('self_cycles',0)-c.get('self_cycles',0),'self_instructions_saved':b.get('self_insns',0)-c.get('self_insns',0)})
penalties=['instructions','icache','nl_prefetch','dcache','store_buffer','uncached_io','cbo','misaligned_traps','mispredict','btb_taken','dependency_stalls','div_extra','fdiv_extra','fsqrt_extra','f2i_extra']
for d in profiles.values():assert sum(d['counters'][k] for k in penalties)==d['counters']['est_cycles']
totals={k:{'baseline':base['counters'][k],'candidate':cand['counters'][k],'saved':base['counters'][k]-cand['counters'][k]} for k in penalties}
result={'candidate':a.label,'baseline':a.baseline,'scope':'360 frames, cumulative profile0360 to0720; actual guest counts and unchanged modeled cycles, not hardware FPS or RTL stall measurements. Functions are self cost; changed inlining/cloning can transfer attribution between functions.','counter_delta':totals,'functions':sorted(rows,key=lambda r:-r['self_cycles_saved']),'evidence_sha256':evidence}
dest=OUT/('profile-delta-'+a.label+'.json');dest.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'largest_modeled_function_reductions':result['functions'][:10],'largest_modeled_function_increases':result['functions'][-5:],'counter_delta':totals},indent=2))
