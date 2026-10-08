#!/usr/bin/env python3
"""Subtract retained profiles; no replay or timing-model parameter changes."""
from common import *
import csv,re

def counters(path):
 text=path.read_text().split('== functions')[0]
 return {m[1].strip():int(m[2]) for line in text.splitlines() if (m:=re.match(r'^\s{2,}(.+?)\s{2,}(\d+)',line))}
def functions(path):return {r['function']:r for r in csv.DictReader(path.open(),delimiter='\t')}
def main():
 result={'window':'profile-0360 to profile-0720, corresponding to frames360 through719,360 frames. Candidate comparisons separately include frame720 (361 frames).','scope':'Actual interpreted RV32 instructions and calibrated qsim estimates, not full-game CPU RTL stall counters. No GPU overlap/device FPS inference.','profiles':{}}
 for label,path in BASELINES.items():
  a,b=counters(path/'profile-0360.txt'),counters(path/'profile-0720.txt');d={k:b[k]-v for k,v in a.items()}
  fs,fe=functions(path/'profile-0360.tsv'),functions(path/'profile-0720.tsv');hot=[]
  for name,r in fe.items():
   h={'function':name}
   for key in ['self_insns','self_cycles','icache_miss','dcache_miss','mispredict','calls','tailcalls']:h[key]=int(r[key])-int(fs.get(name,{}).get(key,0))
   h['modeled_cycle_percent']=100*h['self_cycles']/d['est_cycles'];hot.append(h)
  penalties=['icache','nl_prefetch','dcache','store_buffer','uncached_io','cbo','misaligned_traps','mispredict','btb_taken','dependency_stalls','div_extra','fdiv_extra','fsqrt_extra','f2i_extra']
  assert sum(d[k] for k in penalties)+d['instructions']==d['est_cycles']
  result['profiles'][label]={'elf_sha256':sha(path/'sm64.elf'),'counters':d,'penalty_cycle_percent':{k:100*d[k]/d['est_cycles'] for k in penalties},'base_issue_percent':100*d['instructions']/d['est_cycles'],'conditional_branch_miss_percent':100*d['cond_mispredicts']/d['cond_branches'],'dependency_breakdown_percent':{k:100*v/d['est_cycles'] for k,v in d.items() if k.startswith('dep:')},'hottest_functions':sorted(hot,key=lambda x:-x['self_cycles'])[:40],'evidence_sha256':{str(f.relative_to(ROOT)):sha(f) for f in [path/'profile-0360.txt',path/'profile-0720.txt',path/'profile-0360.tsv',path/'profile-0720.tsv']}}
 (OUT/'baseline-profile-breakdown.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS: additive penalty totals reconcile exactly for both published apps')
if __name__=='__main__':main()
