#!/usr/bin/env python3
"""Bound native interval comparison; no instruction-cost subtraction."""
import argparse,bisect,collections,csv,hashlib,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('candidate',type=Path);p.add_argument('control',type=Path);p.add_argument('symbols',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not a.output.exists()
symbols=[]
for line in a.symbols.read_text().splitlines():
 w=line.split()
 if len(w)==4 and w[2].lower()=='t':symbols.append((int(w[0],16),w[3]))
symbols.sort();keys=[x[0]for x in symbols]
def load(run):
 rows=[[int(v)for v in l.split()]for l in(run/'boundaries.tsv').read_text().splitlines()]
 assert len(rows)==3
 cycles=[rows[i+1][1]-rows[i][1]for i in range(2)]
 result=dict(cycles=cycles,total_cycles=sum(cycles),retired=rows[-1][2]-rows[0][2],uploaded_words=rows[-1][3]-rows[0][3])
 buckets=collections.defaultdict(lambda:[0,0])
 for row in csv.DictReader((run/'pc.csv').open()):
  data=list(row.values());i=bisect.bisect_right(keys,int(data[0]))-1
  name=symbols[i][1] if i>=0 else '<before_first_ELF_symbol>'
  buckets[name][0]+=int(data[1]);buckets[name][1]+=int(data[2])
 result['function_retire_and_preceding_gap_cycles']=buckets
 return result
c=load(a.candidate);b=load(a.control)
delta={k:[c['function_retire_and_preceding_gap_cycles'][k][i]-b['function_retire_and_preceding_gap_cycles'][k][i]for i in range(2)]for k in set(c['function_retire_and_preceding_gap_cycles'])|set(b['function_retire_and_preceding_gap_cycles'])}
overlap=collections.defaultdict(collections.Counter)
for row in csv.DictReader((a.candidate/'async-overlap.tsv').open(),delimiter='\t'):
 phase=int(row['phase']);mask=int(row['mask']);n=int(row['cycles'])
 for name,bit in [('retire',1),('gpu_busy',2),('raw_owner',4),('raw_transform',8),('raw_cull',16),('output_word',32),('cpu_repair_pending',64),('gpu_fragment',128),('output_full',256),('legacy_lease',512),('lease_pending',1024)]:
  if mask&bit:overlap[phase][name]+=n
 overlap[phase]['all_cycles']+=n
 if mask&256 and mask&128:overlap[phase]['output_full_and_fragment']+=n
 if mask&1024 and mask&128:overlap[phase]['lease_wait_and_fragment']+=n
 if mask&4 and not mask&512 and mask&1:overlap[phase]['async_owner_and_retire']+=n
result=dict(candidate=c,control=b,cycle_change_percent=(c['total_cycles']/b['total_cycles']-1)*100,prior_qualified_cycles=[3980965,4366717],change_vs_prior_qualified_percent=(c['total_cycles']/8347682-1)*100,function_delta=dict(sorted(delta.items(),key=lambda item:item[1][1],reverse=True)),overlap=overlap,limits=['Two bounded native CPU/raster/game/audio intervals only. No Pocket FPS or timing claim.','Function buckets attribute the gap since prior retirement, not exclusive function execution latency; use for diagnosis only.','All cycle totals are measured boundaries, never summed estimated savings.'])
paths=[Path(__file__),a.symbols]
for r in (a.candidate,a.control):paths.extend([r/'boundaries.tsv',r/'pc.csv',r/'process.json',r/'manifest.json',r.parent/'build.json'])
paths.append(a.candidate/'async-overlap.tsv');result['inputs_sha256']={str(p.resolve()):sha(p)for p in paths}
a.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k]for k in ['cycle_change_percent','change_vs_prior_qualified_percent','overlap']},indent=2))
