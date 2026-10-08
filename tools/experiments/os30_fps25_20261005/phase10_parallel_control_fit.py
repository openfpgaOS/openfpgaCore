#!/usr/bin/env python3
"""One prospective ordinary seed-2 parallel-control CPU/GPU/cache fit; unchanged clocks and locations."""
import argparse,hashlib,json,pathlib,re,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
C=P/'parallel-control-map-v1';O=P/'parallel-control-fit-v1';F=O/'fit-seed2';SRC=C/'map-core/job';QSF=pathlib.Path('src/fpga/targets/pocket/ap_core.qsf')
QUALIFICATION={
 P/'normal-build-parallel-control-v1/manifest.json':'690c9ec32e7c53c2de1bd0784df21271c72d672a9222c11ed56070d8feffa5b8',
 P/'native-parallel-control-check-v1/manifest.json':'26f66d654c8c948be86816ba1c00b6f118c142baf398db1b60ccf15de2c71688',
 P/'cpu-parallel-control-source-review-v1/manifest.json':'db4312cf2d7fe8aeee2a7950f43c9c194de288ae329fa85dabf7ef96354ce776',
 P/'gpu-parallel-control-v1/manifest.json':'88542a32028d1d923e4ac0e83c750c99d6d6710724480380922e097926684252',
 P/'gpu-light-prefix-source-review-v1/manifest.json':'3474c14b4b335d2685ca1a296f5dbd2c9cd7fa68ccd9c5e499308fc60665bd29',
 P/'render-cache-speculative-read-source-review-v1/manifest.json':'245790186d3f48318578097c0a6a6ba8171a44bd52bf8aab936e3d5d0e9e12c0',
}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def inv(d):return {str(p.relative_to(d)):sha(p) for p in sorted(d.rglob('*')) if p.is_file()}
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def component():
 assert all(len(v)==64 for v in QUALIFICATION.values()),"Final qualification pins pending; no fit preparation/execution"
 d=json.loads((C/'manifest.json').read_text())
 for n,h in d['evidence_sha256'].items():assert sha(R/n)==h,n
 assert json.loads((C/'result.json').read_text())['passed_map']
 for p,h in QUALIFICATION.items():
  assert sha(p)==h,p
  q=json.loads(p.read_text())
  if p==P/'normal-build-parallel-control-v1/manifest.json':
   assert q and all(isinstance(v,str) and re.fullmatch(r'[0-9a-f]{64}',v) for v in q.values()),p
   for n,v in q.items():assert sha(p.parent/n)==v,(p,n)
  for n,v in q.get('files',{}).items():assert sha(p.parent/n)==v,(p,n)
  for n,v in q.get('evidence_sha256',{}).items():assert sha(R/n)==v,(p,n)
 return d
