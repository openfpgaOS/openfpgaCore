from pathlib import Path
import importlib.util,json,bisect,struct,csv,hashlib
H=Path(__file__).resolve().parent;V=H.parent
spec=importlib.util.spec_from_file_location('life',V/'independent_lifetime_v3.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
F=V/'fixture-v4';B=V/'control-v4/run'
initial=list(struct.unpack('<80I',(F/'capture/run-reference/snapshot/geometry-registers.bin').read_bytes()));mapping={**{64+i:i for i in range(16)},**{5+i:16+i for i in range(8)}}
cs=m.rows(B/'vtx-calls.tsv');stamps=[c['cycle'] for c in cs];state=initial[64:80]+initial[5:13];ref={}
for cycle,kind,reg,value in m.mmio(B):
 if kind!='GEO':continue
 if reg in mapping:state[mapping[reg]]=value
 elif reg==2 and value&1:ref[bisect.bisect_right(stamps,cycle)-1]=state.copy()
def consumed(x):return x[:17]+(x[17:22] if x[23] else [])+x[22:24]
result={}
for name in ['candidate-v4','single-v4']:
 R=V/name/'run';calls=m.rows(R/'vtx-calls.tsv');desc={}
 if (R/'group-descriptors.tsv').exists():
  for r in m.rows(R/'group-descriptors.tsv'):desc.setdefault(r['firstseq'],[]).append(r['word'])
 boundaries=[int(x.split()[1]) for x in (R/'boundaries.tsv').read_text().splitlines()];jobs=[];context=None
 for up in m.uploads(R):
  if up['op']==0x63:context=up['data'][1:]
  elif up['op'] in [0x64,0x65]:
   data=up['data'];phase=bisect.bisect_right(boundaries,up['end'])
   fields=[data] if up['op']==0x64 else [[data[0]+i,*desc[data[0]][6*i:6*i+4],*data[3:6],*desc[data[0]][6*i+4:6*i+6],data[6]] for i in range(data[2])]
   for d in fields:
    seq,source,vtx,cmdsrc,config,flags,u,v,cpusrc,cpucmd,alpha=d;count=vtx>>12&255
    matches=[i for i,c in enumerate(cs) if c['phase']==phase and (c['a0'],c['a1'],c['a2'],c['command']+8)==(count,0,cpusrc,cpucmd)]
    matched=[i for i in matches if i in ref and consumed(ref[i])==consumed(context[:24])]
    jobs.append({'seq':seq,'phase':phase,'matches':matches,'exact_context_matches':matched,'contexts':[{'call':i,'dl_invocation':cs[i]['dl_invocation'],'continuation':cs[i]['dl_continuation']} for i in matched]})
   context=None
 result[name]={'jobs':len(jobs),'ambiguous_source':sum(len(j['matches'])>1 for j in jobs),'exact_context_nonunique':[j for j in jobs if len(j['exact_context_matches'])!=1],'repeated_source_jobs':[j for j in jobs if len(j['matches'])>1]}
result['inputs_sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),V/'independent_lifetime_v3.py',B/'vtx-calls.tsv',B/'mmio.tsv']}
(H/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:{kk:vv for kk,vv in v.items() if kk!='repeated_source_jobs'} for k,v in result.items() if k!='inputs_sha256'},indent=2))
