#!/usr/bin/env python3
"""One map-only GPUv8 source composition over exact compact LOD5v1."""
import copy,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
from phase10_lod5_queues_map import entities
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v1';OUT=P/'gpu-queue-map-v8'
GPU='src/fpga/common/gpu_core.v';CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
CAND=P/'gpu-queue-pack-v8/gpu_core.v';PROOF=P/'gpu-queue-review-v8/manifest.json'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
 parent=json.loads((BASE/'prepared.json').read_text());proof=json.loads(PROOF.read_text())
 assert proof['passed_for_map_only'] and proof['candidate_sha256']==sha(CAND)
 for n,h in proof['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 for n,h in parent['source_sha256'].items():assert sha(BASE/'frozen'/n)==h,n
 OUT.mkdir(exist_ok=False);shutil.copytree(BASE/'frozen',OUT/'frozen')
 shutil.copyfile(CAND,OUT/'frozen'/GPU)
 sources={str(p.relative_to(OUT/'frozen')):sha(p) for p in sorted((OUT/'frozen').rglob('*')) if p.is_file()}
 changed=[n for n,h in sources.items() if h!=parent['source_sha256'][n]]
 assert changed==[GPU]
 save(OUT/'prepared.json',dict(source_sha256=sources,only_changed_files=changed,
  seed=2,cpu_cache_kib=128,clocks_unchanged=True,full_fit_authorized=False,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),BASE/'prepared.json',PROOF]}))
def run():
 prep=json.loads((OUT/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(OUT/'frozen'/n)==h,n
 for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 row=copy.deepcopy(json.loads((BASE/'fit-seed2/inventory.json').read_text())[0])
 assert row['seed']==2 and row['cpu_sha256']==prep['source_sha256'][CPU]
 directory,project=fit.prepare(OUT/'frozen',OUT/'job',row,2,4)
 qsf=directory/(project+'.qsf')
 assert re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['2']
 cmd=fit.docker(OUT,'pocket',directory,['quartus_map',project])
 save(OUT/'invocation.json',dict(command=cmd,qsf_sha256=sha(qsf),seed=2,fitter_invoked=False))
 with (OUT/'map.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 report=directory/'output_files/ap_core.map.rpt'
 baseline=next((BASE/'fit-seed2/jobs').rglob('ap_core.map.rpt'))
 a,b=entities(baseline),entities(report)
 delta={n:{k:int(b[n][k].split()[0].replace(',',''))-int(a[n][k].split()[0].replace(',',''))
  for k in ['aluts','registers','memory_bits','dsp']} for n in sorted(a.keys()&b.keys())}
 save(OUT/'result.json',dict(passed_map=True,full_fit_attempted=False,baseline=a,candidate=b,delta=delta,
  baseline_report_sha256=sha(baseline),candidate_report_sha256=sha(report),prepared_sha256=sha(OUT/'prepared.json'),
  qsf_sha256=sha(qsf),scope='One map-only source screen; no inference of fitted ALM/LAB or Fmax.'))
 print(json.dumps(delta,indent=2))
if __name__=='__main__':globals()[sys.argv[1]]()
