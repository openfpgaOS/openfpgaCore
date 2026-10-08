#!/usr/bin/env python3
"""Independent exact-source and dry generated-QSF gate; invokes no Quartus."""
import ast,copy,json,re,shutil,sys,tempfile
from pathlib import Path
import phase10_compact_v7_seed1_fit as c
ROOT,BASE,OUT=c.ROOT,c.BASE,c.OUT
sha,save=c.sha,c.save

def main():
 assert not(OUT/'independent-review.json').exists()
 prep=c.checked_candidate(require_review=False)
 assert sha(OUT/'prepared.json')=='5be2837e16b5d000dfd61e64f07499588ec4cb5bfa49eba1b810c71adb216d17'
 assert not c.FIT.exists(),'No fit expected during this independent gate'
 review=json.loads((BASE/'independent-review.json').read_text())
 # Verify all direct component result/proof bindings in the approved parent,
 # then each bound manifest's own artifact map (files or root-relative).
 hashes_checked=0
 for name,digest in review['evidence_sha256'].items():
  p=ROOT/name;assert sha(p)==digest,name;hashes_checked+=1
  if p.name.endswith('manifest.json'):
   data=json.loads(p.read_text())
   for rel,h in data.get('files',{}).items():assert sha(p.parent/rel)==h,rel;hashes_checked+=1
   for rel,h in data.get('evidence_sha256',{}).items():assert sha(ROOT/rel)==h,rel;hashes_checked+=1
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as sweep
 rows=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text()))
 assert len(rows)==1 and rows[0]['target']=='pocket'and rows[0]['variant']=='os30'and rows[0]['seed']==2
 tasks=sweep.seed_candidates(rows,[-1]);assert len(tasks)==1 and tasks[0][1]==1
 d=OUT/'independent-review-proof';d.mkdir(exist_ok=False)
 with tempfile.TemporaryDirectory(prefix='v7-seed1-review-')as tmp:
  dest=Path(tmp)/'job';sweep.prepare(OUT/'frozen',dest,rows[0],1,4)
  qsf=dest/'src/fpga/targets/pocket/ap_core.qsf'
  parent=BASE/'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf'
  assert re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['1']
  assert qsf.read_text()==parent.read_text().replace('set_global_assignment -name SEED 2\n','set_global_assignment -name SEED 1\n')
  shutil.copy2(qsf,d/'effective-seed1.qsf')
  for name,h in prep['source_sha256'].items():
   if name!='src/fpga/targets/pocket/ap_core.qsf':assert sha(dest/name)==h,name
 # Inspect the literal launch options without invoking fitrun().
 tree=ast.parse(Path(c.__file__).read_text())
 run=next(n for n in tree.body if isinstance(n,ast.FunctionDef)and n.name=='fitrun')
 argv=next(n.value for n in ast.walk(run)if isinstance(n,ast.Assign)and any(isinstance(t,ast.Attribute)and isinstance(t.value,ast.Name)and t.value.id=='sys'and t.attr=='argv'for t in n.targets))
 strings=[n.value for n in ast.walk(argv)if isinstance(n,ast.Constant)and isinstance(n.value,str)]
 assert strings==['--target','pocket','--variant','os30','--seed-offsets','-1','--jobs','1','--processors','4','--output'],strings
 assert not c.FIT.exists()
 findings=[
  'All frozen files match the approved compact-v7 source closure byte for byte; CPU128KiB D/32KiB I, arithmetic, ISA, clocks, constraints, geometry, GPU and memory interfaces are unchanged.',
  'The parent independent component/native/coupled gate and its bound manifests/results were rehashed. Reuse is through exact source closure and the already-approved zero-cycle component bridges; no new native-game measurement is claimed.',
  'A dry invocation of the actual shared job-preparation function produces a QSF exactly equal to the previous routed v7 QSF except effective SEED2 becomes1. Every other generated source remains identical. The source seed file stays2.',
  'The helper selects exactly one os30 task with offset-1 from saved2, one job, four processors, and rejects an already-existing fit directory. No seed sweep, resume or automatic retry path is exposed.',
  'The report helper requires one result and actual generated QSF equality, preserves fit failures and negative timing, reports both slow corners and all timing checks, and distinguishes the comparative >90MHz target from configured100MHz closure.',
  'No production, source seed, SDK/application, SD, clock or physical option is changed by this stage. A qualifying result still needs normal-build seed1 source/settings closure before any promotion.'
 ]
 save(d/'result.json',dict(passed=True,fitter_invoked=False,quartus_invoked=False,hash_bindings_checked=hashes_checked,frozen_files=len(prep['source_sha256']),tasks=[dict(target=r['target'],variant=r['variant'],seed=s)for r,s in tasks],actual_dry_qsf_only_change='SEED2 to1',source_saved_seed=2))
 (d/'REPORT.txt').write_text('Independent exact-v7 alternate-placement review\n\n'+'\n\n'.join(findings)+'\n\nPassed only for one private effective-seed1 resource/timing run. Deployment remains false. No fitter or Quartus process was invoked by this review.\n')
 shutil.copy2(__file__,d/'recipe.py')
 evidence=[OUT/'prepared.json',BASE/'prepared.json',BASE/'independent-review.json',BASE/'final-manifest.json',BASE/'fit-seed2/inventory.json',BASE/'fit-seed2/jobs/pocket-os30-s2/src/fpga/targets/pocket/ap_core.qsf',Path(c.__file__),c.REPORT_RECIPE,Path(c.common.__file__),Path(sweep.__file__),Path(__file__),*d.iterdir()]
 evidence +=[ROOT/n for n in review['evidence_sha256'] if n.endswith('manifest.json')]
 save(OUT/'independent-review.json',dict(passed_for_resource_fit=True,deployment_ready=False,component_and_coupled_proofs_complete=True,exact_source_binding_passed=True,prepared_sha256=sha(OUT/'prepared.json'),parent_review_sha256=sha(BASE/'independent-review.json'),effective_seed=1,saved_seed=2,seed_sweep=False,exactly_one_fit=True,findings=findings,limits=['Only the explicitly selected unchanged-v7 seed1 placement is authorized by this gate.','No new native-game or Pocket FPS claim; 25FPS remains unproven.','This read-only/private review invoked no fitter and made no production or device change.'],evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(evidence))}))
 c.checked_candidate(require_review=True)
 print(json.dumps(dict(independent_review_sha256=sha(OUT/'independent-review.json'),hash_bindings_checked=hashes_checked,frozen_files=len(prep['source_sha256']))))
if __name__=='__main__':main()
