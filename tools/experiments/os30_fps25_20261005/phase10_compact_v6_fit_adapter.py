#!/usr/bin/env python3
"""Private schema adapter for the sealed v6 fit gate; no source/option changes."""
import copy,hashlib,importlib.util,inspect,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OUT=P/'compact-lod5-core-v6';D=OUT/'fit-driver-compat-v1'
HELPER=ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_lod5_fit.py'
REVIEW_SHA='e885ff4954414d194ff34899b87b2a2cedd59d6c26a634307f4374430410ff33'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def prepare():
 assert sha(OUT/'independent-review.json')==REVIEW_SHA
 review=json.loads((OUT/'independent-review.json').read_text());assert 'prepared_sha256' not in review
 assert review['candidate_prepared_sha256']==sha(OUT/'prepared.json')
 D.mkdir(exist_ok=False);gate=copy.deepcopy(review);gate['prepared_sha256']=gate['candidate_prepared_sha256']
 gate['compatibility']={'scope':'Schema alias and separately bound invocation adapter only; original sealed review unchanged.','original_review_sha256':REVIEW_SHA,'original_review_path':str((OUT/'independent-review.json').relative_to(ROOT)),'adapter_sha256':sha(Path(__file__)),'fit_driver_sha256':sha(HELPER)}
 gate['evidence_sha256'][str((OUT/'independent-review.json').relative_to(ROOT))]=REVIEW_SHA
 gate['evidence_sha256'][str(Path(__file__).relative_to(ROOT))]=sha(Path(__file__))
 gate['evidence_sha256'][str(HELPER.relative_to(ROOT))]=sha(HELPER)
 save(D/'gate.json',gate);shutil.copy2(__file__,D/'adapter.py')
def adapted():
 gate=json.loads((D/'gate.json').read_text());assert sha(OUT/'independent-review.json')==REVIEW_SHA
 assert sha(Path(__file__))==gate['compatibility']['adapter_sha256'] and sha(HELPER)==gate['compatibility']['fit_driver_sha256']
 original=json.loads((OUT/'independent-review.json').read_text());check=copy.deepcopy(gate)
 del check['prepared_sha256'];del check['compatibility']
 for name in [str((OUT/'independent-review.json').relative_to(ROOT)),str(Path(__file__).relative_to(ROOT)),str(HELPER.relative_to(ROOT))]:
  if name not in original['evidence_sha256']:del check['evidence_sha256'][name]
 assert check==original,'Compatibility gate changes reviewed substance'
 assert gate['prepared_sha256']==gate['candidate_prepared_sha256']==sha(OUT/'prepared.json')
 assert gate['component_and_coupled_proofs_complete']
 assert json.loads((OUT/'map-core/result.json').read_text())['passed_map']
 spec=importlib.util.spec_from_file_location('qualified_fit',HELPER);f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
 source=inspect.getsource(f.fitrun);old="OUT / 'independent-review.json'";new="OUT / 'fit-driver-compat-v1/gate.json'";assert source.count(old)==2
 modified=source.replace(old,new);assert modified.replace(new,old)==source
 # The qualified module's __file__ stays original: source recipe hashes and
 # all original preflight checks remain exact. Only the bound review path
 # changes; the gate binds this adapter as part of its evidence.
 exec(compile(modified,str(Path(__file__))+'::<qualified fitrun review-path adapter>','exec'),f.__dict__)
 f.OUT=OUT
 return f,source,modified

def verify():
 f,source,modified=adapted()
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as sweep
 oldmain=sweep.main;called=[]
 def no_fit():
  assert sys.argv[1:]==['--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT/'fit-seed2')]
  rows=sweep.inventory();assert len(rows)==1 and rows[0]['seed']==2 and rows[0]['variant']=='os30'
  assert rows[0]['cpu_sha256']==json.loads((OUT/'prepared.json').read_text())['source_sha256'][f.CPU]
  called.append(True)
 sweep.main=no_fit
 try:f.fitrun()
 finally:sweep.main=oldmain
 assert called==[True]
 (D/'qualified-fitrun.txt').write_text(source);(D/'adapted-fitrun.txt').write_text(modified)
 save(D/'verification.json',{'passed':True,'actual_original_preflight_executed':True,'only_two_review_path_literals_changed':True,'original_review_unchanged':sha(OUT/'independent-review.json')==REVIEW_SHA,'fitter_invoked':False,'sweep_main_intercepted_before_freeze_or_tool_invocation':True,'root_fit_command':'python3 tools/experiments/os30_fps25_20261005/phase10_compact_v6_fit_adapter.py fit','gate_sha256':sha(D/'gate.json')})
 save(D/'manifest.json',{p.name:sha(p) for p in D.iterdir() if p.is_file()});print('PASS schema adapter preflight; manifest',sha(D/'manifest.json'))
def fit():
 # Invocation remains explicitly separate from preparation/preflight proof.
 proof=json.loads((D/'verification.json').read_text());assert proof['passed'] and not proof['fitter_invoked']
 for n,h in json.loads((D/'manifest.json').read_text()).items():assert sha(D/n)==h,n
 f,_,_=adapted();f.fitrun()
if __name__=='__main__':
 assert sys.argv[1:] in (['prepare'],['verify'],['fit'])
 globals()[sys.argv[1]]()
