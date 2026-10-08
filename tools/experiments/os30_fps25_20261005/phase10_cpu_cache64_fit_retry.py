#!/usr/bin/env python3
"""Exactly one ordinary seed-2 cache-only resource/timing fit; root invokes."""
import argparse,hashlib,json,pathlib,re,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'cpu-cache64-resource-v1';O=P/'cache64-v7-fit-v1-retry1';F=O/'fit-seed2';SRC=C/'map-core/job';QSF=pathlib.Path('src/fpga/targets/pocket/ap_core.qsf')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def inv(d):return {str(p.relative_to(d)):sha(p) for p in sorted(d.rglob('*')) if p.is_file()}
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def component():
 d=json.loads((C/'manifest.json').read_text())
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 assert json.loads((C/'resource-result.json').read_text())['passed_map']
 return d
def prepare():
 component();O.mkdir(exist_ok=False);before=inv(SRC);shutil.copytree(SRC,O/'job');assert inv(O/'job')==before
 q=(O/'job'/QSF).read_text();assert re.findall(r'^set_global_assignment -name SEED (\d+)$',q,re.M)==['2']
 locations=[l for l in q.splitlines() if l.startswith('set_location_assignment ')];assert len(locations)==224 and all('PIN_' in l for l in locations)
 assert not list((O/'job').rglob('*.fit.rpt')) and not list((O/'job').rglob('*.sta.rpt')) and not (O/'job'/QSF.parent/'timing').exists()
 source={n:h for n,h in before.items() if pathlib.Path(n).suffix.lower() in {'.v','.sv','.vh','.vhd','.qsf','.qpf','.qip','.sdc','.mif','.hex'} and not set(pathlib.Path(n).parts)&{'db','incremental_db','output_files','simulation'}}
 put(O/'prepared.json',dict(component_manifest_sha256=sha(C/'manifest.json'),mapped_job_sha256=before,source_input_sha256=source,recipe_sha256=sha(pathlib.Path(__file__)),qsf_sha256=sha(O/'job'/QSF),seed=2,configured_mhz=100,fit_count=1,seed_sweep=False,only_rtl_change='CPU cache sets I256/D1024 to I512/D512',internal_location_constraints=0,original_pins=224,production_modified=False,speed_improvement_claimed=False,limits=['The model has a0.605% warm /0.293% cold CPU-time regression; resource/timing feasibility only.','No actual M10K, fitted ALM or timing result exists before this one fit.']))
 shutil.copy2(__file__,O/'recipe.py');print('PREPARED',sha(O/'prepared.json'),flush=True)
def checked(review=True):
 component();d=json.loads((O/'prepared.json').read_text());assert d['component_manifest_sha256']==sha(C/'manifest.json');assert d['recipe_sha256']==sha(pathlib.Path(__file__))
 assert inv(SRC)==d['mapped_job_sha256'];assert inv(O/'job')==d['mapped_job_sha256'];assert sha(O/'job'/QSF)==d['qsf_sha256']
 if review:
  g=json.loads((O/'independent-review.json').read_text());assert g['passed_for_one_fit'];assert g['prepared_sha256']==sha(O/'prepared.json')
 return d
