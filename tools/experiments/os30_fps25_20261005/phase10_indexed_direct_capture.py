#!/usr/bin/env python3
"""Native-FMA unchanged-renderer work/cost screen for one-pass direct indexed preparation."""
import argparse,csv,json,os,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write,verify_capture,canonical
H=Path(__file__).resolve().parent;P=CAMP/'phase10';OUT=P/'indexed-direct-v2';BASE=CAMP/'phase9/checks-state-f11';SIM=P/'load-cram-functional-v1/functional-qsim/qsim'
def capture(label):
 os.environ['LOD5_MODEL_MODE']='absent'
 for k in tuple(os.environ):
  if k.startswith('LOD5_')and k!='LOD5_MODEL_MODE':os.environ.pop(k)
 m=module('cursor_native_capture',ROOT/'tools/experiments/sm64_batch_cull_20261005/validation/capture.py');m.OUT=OUT
 sys.argv=[str(Path(__file__)),label,'--elf',str((BASE if label=='baseline'else OUT)/'app.elf'),'--exe',str(SIM),'--geometry','0','--audio'];m.main()
def analyze():
 a=P/'batch-cursor-v1/capture-baseline';b=OUT/'capture-candidate';x=verify_capture(a,sha(BASE/'app.elf'));y=verify_capture(b,sha(OUT/'app.elf'))
 for d in(a,b):assert'FATAL'not in(d/'run.log').read_text()
 for n in('scene.csv','geometry.csv','culling.csv'):assert(a/n).read_bytes()==(b/n).read_bytes(),n
 assert len(x)==len(y)==721 and all(l['gpu_cmd_words']==r['gpu_cmd_words']for l,r in zip(x,y))
 val=module('cursor_validate',H/'phase7_validate.py');changes=[]
 for f in sorted((a/'gpuvec').glob('*.vec')):
  target=b/'gpuvec'/f.name;old,new=(val.v.vector(p.read_bytes())for p in(f,target));ow,ob=canonical(old);nw,nb=canonical(new)
  commands=lambda e:[p for p in e if p[:4]==b'CMD1'];mmio=lambda e:[p for p in e if p[:4]==b'MMIO']
  assert commands(ow)==commands(nw),(f.name,'commandorder');assert mmio(ow)==mmio(nw),(f.name,'MMIOorder')
  assert all(old[k]==new[k]for k in('depth','fence','uploads'))
  diffs=[dict(offset=i,before=old['texture'][i:i+4].hex(),after=new['texture'][i:i+4].hex())for i in range(0,len(old['texture']),4)if old['texture'][i:i+4]!=new['texture'][i:i+4]]
  changes.append(dict(frame=int(f.stem.split('_')[1]),texture_changes=diffs,combined_interleaving_exact=ow==nw,publication_boundaries_exact=ob==nb,baseline_batches=ob,candidate_batches=nb,baseline_sha256=sha(f),candidate_sha256=sha(target)))
 totals={}
 for name,first,last in [('gameplay',360,721),('busy8',360,368),('warm60',360,420)]:
  fields={k:{'baseline':sum(int(r[k])for r in x[first:last]),'candidate':sum(int(r[k])for r in y[first:last])}for k in('est_cycles','insns','icache_miss_sdram','dcache_miss','mispredict')}
  totals[name]=dict(frames=last-first,fields=fields,baseline_ms=fields['est_cycles']['baseline']/((last-first)*100000),candidate_ms=fields['est_cycles']['candidate']/((last-first)*100000),time_change_percent=100*(fields['est_cycles']['candidate']/fields['est_cycles']['baseline']-1))
 result=dict(passed=True,all721_scene_geometry_culling_counts_exact=True,all8_commands_and_MMIO_individually_exact=True,combined_interleaving_changed_frames=[r['frame']for r in changes if not r['combined_interleaving_exact']],publication_changed_frames=[r['frame']for r in changes if not r['publication_boundaries_exact']],padding_check=val.padding(changes,a,b,OUT/'SM64'),vectors=changes,cpu_only_cost=totals,scope='Functional indexedopcodes notrendered; actualcoupled output gate stillrequired, especiallywherepublicationchangescommand/MMIOinterleaving.',qualified_for_deployment=False)
 write(OUT/'functional-result.json',result);print(json.dumps(totals,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['capture','analyze']);p.add_argument('--label',choices=['baseline','candidate'],default='candidate');a=p.parse_args();capture(a.label)if a.action=='capture'else analyze()
