#!/usr/bin/env python3
"""Qualify observer noninterference, then count immutable duplicate positions."""
from common import *
import csv,collections
B=BASELINES['baseline-normal'];N=capture_path('vertex-reuse-observed-normal');check_capture(N)
assert sha(N/'sm64.elf')==sha(B/'sm64.elf')=='0350acc15976134120b3ba4cb48de4ee5f9077f62732143251840d68293ebc6f'
proof={}
for p in B.rglob('*'):
 if not p.is_file():continue
 rel=p.relative_to(B)
 if p.name in ['frames.csv','scene.csv','geometry.csv','culling.csv'] or p.suffix=='.tsv' or p.suffix=='.vec':
  assert (N/rel).read_bytes()==p.read_bytes(),str(rel)
  proof[str(rel)]=sha(p)
assert len(proof)>20
rows=[{k:int(v) for k,v in r.items()} for r in csv.DictReader((N/'vertex-reuse.csv').open())]
def summary(rs):
 vertices=sum(r['count'] for r in rs);eligible=[r for r in rs if r['eligible']];eligible_vertices=sum(r['count'] for r in eligible);duplicates=sum(r['duplicates'] for r in eligible)
 keys=collections.Counter((r['address'],r['count']) for r in eligible);useful=[r for r in eligible if r['duplicates']]
 return {'batches':len(rs),'vertices':vertices,'eligible_batches':len(eligible),'eligible_vertices':eligible_vertices,'eligible_vertex_percent':100*eligible_vertices/vertices if vertices else 0,'duplicate_positions':duplicates,'duplicate_percent_all_vertices':100*duplicates/vertices if vertices else 0,'duplicate_percent_eligible_vertices':100*duplicates/eligible_vertices if eligible_vertices else 0,'duplicates_with_attribute_seams':sum(r['attribute_seams'] for r in eligible),'useful_batches':len(useful),'unique_source_pointer_count_descriptors':len(keys),'unique_useful_descriptors':len({(r['address'],r['count']) for r in useful}),'batch_size_counts':dict(sorted(collections.Counter(r['count'] for r in eligible).items()))}
ps={r['function']:r for r in csv.DictReader((B/'profile-0360.tsv').open(),delimiter='\t')};pe={r['function']:r for r in csv.DictReader((B/'profile-0720.tsv').open(),delimiter='\t')}
expected=int(pe['gfx_sp_vertex']['calls'])-int(ps['gfx_sp_vertex']['calls']);actual=sum(360<=r['frame']<720 for r in rows);assert actual==expected,(actual,expected)
sel=[r for r in rows if 360<=r['frame']<=720]
descriptors={}
for r in rows:
 if not r['eligible']:continue
 key=(r['address'],r['count']);d=descriptors.setdefault(key,{'address':r['address'],'count':r['count'],'first_frame':r['frame'],'duplicates':r['duplicates'],'attribute_seams':r['attribute_seams'],'calls_all':0,'calls_gameplay':0})
 assert d['duplicates']==r['duplicates'] and d['attribute_seams']==r['attribute_seams'],'Eligible static source changed in captured route'
 d['calls_all']+=1;d['calls_gameplay']+=360<=r['frame']<=720
res={'passed_noninterference':True,'elf_sha256':sha(N/'sm64.elf'),'observer_manifest_sha256':sha(OUT/'qsim-vertex-reuse-observed-manifest.json'),'observation_sha256':sha(N/'vertex-reuse.csv'),'script_sha256':sha(Path(__file__)),'unchanged_guest_artifacts':proof,'profile_count_crosscheck':{'window':'360..719','profile_function_calls':expected,'observed_calls':actual},'symbols':read(N/'vertex-reuse-symbols.json'),'all_frames':summary(rows),'gameplay':{'first_frame':360,'last_frame':720,**summary(sel)},'windows':[{'first_frame':f,'last_frame':min(720,f+59),**summary([r for r in sel if f<=r['frame']<min(721,f+60)])} for f in range(360,721,60)],'descriptors':sorted(descriptors.values(),key=lambda r:-(r['calls_gameplay']*r['duplicates'])),'scope':'Host observer reads exact12 coordinate bytes in24-byte GBI_FLOATS vertices at actual function entry. Every selected profile/trace/vector matches original. Duplicate positions are potential transform reuse, not a speedup claim. Eligible region follows _start..__EH_FRAME_BEGIN__ and1..32 vertices; source immutability/linker contract is independently required before caching descriptors. Attribute seam constancy does not prove the absence of source writes.'}
(OUT/'vertex-reuse-observer-results.json').write_text(json.dumps(res,indent=2)+'\n');print(json.dumps({k:res[k] for k in ['passed_noninterference','profile_count_crosscheck','symbols','gameplay']},indent=2))
