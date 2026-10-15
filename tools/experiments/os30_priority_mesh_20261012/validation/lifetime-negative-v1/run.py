#!/usr/bin/env python3
"""Fault-inject passive witness copies; never modify measured inputs."""
from pathlib import Path
import gc,hashlib,importlib.util,json,tempfile,csv
E=Path(__file__).resolve().parent;V=E.parent
SOURCE=V/'candidate-v1/run';BASE=V/'control-v1/run';FIXTURE=V/'fixture-v1';HOOK=V/'async_lifetime_v1.py'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not(E/'result.json').exists()
spec=importlib.util.spec_from_file_location('lifetime',HOOK);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cases=[('source_vertex_word','job-sources.tsv','word',lambda r:r['kind']=='0'),
 ('accepted_context_word','async-events.tsv','ctx_data',lambda r:r['ctx_take']=='1'),
 ('legacy_granted_ticket','async-events.tsv','granted_ticket',lambda r:r['lease_granted']=='1'),
 ('CPU_LOAD_identity','vtx-calls.tsv','a2',lambda r:True),
 ('grant_during_open_context','async-events.tsv','context_open',lambda r:r['lease_granted']=='1'),
 ('actual_matrix_write_data','async-events.tsv','matrix_data',lambda r:r['ctx_take']=='1' and int(r['ctx_index'])<16)]
inputs=[HOOK,SOURCE.parent/'check-independent-v1.json',SOURCE.parent/'build.json',*(SOURCE/f for _,f,_,_ in cases)]
before={str(p):sha(p) for p in inputs};results=[]
for name,filename,field,predicate in cases:
 with tempfile.TemporaryDirectory(prefix='aj02-audit-',dir='/tmp') as temp:
  root=Path(temp);run=root/'run';run.mkdir();(root/'build.json').symlink_to(SOURCE.parent/'build.json')
  for p in SOURCE.iterdir():
   if p.name!=filename:(run/p.name).symlink_to(p)
  with(SOURCE/filename).open() as f:
   reader=csv.DictReader(f,delimiter='\t');fields=reader.fieldnames;rows=list(reader)
  mutated=0
  for r in rows:
   if predicate(r):r[field]=str(int(r[field],0)^1);mutated+=1;break
  assert mutated==1
  with(run/filename).open('w') as f:
   writer=csv.DictWriter(f,fieldnames=fields,delimiter='\t',lineterminator='\n');writer.writeheader();writer.writerows(rows)
  del rows;gc.collect()
  try:m.check(run,BASE,FIXTURE,[],[])
  except AssertionError as error:results.append({'case':name,'status':'REJECTED','message':str(error)})
  else:raise AssertionError('Corrupted witness accepted: '+name)
 gc.collect()
assert before=={str(p):sha(p) for p in inputs}
result={'status':'PASS_SIX_CORRUPTIONS_REJECTED','cases':results,'inputs_sha256':before,'script_sha256':sha(Path(__file__)),'measured_inputs_unchanged':True}
(E/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
