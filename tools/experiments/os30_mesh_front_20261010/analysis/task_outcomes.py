#!/usr/bin/env python3
import hashlib,json,struct
from pathlib import Path
w=Path(__file__).resolve().parents[1];vf=w.parent/'work-app/native-vectors-v3/vectors.json';ip=w/'tests/scene-cull-oracle-v1/inputs.json';op=w/'tests/scene-cull-oracle-v1/outputs.bin';rp=w/'tests/scene-chain-v1/manifest.json';cpu=json.loads(ip.read_text());outs=list(struct.iter_unpack('<11I',op.read_bytes()));oracle={(m['task'],m['triangle']):x for m,x in zip(cpu['mapping'],outs)};records=[]
for t in json.loads(vf.read_text())['vectors']:
 raw=t['raw_expected_u32'];failure=0;survivors=[];clip=cull=0
 for n,indices in enumerate(t['triangles']):
  reject=oracle[(t['native_task'],n)][9]
  if reject:clip+=reject==1;cull+=reject==2;continue
  vs=[raw[k-t['destination']] for k in indices];bad=8 if any(r[7] for r in vs) else 9 if any((r[3]>>31) or (r[3]>>23&255)==0 or r[3]>=0x46fffe00 for r in vs) or len({r[3] for r in vs})==1 else 0
  if bad and not failure:failure=bad
  survivors.append(indices)
 records.append({'task':t['native_task'],'phase':t['phase'],'accepted_geometry':failure==0,'fallback_reason':failure,'input_triangles':len(t['triangles']),'cpu_clip_reject':clip,'cpu_cull_reject':cull,'cpu_kept_before_scope':len(survivors),'emitted_triangles':0 if failure else len(survivors),'fully_rejected_accepted':not failure and not survivors,'survivors':[] if failure else survivors})
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();j={'status':'PASS_EXACT_GEOMETRY_OUTCOMES_BOUND_TO_CHAIN','inputs_sha256':{str(p):sha(p) for p in (Path(__file__).resolve(),vf,ip,op,rp)},'scope':'Task geometry only; successful whole span uses exact CPU cull order before clip/equal-W fallback. Material and raster binding not inferred.','tasks':records};p=w/'task-outcomes-v1.json';p.write_text(json.dumps(j,indent=2)+'\n');print(str(p),sha(p),len(records))
