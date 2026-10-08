#!/usr/bin/env python3
"""One ordinary timing-driven v7 seed-2 fit with frozen LC placement assignments."""
import argparse,hashlib,json,pathlib,re,shlex,shutil,subprocess,sys,time
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'lc-v7-timing-placement-v4';FIT=OUT/'fit-seed2'
ORIGIN=P/'compact-lod5-core-v7/fit-seed2/jobs/pocket-os30-s2'
LC=P/'placement-preservation-readonly-v1/lc/src/fpga/targets/pocket/ap_core.qsf'
QSF=pathlib.Path('src/fpga/targets/pocket/ap_core.qsf')
OLD='set_location_assignment LABCELL_X50_Y32_N33 -to "core_top:ic|cpu_system:cpu|VexiiRiscv:cpu|WideOr109~0"'
NEW=OLD.replace('LABCELL_X50_Y32_N33','LABCELL_X44_Y34_N6')
sha=lambda f:hashlib.sha256(f.read_bytes()).hexdigest()
def put(f,d):f.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n')
def inv(d):return {str(p.relative_to(d)):sha(p) for p in sorted(d.rglob('*')) if p.is_file()}
def source_guard():
 d=json.loads((P/'eco-v7-local-placement-v1/prepared.json').read_text())['source_db_sha256']
 assert inv(ORIGIN)==d,'Original v7 changed';return d
def canonical_inputs():
 baseline=P/'eco-v7-local-placement-v1/baseline-nodes.tsv'
 names={r[1]:r[2] for l in baseline.read_text().splitlines() if (r:=l.split('\t'))}
 log=P/'lc-v7-timing-placement-v2/fit-seed2/fit.log'
 errors=re.findall(r'Error \(11943\): Can.t pack register node (.*?) into non-LAB location',log.read_text())
 assert len(errors)==125 and len(set(errors))==117
 excluded={n for n in errors if n in names};assert len(excluded)==19 and all(names[n].startswith('FF_') for n in excluded)
 return names,excluded
def expected_qsf():
 original=(ORIGIN/QSF).read_text();locations=[l for l in LC.read_text().splitlines() if l.startswith('set_location_assignment ')]
 assert len(locations)==97942 and locations.count(OLD)==1
 assert sum('LABCELL_X50_Y32_N33' in l for l in locations)==1,'Unchanged alias could conflict'
 names,excluded=canonical_inputs()
 locations=[NEW if l==OLD else l for l in locations if shlex.split(l)[3] in names and shlex.split(l)[3] not in excluded]
 assert len(locations)==53143
 parents={n.removesuffix('~_Duplicate_1') for n in excluded};assert len(parents)==19
 removed=[l for l in locations if shlex.split(l)[3] in parents];assert len(removed)==19 and all(shlex.split(l)[1].startswith('DDIOOUTCELL_') for l in removed)
 locations=[l for l in locations if shlex.split(l)[3] not in parents];assert len(locations)==53124
 assert sum(shlex.split(l)[1].startswith('PIN_') for l in locations)==224
 return ''.join(l for l in original.splitlines(keepends=True) if not l.startswith('set_location_assignment '))+'\n# Exact v7 LC back-annotation; one approved LUT placement change.\n'+'\n'.join(locations)+'\n'
def prepare():
 before=source_guard();OUT.mkdir(exist_ok=False);shutil.copytree(ORIGIN,OUT/'job')
 assert inv(OUT/'job')==before
 (OUT/'job'/QSF).write_text(expected_qsf())
 nonloc=lambda s:[l for l in s.splitlines() if l and not l.startswith(('#','set_location_assignment '))]
 assert nonloc((OUT/'job'/QSF).read_text())==nonloc((ORIGIN/QSF).read_text())
 d=dict(original_job=str(ORIGIN.relative_to(R)),original_inventory_sha256=before,prepared_job_sha256=inv(OUT/'job'),recipe_sha256=sha(pathlib.Path(__file__)),lc_qsf_sha256=sha(LC),original_v7_qsf_sha256=sha(ORIGIN/QSF),candidate_qsf_sha256=sha(OUT/'job'/QSF),location_assignments=53124,released_parent_ddio_targets=sorted(n.removesuffix('~_Duplicate_1') for n in canonical_inputs()[1]),parent_v3_fit_log_sha256=sha(P/'lc-v7-timing-placement-v3/fit-seed2/fit.log'),excluded_actual_ff_names=sorted(canonical_inputs()[1]),previously_covered_sites_released=362,alias_only_hard_sites_released=324,known_invalid_ff_sites_released=19,inherited_parent_ddio_sites_released=19,baseline_node_census_sha256=sha(P/'eco-v7-local-placement-v1/baseline-nodes.tsv'),mapped_type_census_sha256=sha(P/'lc-mapped-type-audit-v1/mapped-node-types.tsv'),parent_fit_log_sha256=sha(P/'lc-v7-timing-placement-v2/fit-seed2/fit.log'),changed_placement_assignments=[dict(before=OLD,after=NEW)],seed=2,configured_mhz=100,synthesis_rerun=False,seed_sweep=False,production_modified=False,guaranteed_placement_preservation=False,source_only_reproducibility=False,notes=['Original non-location QSF lines preserved exactly, including overridden early assignments.','Ordinary fitter is explicitly timing driven; this is not the timing-disabled ECO flow.','Post-fit duplicates and aliases may be ignored or remapped; actual warnings and placement must be audited.','Successful normal-build closure must include these text location constraints.'])
 put(OUT/'prepared.json',d);shutil.copyfile(__file__,OUT/'recipe.py');print(sha(OUT/'prepared.json'))