def prepare():
 component();O.mkdir(exist_ok=False);before=inv(SRC);shutil.copytree(SRC,O/'job');assert inv(O/'job')==before
 q=(O/'job'/QSF).read_text();assert re.findall(r'^set_global_assignment -name SEED (\d+)$',q,re.M)==['2']
 locations=[l for l in q.splitlines() if l.startswith('set_location_assignment ')];assert len(locations)==224 and all('PIN_' in l for l in locations)
 assert not list((O/'job').rglob('*.fit.rpt')) and not list((O/'job').rglob('*.sta.rpt')) and not (O/'job'/QSF.parent/'timing').exists()
 source={n:h for n,h in before.items() if pathlib.Path(n).suffix.lower() in {'.v','.sv','.vh','.vhd','.qsf','.qpf','.qip','.sdc','.mif','.hex'} and not set(pathlib.Path(n).parts)&{'db','incremental_db','output_files','simulation'}}
 put(O/'prepared.json',dict(component_manifest_sha256=sha(C/'manifest.json'),qualification_sha256={str(p.relative_to(R)):h for p,h in QUALIFICATION.items()},mapped_job_sha256=before,source_input_sha256=source,recipe_sha256=sha(pathlib.Path(__file__)),qsf_sha256=sha(O/'job'/QSF),seed=2,configured_mhz=100,fit_count=1,seed_sweep=False,selected_components='I64/D64 single-issue RVC pipeline CPU3bbe with exact uniform FP, symmetric ready and GShare factors; composed GPU queue finality and light prefix; GPU32 cachea88 speculative owned read; unchanged texturec3fd and audiof9cb',internal_location_constraints=0,original_pins=224,production_modified=False,speed_measurement_scope='Two bounded native continuous intervals total7,869,037 cycles versus original I32/D1288,162,221. The added CPU/GPU/cache factors preserve all17 output artifacts and matched-capacity cycles; no sustained Pocket FPS claim',limits=['The RVC pipeline adds frontend latency; GPU32 capacity recovers measured runtime. Native replay uses bounded guest services and synthetic traffic and does not qualify the actual OS/IRQ/PCM/display path.','No actual M10K, fitted ALM or timing result exists before this one fit.']))
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
 d=checked();result=json.loads((F/'complete.json').read_text());wd=pathlib.Path(result['directory']);freq={};resources={};report=wd/'output_files/ap_core.fit.rpt';s=report.read_text(errors='replace') if report.exists() else ''
 for k,p in {'needed_alms':r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)','placed_alms':r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)','labs':r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)','m10ks':r';\s*M10K blocks\s*;\s*([\d,]+)','dsps':r';\s*Total DSP Blocks\s*;\s*([\d,]+)'}.items():
  match=re.search(p,s);resources[k]=int(match.group(1).replace(',','')) if match else None
 if result['returncodes'].get('sta')==0:
  s=(wd/'output_files/ap_core.sta.rpt').read_text(errors='replace')
  for t in (0,85):
   section=s.split(f'; Slow 1100mV {t}C Model Fmax Summary',1)[1].split('Model Setup Summary',1)[0];match=re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]',section);assert match;freq[str(t)]=float(match.group(1))
 complete=result['state']=='complete';worst=result.get('worst',{});resources_complete=all(isinstance(v,int) and v>0 for v in resources.values());m10k_budget_pass=resources_complete and resources['m10ks']<=308;resource_budgets={'needed_alms':18480,'placed_alms':18480,'labs':1848,'m10ks':308,'dsps':66};resources_budget_pass=resources_complete and all(resources[k]<=limit for k,limit in resource_budgets.items());slow_keys_complete=set(freq)=={'0','85'};passed=complete and slow_keys_complete and min(freq.values())>90 and resources_budget_pass and all(worst.get(k,-1)>0 for k in ('hold','recovery','removal','pulse_width'))
 output=dict(comparative_physical_gate_pass=passed,physical_resource_totals_complete=resources_complete,resources_budget_pass=resources_budget_pass,resource_budgets=resource_budgets,m10k_budget_pass=m10k_budget_pass,m10k_budget=308,both_slow_corners_present=slow_keys_complete,configured_mhz=100,all_configured_timing_checks_pass=result.get('all_checks_pass',False),slow_main_clock_fmax_mhz=freq,resources=resources,worst_timing=worst,phase_returncodes=result['returncodes'],seconds=result['seconds'],seed=2,full_fit_count=1,seed_sweep=False,production_modified=False,deployment_ready=False,performance_scope='Refer to native-parallel-control-check-v1 and predecessor interval/component proofs. Physical timing alone is not a performance measurement')
 put(O/'result.json',output)
 files=[pathlib.Path(__file__),O/'prepared.json',O/'independent-review.json',O/'result.json',F/'complete.json',F/'prepared.json',C/'manifest.json',wd/'ap_core.qsf']+list(QUALIFICATION)
 for directory in [F,wd/'output_files',wd/'timing']:
  if directory.exists():files += [p for p in directory.iterdir() if p.is_file() and p.suffix in {'.json','.log','.rpt','.tsv','.rbf','.sof'}]
 put(O/'manifest.json',dict(deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}));print(json.dumps(output,indent=2));print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run','seal']);globals()[a.parse_args().action]()
