#!/usr/bin/env python3
"""Bind retained architectural results and measured cycles for decoded elasticity."""
from pathlib import Path
import re,json,hashlib,shutil,subprocess,sys
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];ART=ROOT/'docs/measurements/os30-dual-20261004';VAL=ART/'validation'
LABEL='rvc_skid_immediate_second_decode';REFERENCE='rvc_skid_immediate_pingpong_pointer_packer'
P=VAL/LABEL;B=VAL/REFERENCE;CPU=ART/'decode-second-skid-draft-v1/immediate'
SHA='f703a709e272a70084ee567ee59719da1e4ff351462fd64f4355ae9e61e47fc5'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x):
 assert not p.exists(),p;p.write_text(json.dumps(x,indent=2)+'\n')
def delta(old,new):return dict(reference_cycles=old,candidate_cycles=new,cycle_change_pct=(new/old-1)*100,throughput_change_pct=(old/new-1)*100)
def main():
 assert sha(CPU/'VexiiRiscv.v')==SHA==sha(ART/'decode-second-skid-replay-v1/immediate/VexiiRiscv.v')
 dm=json.loads((P/'driver-manifest.json').read_text());assert dm['driver_sha256']==sha(HERE/'second_decode_runtime.py') and dm['added_monitor_sha256']==sha(HERE/'second_decode_monitor.py')
 records=[];allresults=[]
 files=['results.json','frontend-results.json','icache-results.json','decode-traps-results.json','fp-legality-results.json']
 for name in files:
  before=json.loads((B/name).read_text());after=json.loads((P/name).read_text());assert len(before['results'])==len(after['results'])
  if 'binary_sha256' in before:assert before['binary_sha256']==after['binary_sha256']
  for old,new in zip(before['results'],after['results']):
   assert old['mode']==new['mode'];program=new.get('program',{'frontend-results.json':'frontend','icache-results.json':'icache','decode-traps-results.json':'decode-traps','fp-legality-results.json':'fp-legality'}.get(name))
   row=dict(program=program,mode=new['mode'],architectural_result_equal=True)
   if 'program_sha256' in old:assert old['program_sha256']==new['program_sha256']
   for key in ['hash','traps','cases']:
    if key in old:assert old[key]==new[key];row[key]=new[key]
   if 'cycles_to_pass' in old:row.update(delta(old['cycles_to_pass'],new['cycles_to_pass']))
   elif 'cycles' in old:row.update(delta(old['cycles'],new['cycles']))
   if 'interrupts' in old:row['interrupts']=dict(reference=old['interrupts'],candidate=new['interrupts'])
   for key in ['kernels','rows']:
    if key in old:
     assert len(old[key])==len(new[key]);row[key]=[]
     for i,(x,y) in enumerate(zip(old[key],new[key])):
      for attr in ['hash','name','region_bytes']:
       if attr in x:assert x[attr]==y[attr]
      row[key].append(dict(index=i,**{k:y[k] for k in ['hash','name','region_bytes'] if k in y},**delta(x['cycles'],y['cycles'])))
   if program=='stress':assert 'CPU stress PASS: integer, FPU, atomics, traps, cache.' in new['uart']
   allresults.append(row)
   log=P/f'{program}-{new["mode"]}.log';assert sha(log)==new['log_sha256'];records.append(dict(program=program,mode=new['mode'],log_sha256=sha(log)))
 assert len(records)==14
 decoded=[];pingpong=[];dtot={};ptot={}
 for r in records:
  text=(P/f'{r["program"]}-{r["mode"]}.log').read_text()
  for prefix,dest,total in [('DECODED SKID COVERAGE',decoded,dtot),('PINGPONG COVERAGE',pingpong,ptot)]:
   m=re.findall(r'^'+prefix+r' (.+)$',text,re.M);assert len(m)==1
   counts={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',m[0])};assert counts['errors']==0
   for k,v in counts.items():total[k]=total.get(k,0)+v
   dest.append(dict(program=r['program'],mode=r['mode'],counters=counts))
 for key in ['captures','tail_drain','blocked','full_flush','blocked_flush','flush_waiting','partial_capture','partial_issue_hold','tail_trap','younger_flush_held','cancel_accept','rotations']:assert dtot[key]>0,key
 write(P/'decoded-skid-coverage.json',dict(passed=True,candidate_sha256=SHA,totals=dtot,runs=decoded))
 write(P/'pingpong-coverage.json',dict(passed=True,candidate_cpu_sha256=SHA,totals=ptot,runs=pingpong))
 with (P/'skid-coverage.driver.log').open('w') as f:subprocess.run([sys.executable,str(HERE/'skid_coverage.py'),'--label',LABEL],cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(P/'performance-comparison.json',dict(reference=REFERENCE,candidate=LABEL,source_sha256=SHA,all_fourteen_architectural_results_equal=True,cycles_may_change=True,scope='Retained deterministic kernels and stress fixtures; not game FPS.',results=allresults))
 proofs={n:sha(ART/n/'manifest.json') for n in ['decode-second-skid-emitted-audit-v1','decode-second-skid-actual-proof-v1','second-decode-monitor-review-v1']}
 q=dict(passed=True,scope='Whole-CPU retained fourteen fixtures; physical capacity/timing separate.',candidate_sha256=SHA,retained_program_modes=14,all_architectural_results_equal_to=REFERENCE,byte_identical_cycles_not_expected=True,
  exact_source_replay_sha256=sha(ART/'decode-second-skid-replay-v1/immediate/VexiiRiscv.v'),source_recipe_sha256=sha(CPU/'recipe.json'),source_proof_sha256=sha(CPU/'local-refinement.json'),independent_and_actual_source_evidence=proofs,
  build_manifest_sha256=sha(P/'build-manifest.json'),driver_manifest_sha256=sha(P/'driver-manifest.json'),monitor_map_sha256=sha(P/'skid-observations.json'),added_monitor_sha256=sha(HERE/'second_decode_monitor.py'),
  decoded_skid_coverage_sha256=sha(P/'decoded-skid-coverage.json'),first_skid_coverage_sha256=sha(P/'skid-coverage.json'),first_pingpong_coverage_sha256=sha(P/'pingpong-coverage.json'),performance_comparison_sha256=sha(P/'performance-comparison.json'),retained_logs=records,
  physical_status='First baseline-GPU full fit failed by one LAB; no final fitted timing qualification.')
 write(P/'runtime-qualification.json',q);write(CPU/'runtime-qualification.json',q)
 for n in ['second_decode_runtime.py','second_decode_collect.py','second_decode_monitor.py']:shutil.copy2(HERE/n,P/n)
 print(json.dumps(dict(passed=True,architectural_cases=14,decoded_coverage=dtot,performance=[r for r in allresults if r['program'] in ['frontend','kernels']]),indent=2))
if __name__=='__main__':main()
