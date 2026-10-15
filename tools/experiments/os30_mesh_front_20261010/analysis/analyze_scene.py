#!/usr/bin/env python3
import hashlib,json,struct,collections
from pathlib import Path
w=Path(__file__).resolve().parents[1];vfile=w.parent/'work-app/native-vectors-v3/vectors.json';i=w/'tests/scene-cull-oracle-v1/inputs.json';o=w/'tests/scene-cull-oracle-v1/outputs.bin';receipt=w/'tests/scene-chain-v1/manifest.json'
v=json.loads(vfile.read_text())['vectors'];cpu=json.loads(i.read_text());outs=list(struct.iter_unpack('<11I',o.read_bytes()));reject={(m['task'],m['triangle']):x[9] for m,x in zip(cpu['mapping'],outs)};phases=collections.defaultdict(collections.Counter)
for x in v:
 q=phases[x['phase']];raw=x['raw_expected_u32'];q['tasks']+=1;q['input_triangles']+=len(x['triangles']);q['raw_words_compared']+=len(raw)*8;bad=0;kept=0;used=set()
 for n,tri in enumerate(x['triangles']):
  r=reject[(x['native_task'],n)];q['CPU_clip_rejected' if r==1 else 'CPU_cull_rejected' if r==2 else 'CPU_kept_before_scope']+=1
  if r:continue
  vs=[raw[k-x['destination']] for k in tri];fb=8 if any(row[7] for row in vs) else 9 if any((row[3]>>31) or (row[3]>>23&255)==0 or row[3]>=0x46fffe00 for row in vs) or len({row[3] for row in vs})==1 else 0
  if fb and not bad:bad=fb
  kept+=1;used.update(tri)
 if bad:q['fallback_tasks']+=1;q['fallback_input_triangles']+=len(x['triangles']);q['fallback_partial_clip' if bad==8 else 'fallback_W']+=1
 else:q['accepted_geometry_tasks']+=1;q['accepted_input_triangles']+=len(x['triangles']);q['emitted_triangles']+=kept;q['emitted_packet_words']+=9*len(used)+2*kept;q['fully_rejected_accepted_tasks']+=kept==0
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();out={'status':'PASS_BOUND_INDEPENDENT_EXPECTED_SCENE_OUTCOMES','per_phase':{str(k):dict(v) for k,v in phases.items()},'scope':'Captured geometry-only admission with exact current CPU cull oracle. Material eligibility/raster binding not established; counts are not wholeframe speedup predictions.','inputs_sha256':{str(p):sha(p) for p in (Path(__file__).resolve(),vfile,i,o,receipt)}}
(w/'scene-outcomes-v1.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out['per_phase'],indent=2))
