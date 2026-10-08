#!/usr/bin/env python3
"""Independent static/source fit gate. Never invokes Quartus or a fit action."""
from pathlib import Path
import ast,hashlib,importlib.util,json,re,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';T=R/'tools/experiments/os30_fps25_20261005'
H=T/'phase10_parallel_control_fit.py';C=P/'parallel-control-map-v1';F=P/'parallel-control-fit-v1';N=P/'normal-build-parallel-control-v1';O=P/'parallel-control-fit-source-review-v1';Q=Path('src/fpga/targets/pocket/ap_core.qsf')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def inv(d):return {str(p.relative_to(d)):sha(p) for p in sorted(d.rglob('*')) if p.is_file()}
def main():
 assert not O.exists() and not (F/'independent-review.json').exists() and not (F/'fit-seed2').exists()
 assert sha(F/'prepared.json')=='b28af76838ea36c52890e4d22ece6a1cf03af06030ff3af36aecf261b3e3ded9'
 assert sha(C/'manifest.json')=='ca5ff6cc875075c84950d31e68d637ff16ab494fecb407c2437ef9277a29a16b'
 fit=json.loads((F/'prepared.json').read_text());source=json.loads((C/'prepared.json').read_text());resource=json.loads((C/'result.json').read_text())
 assert fit['recipe_sha256']==sha(H) and fit['component_manifest_sha256']==sha(C/'manifest.json')
 evidence={};count=0
 def bind(p):evidence[str(p.relative_to(R))]=sha(p)
 def checkfile(p,h):
  nonlocal count
  assert sha(p)==h,p;count+=1;bind(p)
 def check_manifest(p,flat=False):
  m=json.loads(p.read_text());bind(p)
  for n,h in (m if flat else m.get('files',{})).items():checkfile(p.parent/n,h)
  if not flat:
   for n,h in m.get('evidence_sha256',{}).items():checkfile(R/n,h)
 check_manifest(C/'manifest.json')
 for n,h in fit['qualification_sha256'].items():
  p=R/n;checkfile(p,h);check_manifest(p,p==N/'manifest.json')
 assert resource['passed_map']
 assert inv(C/'map-core/job')==fit['mapped_job_sha256']==inv(F/'job')
 assert fit['seed']==2 and fit['fit_count']==1 and not fit['seed_sweep']
 assert fit['original_pins']==224 and fit['internal_location_constraints']==0
 for n,h in fit['source_input_sha256'].items():checkfile(F/'job'/n,h)
 assert len(source['source_sha256'])==115
 for n,h in source['source_sha256'].items():checkfile(C/'frozen'/n,h)
 parent=json.loads((P/'control-cones-compose-map-v1/prepared.json').read_text())['source_sha256']
 delta=sorted(k for k in parent if parent[k]!=source['source_sha256'][k])
 assert delta==['src/fpga/common/gpu_color_depth_cache.sv','src/fpga/common/gpu_core.v','src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
 q=(F/'job'/Q).read_text();assert re.findall(r'^set_global_assignment -name SEED (\d+)$',q,re.M)==['2']
 assert (F/'job'/Q).read_bytes()==(P/'control-cones-compose-map-v1/map-core/job'/Q).read_bytes()
 locations=[l for l in q.splitlines() if l.startswith('set_location_assignment ')];assert len(locations)==224 and all('PIN_' in l for l in locations)
 n=json.loads((N/'checks/selected-binding.json').read_text());assert n['mapped_qsf_sha256']==sha(F/'job'/Q)==fit['qsf_sha256'] and n['selected_prepared_sha256']==sha(C/'prepared.json')
 assert not list((F/'job').rglob('*.fit.rpt')) and not list((F/'job').rglob('*.sta.rpt')) and not (F/'job'/Q.parent/'timing').exists()
 # The entire executed phase/return-code/fresh20-row logic is unchanged from
 # the previously reviewed fit driver; only globals identify the new job.
 def function(path,name):return next(x for x in ast.parse(path.read_text()).body if isinstance(x,ast.FunctionDef) and x.name==name)
 assert ast.dump(function(H,'run'),include_attributes=False)==ast.dump(function(T/'phase10_control_cones_fit.py','run'),include_attributes=False)
 text=H.read_text()
 for clause in ["resource_budgets={'needed_alms':18480,'placed_alms':18480,'labs':1848,'m10ks':308,'dsps':66}","resources_budget_pass=resources_complete and all(resources[k]<=limit for k,limit in resource_budgets.items())","passed=complete and slow_keys_complete and min(freq.values())>90 and resources_budget_pass and all(worst.get(k,-1)>0 for k in ('hold','recovery','removal','pulse_width'))","assert len(rows)==20","assert len(re.findall(r'^RESULT ',log,re.M))==20"]:assert clause in text
 O.mkdir();shutil.copy2(__file__,O/'recipe.py')
 for p in [H,F/'prepared.json',C/'prepared.json',C/'result.json',N/'checks/selected-binding.json',P/'cpu-parallel-control-repro-v1/manifest.json',P/'cpu-parallel-control-regenerate-v1/manifest.json',T/'phase10_control_cones_fit.py',R/'tools/sweep_target_matrix.py'] :bind(p)
 review=dict(passed_for_one_fit=True,prepared_sha256=sha(F/'prepared.json'),deployment_ready=False,blocking_findings=[],source_files=115,mapped_job_files=len(fit['mapped_job_sha256']),evidence_rehashed=count,exact_map_job_copy=True,only_source_delta=delta,normal_selected_qsf_exact=True,seed=2,original_pins=224,internal_placement_constraints=0,clocks_and_settings_unchanged=True,physical_comparison_gate='Both main-clock slow Fmax values strictly >90MHz; all20 fresh corner/check rows; strictly positive hold/recovery/removal/pulse checks; complete positive resources bounded at needed/placedALM18480,LAB1848,M10K308,DSP66.',configured100MHz_setup_closure_not_implied=True,root_authorized_fit_count=1,no_CAD_invoked_by_review=True,evidence_sha256=evidence)
 put(O/'review.json',review);put(F/'independent-review.json',review)
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in [Path(__file__),O/'recipe.py',O/'review.json',F/'independent-review.json',F/'prepared.json',H,C/'manifest.json',N/'manifest.json']}))
 print('REVIEW',sha(F/'independent-review.json'));print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':main()
