#!/usr/bin/env python3
"""Fault-inject passive witness copies; never modify measured inputs."""
from pathlib import Path
import argparse,gc,hashlib,importlib.util,json,tempfile,csv
E=Path(__file__).resolve().parent;V=E.parent
parser=argparse.ArgumentParser();parser.add_argument('--candidate',type=Path,required=True);parser.add_argument('--baseline',type=Path,required=True);parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--strict-receipt',type=Path,required=True);parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
SOURCE=args.candidate.resolve();BASE=args.baseline.resolve();FIXTURE=args.fixture.resolve();HOOK=V/'independent_lifetime_v1.py'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert not args.report.exists();assert json.loads(args.strict_receipt.read_text())['status'].startswith('PASS_AJ03_')
spec=importlib.util.spec_from_file_location('lifetime',HOOK);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
cases=[('source_vertex_word','job-sources.tsv','word',lambda r:r['kind']=='0'),
 ('accepted_context_word','async-events.tsv','ctx_data',lambda r:r['ctx_take']=='1'),
 ('legacy_granted_ticket','async-events.tsv','granted_ticket',lambda r:r['lease_granted']=='1'),
 ('CPU_LOAD_identity','vtx-calls.tsv','a2',lambda r:True),
 ('grant_during_open_context','async-events.tsv','context_open',lambda r:r['lease_granted']=='1'),
 ('actual_matrix_write_data','async-events.tsv','matrix_data',lambda r:r['ctx_take']=='1' and int(r['ctx_index'])<16),
 ('snapshot_converted_halfword','async-events.tsv','packet_store_wdata',lambda r:r['packet_store_we']=='1'),
 ('raw_read_after_detachment','async-events.tsv','packet_raw_read_active',lambda r:r['snapshot_detached']=='1' and r['output_pending']=='1'),
 ('lease_before_snapshot','async-events.tsv','snapshot_detached',lambda r:r['lease_granted']=='1' and r['output_pending']=='1'),
 ('survivor_index_storage','async-events.tsv','survivor_check_indices',lambda r:r['survivor_accepted_result']=='1' and r['survivor_result_keep']=='1'),
 ('actual_post_edge_snapshot_store','snapshot-stores.tsv','word',lambda r:r['writer']=='1'),
 ('physical_group_descriptor','group-descriptors.tsv','word',lambda r:r['index']=='0'),
 ('accepted_group_descriptor_pair','async-events.tsv','descriptor_w0',lambda r:r['descriptor_pair_take']=='1'),
 ('physical_protected_context','context-stores.tsv','word',lambda r:r['index']=='0'),
 ('restored_group_context','async-events.tsv','context_apply_data',lambda r:r['restore_apply']=='1'),
 ('internal_member_identity','async-events.tsv','job_cpu_commands',lambda r:r['job_take']=='1' and r['group_active']=='1' and int(r['group_index'])>0)]
inputs=[HOOK,V/'independent_group_v1.py',args.strict_receipt,SOURCE.parent/'build.json',*(SOURCE/f for _,f,_,_ in cases)]
before={str(p):sha(p) for p in inputs};results=[]
for name,filename,field,predicate in cases:
 with tempfile.TemporaryDirectory(prefix='aj03-audit-',dir='/tmp') as temp:
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
result={'status':'PASS_SIXTEEN_CORRUPTIONS_REJECTED','cases':results,'inputs_sha256':before,'script_sha256':sha(Path(__file__)),'measured_inputs_unchanged':True}
args.report.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
