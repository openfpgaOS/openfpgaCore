#!/usr/bin/env python3
"""Map-only LOD5/span-v6 plus previously generated queue-only CPU; no fitter."""
import copy,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
from phase10_cpu_queue_adapter import adapt
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'lod5-cram-span-core-v2';Q=P/'cpu-queues-v1';OUT=P/'lod5-queues-map-v1'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';WRAP='src/fpga/common/cpu_system.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
def entities(path):
 text=path.read_text(errors='replace');out={}
 for line in text.split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
  r=[x.strip() for x in line.split(';')][1:-1]
  if len(r)>=10 and r[-2] in ['VexiiRiscv','cpu_system','apf_top','geometry_load_alias','gpu_core']:
   out[r[-2]]=dict(aluts=r[1],registers=r[2],memory_bits=r[3],dsp=r[4],path=r[-3])
 return out
def prepare():
 parent=json.loads((BASE/'prepared.json').read_text());recipe=json.loads((Q/'recipe.json').read_text())
 original=json.loads((P/'lrsc-only-v1/control/generated.json').read_text())['flags']
 assert [recipe['changes'].get(v,v) for v in original]==recipe['flags']
 assert len(recipe['changes'])==5
 assert sha(Q/'VexiiRiscv.v')==json.loads((Q/'generated.json').read_text())['rtl_sha256']
 assert json.loads((Q/'regression/result.json').read_text())['passed']
 OUT.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',OUT/'frozen')
 for n,h in parent['source_sha256'].items():assert sha(OUT/'frozen'/n)==h,n
 shutil.copyfile(Q/'VexiiRiscv.v',OUT/'frozen'/CPU)
 w=OUT/'frozen'/WRAP;w.write_text(adapt(w.read_text()))
 sources={str(p.relative_to(OUT/'frozen')):sha(p) for p in sorted((OUT/'frozen').rglob('*')) if p.is_file()}
 changed=[n for n in sources if sources[n]!=parent['source_sha256'][n]]
 assert sorted(changed)==sorted([CPU,WRAP])
 save(OUT/'prepared.json',dict(scope=__doc__,source_sha256=sources,only_changed_files=changed,
  original_cpu_sha256=parent['source_sha256'][CPU],cpu_sha256=sha(Q/'VexiiRiscv.v'),
  exact_queue_flag_changes=recipe['changes'],all_other_generator_flags_identical=True,
  cpu_regenerated=False,full_fit_authorized=False,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),Path(__file__).with_name('phase10_cpu_queue_adapter.py'),BASE/'final-manifest.json',Q/'recipe.json',Q/'generated.json',Q/'final-manifest.json',Q/'regression/result.json']}))
 print(OUT)
def run():
 prep=json.loads((OUT/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(OUT/'frozen'/n)==h,n
 for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 row=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text())[0]);row['cpu_sha256']=prep['cpu_sha256']
 directory,project=fit.prepare(OUT/'frozen',OUT/'job',row,2,4)
 qsf=directory/(project+'.qsf');assert re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['2']
 cmd=fit.docker(OUT,'pocket',directory,['quartus_map',project]);save(OUT/'invocation.json',dict(command=cmd,qsf_sha256=sha(qsf),seed=2,fitter_invoked=False))
 with (OUT/'map.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 report=directory/'output_files/ap_core.map.rpt'
 baseline=next((BASE/'fit-seed2').rglob('ap_core.map.rpt'))
 a,b=entities(baseline),entities(report)
 delta={}
 for n in sorted(a.keys()&b.keys()):
  delta[n]={k:int(b[n][k].split()[0].replace(',',''))-int(a[n][k].split()[0].replace(',','')) for k in ['aluts','registers','memory_bits','dsp']}
 save(OUT/'result.json',dict(passed_map=True,full_fit_attempted=False,baseline=a,candidate=b,delta=delta,
  baseline_report_sha256=sha(baseline),candidate_report_sha256=sha(report),prepared_sha256=sha(OUT/'prepared.json'),qsf_sha256=sha(qsf),scope='Synthesis resources only; ALUT/FF changes do not establish placed ALM/LAB or timing.'))
 print(json.dumps(delta,indent=2))
if __name__=='__main__':globals()[sys.argv[1]]()
