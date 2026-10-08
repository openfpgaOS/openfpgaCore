#!/usr/bin/env python3
"""Read-only compact-v4 closure review. Explicitly does not enable a fitter."""
from pathlib import Path
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
STAGE=P/'compact-lod5-core-v4';BASE=P/'compact-lod5-core-v3';OUT=P/'compact-remaining-review-v4'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
evidence={}
def bind(p):evidence[str(p.relative_to(ROOT))]=sha(p)
def manifest(p):
 d=json.loads(p.read_text());maps=[d]
 for k in('files','evidence_sha256','proofs','preserved_proofs'):
  if isinstance(d.get(k),dict):maps.append(d[k])
 checked=0
 for items in maps:
  for n,h in items.items():
   if not isinstance(h,str)or not re.fullmatch(r'[a-f0-9]{64}',h):continue
   # Only path keys are file manifests, not hash-valued metadata fields.
   if not ('.'in n or '/'in n):continue
   a=ROOT/n if(ROOT/n).is_file()else p.parent/n
   assert a.is_file()and sha(a)==h,n;checked+=1
 assert checked,p
 bind(p);return d
def main():
 prep=json.loads((STAGE/'prepared.json').read_text());old=json.loads((BASE/'prepared.json').read_text())
 assert prep['parent_prepared_sha256']==sha(BASE/'prepared.json')
 assert prep['source_sha256'].keys()==old['source_sha256'].keys()
 changed=[]
 for n,h in prep['source_sha256'].items():
  assert sha(STAGE/'frozen'/n)==h,n
  if h!=old['source_sha256'][n]:changed.append(n)
 expected={
 'src/fpga/common/gpu_core.v':P/'gpu-paths-v11/gpu_core.v',
 'src/fpga/common/gpu_color_depth_cache.sv':P/'cache-write-speculate-v1/gpu_color_depth_cache.sv',
 'src/fpga/targets/pocket/io_sdram.v':P/'sdram-address-mux-v1/io_sdram.v',
 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v':P/'cpu-rvc-bits-v2/VexiiRiscv.v'}
 top='src/fpga/targets/pocket/core_top.v'
 assert set(changed)==set(expected)|{top}and sorted(changed)==prep['only_changed_files']
 for n,p in expected.items():assert sha(p)==prep['source_sha256'][n];bind(p)
 a=(BASE/'frozen'/top).read_text();b=(STAGE/'frozen'/top).read_text()
 needle='.REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1)'
 assert a.count(needle)==1 and a.replace(needle,needle+', .SPECULATE_WRITE_HOT0(1)')==b
 for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n;bind(ROOT/n)
 cpu=manifest(P/'cpu-rvc-bits-v2/manifest.json')
 gpu=manifest(P/'gpu-paths-review-v11/manifest.json')
 for p in [P/'cache-write-speculate-v1/manifest.json',P/'cache-write-review-v1/manifest.json',
           P/'cache-write-lod5-coupled-v1/manifest.json',P/'sdram-address-review-v1/manifest.json',
           P/'sdram-address-map-review-v1/manifest.json']:
  manifest(p)
 for p in [P/'cpu-rvc-bits-v2/proof/result.json',P/'cpu-rvc-bits-v2/native/result.json',
           P/'gpu-paths-review-v11/result.json',P/'gpu-paths-review-v11/paths-result.json',
           P/'cache-write-speculate-v1/protocol/result.json',P/'cache-write-lod5-coupled-v1/result.json']:
  assert json.loads(p.read_text())['passed'];bind(p)
 report=STAGE/'map-core/result.json';r=json.loads(report.read_text());assert r['passed_map']and not r['full_fit_attempted']
 assert r['prepared_sha256']==sha(STAGE/'prepared.json')
 mr=STAGE/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
 assert sha(mr)==r['candidate_report_sha256'];bind(mr)
 inv=STAGE/'map-core/invocation.json';i=json.loads(inv.read_text());assert i['seed']==2 and not i['fitter_invoked']
 qsf=STAGE/'map-core/job/src/fpga/targets/pocket/ap_core.qsf'
 assert sha(qsf)==i['qsf_sha256']and re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['2']
 assert prep['seed']==2 and not prep['seed_sweep']and prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32 and prep['configured_mhz']==100
 runner=ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_remaining_fit.py'
 inherited=ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_lod5_fit.py'
 assert sha(runner)==prep['stage_recipe_sha256']and sha(inherited)==prep['recipe_sha256']
 assert "review['component_and_coupled_proofs_complete']"in runner.read_text()
 assert "review['passed_for_resource_fit'] and not review['deployment_ready']"in inherited.read_text()
 assert "'--seed-offsets', '0', '--jobs', '1'"in inherited.read_text()
 for p in[STAGE/'prepared.json',BASE/'prepared.json',STAGE/'integration.patch',report,inv,qsf,runner,inherited,Path(__file__)]:bind(p)
 OUT.mkdir(exist_ok=False);shutil.copyfile(__file__,OUT/'recipe.py')
 result=dict(passed_source_closure=True,component_and_coupled_proofs_complete=True,
  passed_for_resource_fit=False,deployment_ready=False,prepared_sha256=sha(STAGE/'prepared.json'),
  only_changed_files=changed,combined_map_delta=r['delta']['apf_top'],
  hold_reason='Root explicitly holds compact-v4 synthesis-only: the distinct measured CPU ready/control path still needs a reviewed remedy; cache full-strobe shortcut is under separate development.',
  findings=[
   'Exactly four RTL files plus one cache instance parameter differ from compact-v3. All clocks, settings, constraints, CPU cache sizes and remaining sources are unchanged.',
   'CPU only overrides four decompressed instruction bits. All binary compressed encodings and upper-half patterns plus six native output/cycle-exact pairs pass. Illegal flag and original raw32 bypass remain unchanged; no four-state X-equivalence claim.',
   'GPU raw XY is captured after final sorting, before identical determinant issue. Invalid depth accumulator address is unowned; later readback/pending-source captures retain priority. CPU-only ring data removes a mux whose alternate writer is constant absent. Bound native/boundary/ownership replay establishes exact outputs and cycles.',
   'Cache hot0 speculation shortens the AW data-address path with one extra existing-state cycle on hot1 hits; matched recycler frames cost about0.200ms and preserve all16 color/depth buffers.',
   'SDRAM changes only address output decode; every address-bearing state has matching consumed bits, all other outputs/cycles match both full-bank physical schedules.',
   'Runner requires the completed component/coupled gate and binds every reviewed source/evidence hash before one seed2 fit. This record deliberately does not supply an enabling gate.'],
  limits=['No fitter was invoked by this review. No physical-clock or Pocket FPS result.',
          'The combined cache performance experiment uses the previously qualified raster/geometry model and separate zero-cycle equivalence bridges, not a complete current-CPU whole-game simulation.'],
  evidence_sha256=evidence)
 (OUT/'review.json').write_text(json.dumps(result,indent=2)+'\n')
 (OUT/'manifest.json').write_text(json.dumps({p.name:sha(p)for p in OUT.iterdir()if p.is_file()},indent=2)+'\n')
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