def run():
 d=checked();F.mkdir(exist_ok=False);shutil.copytree(O/'job',F/'job');assert inv(F/'job')==d['mapped_job_sha256'];wd=F/'job'/QSF.parent
 sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 phases=[('fit',['quartus_fit','ap_core','--tdc=on','--read_settings_files=on','--write_settings_files=off']),('asm',['quartus_asm','ap_core']),('sta',['quartus_sta','ap_core']),('corners',['quartus_sta','-t',str(F/'job/tools/report_core_timing.tcl'),'ap_core','timing'])]
 put(F/'prepared.json',dict(prepared_sha256=sha(O/'prepared.json'),review_sha256=sha(O/'independent-review.json'),phases=phases,fit_count=1,seed=2));shutil.copy2(__file__,F/'recipe.py')
 result=dict(directory=str(wd),seed=2,variant='os30',returncodes={},state='running',valid=False);start=time.monotonic()
 for label,args in phases:
  cmd=sweep.docker(F,'pocket',wd,args);put(F/(label+'-invocation.json'),dict(command=cmd));print('START',label,flush=True)
  with (F/(label+'.log')).open('x') as f:rc=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT).returncode
  result['returncodes'][label]=rc;print('DONE',label,rc,flush=True)
  if rc and label!='corners':result['state']='failed';break
 else:
  result.update(sweep.parse_result(wd,'ap_core'));result.update(state='complete',valid=True)
  rows=result['timing'];assert len(rows)==20
  assert set((r['model'],r['temperature'],r['check']) for r in rows)==set((m,t,c) for m in ('slow','fast') for t in ('0','85') for c in ('setup','hold','recovery','removal','pulse_width'))
  assert all(re.fullmatch(r'-?\d+(?:\.\d+)?',r['slack']) for r in rows)
  log=(F/'corners.log').read_text();errors=[l for l in log.splitlines() if re.search(r'^Error|^Fatal',l)]
  if result['returncodes']['corners']:
   assert result['returncodes']['corners']==3 and '\nWhole-design timing failed; see timing\n' in log
   assert errors==[f'Error (23031): Evaluation of Tcl script {F}/job/tools/report_core_timing.tcl unsuccessful','Error: Quartus Prime Timing Analyzer was unsuccessful. 1 error, 1 warning'],errors
  else:assert not errors,errors
  assert len(re.findall(r'^RESULT ',log,re.M))==20
 result['seconds']=time.monotonic()-start
 for n,h in d['source_input_sha256'].items():assert sha(F/'job'/n)==h,n
 checked();put(F/'complete.json',result);print(json.dumps(result,indent=2),flush=True)
def seal():
 d=checked();result=json.loads((F/'complete.json').read_text());wd=pathlib.Path(result['directory']);freq={};resources={};report=wd/'output_files/ap_core.fit.rpt';s=report.read_text() if report.exists() else ''
 for k,p in {'needed_alms':r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)','placed_alms':r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)','labs':r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)','m10ks':r';\s*M10K blocks\s*;\s*([\d,]+)','dsps':r';\s*Total DSP Blocks\s*;\s*([\d,]+)'}.items():
  match=re.search(p,s);resources[k]=int(match.group(1).replace(',','')) if match else None
 if result['returncodes'].get('sta')==0:
  s=(wd/'output_files/ap_core.sta.rpt').read_text()
  for t in (0,85):
   section=s.split(f'; Slow 1100mV {t}C Model Fmax Summary',1)[1].split('Model Setup Summary',1)[0];match=re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]',section);assert match;freq[str(t)]=float(match.group(1))
 complete=result['state']=='complete';worst=result.get('worst',{});passed=complete and min(freq.values())>90 and all(worst.get(k,-1)>0 for k in ('hold','recovery','removal','pulse_width'))
 output=dict(comparative_physical_gate_pass=passed,configured_mhz=100,all_configured_timing_checks_pass=result.get('all_checks_pass',False),slow_main_clock_fmax_mhz=freq,resources=resources,worst_timing=worst,phase_returncodes=result['returncodes'],seconds=result['seconds'],seed=2,full_fit_count=1,seed_sweep=False,production_modified=False,deployment_ready=False,model_cpu_time_cost_percent=dict(warm=0.6049778987613497,cold=0.29280837110863267))
 put(O/'result.json',output)
 files=[pathlib.Path(__file__),O/'prepared.json',O/'independent-review.json',O/'result.json',F/'complete.json',F/'prepared.json',C/'manifest.json',wd/'ap_core.qsf']
 for directory in [F,wd/'output_files',wd/'timing']:
  if directory.exists():files += [p for p in directory.iterdir() if p.is_file() and p.suffix in {'.json','.log','.rpt','.tsv','.rbf','.sof'}]
 put(O/'manifest.json',dict(deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}));print(json.dumps(output,indent=2));print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run','seal']);globals()[a.parse_args().action]()