def checked(require_review=True):
 d=json.loads((OUT/'prepared.json').read_text());assert sha(pathlib.Path(__file__))==d['recipe_sha256'];assert sha(LC)==d['lc_qsf_sha256'];source_guard();assert inv(OUT/'job')==d['prepared_job_sha256'];assert (OUT/'job'/QSF).read_text()==expected_qsf();assert sha(P/'lc-v7-timing-placement-v2/fit-seed2/fit.log')==d['parent_fit_log_sha256'];assert sha(P/'lc-v7-timing-placement-v3/fit-seed2/fit.log')==d['parent_v3_fit_log_sha256'];assert sha(P/'eco-v7-local-placement-v1/baseline-nodes.tsv')==d['baseline_node_census_sha256'];assert sha(P/'lc-mapped-type-audit-v1/mapped-node-types.tsv')==d['mapped_type_census_sha256']
 if require_review:
  gate=json.loads((OUT/'independent-review.json').read_text());assert gate['passed_for_one_fit'];assert gate['prepared_sha256']==sha(OUT/'prepared.json')
 return d
def run():
 d=checked();FIT.mkdir(exist_ok=False);shutil.copytree(OUT/'job',FIT/'job');wd=FIT/'job/src/fpga/targets/pocket';assert inv(FIT/'job')==d['prepared_job_sha256']
 sys.path.insert(0,str(R/'tools'));import sweep_target_matrix as sweep
 phases=[('fit',['quartus_fit','ap_core','--tdc=on','--read_settings_files=on','--write_settings_files=off']),('asm',['quartus_asm','ap_core']),('sta',['quartus_sta','ap_core']),('corners',['quartus_sta','-t',str(FIT/'job/tools/report_core_timing.tcl'),'ap_core','timing'])]
 put(FIT/'prepared.json',dict(prepared_sha256=sha(OUT/'prepared.json'),review_sha256=sha(OUT/'independent-review.json'),recipe_sha256=sha(pathlib.Path(__file__)),phases=phases,fit_count=1,seed=2));shutil.copyfile(__file__,FIT/'recipe.py')
 result=dict(directory=str(wd),seed=2,variant='os30',returncodes={},state='running',valid=False);start=time.monotonic()
 for label,args in phases:
  cmd=sweep.docker(FIT,'pocket',wd,args);put(FIT/(label+'-invocation.json'),dict(command=cmd));print('START',label,flush=True)
  with (FIT/(label+'.log')).open('x') as f:rc=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT).returncode
  result['returncodes'][label]=rc;print('DONE',label,rc,flush=True)
  if rc and label!='corners':result['state']='failed';break
 else:result.update(sweep.parse_result(wd,'ap_core'));result.update(state='complete',valid=True)
 result['seconds']=time.monotonic()-start;assert (wd/'ap_core.qsf').read_text()==expected_qsf();checked();put(FIT/'complete.json',result);print(json.dumps(result,indent=2),flush=True)
def seal():
 checked();result=json.loads((FIT/'complete.json').read_text());wd=pathlib.Path(result['directory']);frequencies={};resources={}
 report=wd/'output_files/ap_core.fit.rpt';text=report.read_text(errors='replace') if report.exists() else ''
 for key,pattern in {'alms_needed':r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)','placed_alms':r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)','labs':r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)','m10ks':r';\s*M10K blocks\s*;\s*([\d,]+)','dsps':r';\s*Total DSP Blocks\s*;\s*([\d,]+)'}.items():
  m=re.search(pattern,text);resources[key]=int(m.group(1).replace(',','')) if m else None
 if result['returncodes'].get('sta')==0:
  text=(wd/'output_files/ap_core.sta.rpt').read_text(errors='replace')
  for t in (0,85):
   section=text.split(f'; Slow 1100mV {t}C Model Fmax Summary',1)[1].split('Model Setup Summary',1)[0];m=re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]',section);assert m;frequencies[str(t)]=float(m.group(1))
 worst=result.get('worst',{});success=all(result['returncodes'].get(x)==0 for x in ('fit','asm','sta'))
 passed=success and len(frequencies)==2 and min(frequencies.values())>90 and all(worst.get(x,-1)>0 for x in ('hold','recovery','removal','pulse_width'))
 warnings=[l for l in (FIT/'fit.log').read_text(errors='replace').splitlines() if re.search('ignored|does not exist|cannot find|can.t find|conflict|illegal|unrecognized|Warning',l,re.I)]
 data=dict(comparative_physical_gate_pass=passed,main_clock_slow_fmax_mhz=frequencies,configured_mhz=100,all_configured_timing_checks_pass=result.get('all_checks_pass',False),resources=resources,worst_timing=worst,returncodes=result['returncodes'],seconds=result['seconds'],assignment_warnings=warnings,seed=2,seed_sweep=False,deployment_ready=False,production_modified=False,source_only_reproducibility=False,placement_preservation='Not yet audited; LC assignments alone do not establish preservation.')
 put(OUT/'fit-result.json',data)
 files=[pathlib.Path(__file__),OUT/'prepared.json',OUT/'independent-review.json',OUT/'fit-result.json',FIT/'prepared.json',FIT/'complete.json',FIT/'recipe.py',wd/'ap_core.qsf']
 for directory in [FIT,wd/'output_files',wd/'timing']:
  if directory.exists():files += [p for p in directory.iterdir() if p.is_file() and p.suffix in {'.json','.log','.rpt','.tsv','.rbf','.sof'}]
 put(OUT/'fit-manifest.json',dict(deployment_ready=False,evidence_sha256={str(f.relative_to(R)):sha(f) for f in sorted(set(files))}));print(json.dumps(data,indent=2))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run','seal']);args=a.parse_args();globals()[args.action]()
