#!/usr/bin/env python3
"""Read-only fixed-seed resource-fit gate for exact compact-v3 source closure."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OUT=P/'compact-lod5-core-v3';BASE=P/'compact-lod5-core-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();evidence={}
def bind(p):evidence[str(p.relative_to(ROOT))]=sha(p)
def manifest(p):
 j=json.loads(p.read_text());entries=j.get('evidence_sha256',j)
 for n,h in entries.items():
  if not isinstance(h,str)or len(h)!=64:continue
  f=ROOT/n if(ROOT/n).is_file()else p.parent/n
  assert sha(f)==h,n
 bind(p);return j
prep=json.loads((OUT/'prepared.json').read_text());old=json.loads((BASE/'prepared.json').read_text())
assert prep['parent_prepared_sha256']==sha(BASE/'prepared.json')
assert prep['source_sha256'].keys()==old['source_sha256'].keys()
changed=[]
for n,h in prep['source_sha256'].items():
 assert sha(OUT/'frozen'/n)==h,n
 if h!=old['source_sha256'][n]:changed.append(n)
assert sorted(changed)==sorted(prep['only_changed_files'])and len(changed)==4
expected={
 'src/fpga/common/gpu_core.v':P/'gpu-request-mux-v10/gpu_core.v',
 'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v':P/'cpu-fp-rs3-mux-v1/VexiiRiscv.v',
 'src/fpga/common/geometry_raw_load.v':P/'unified-load-components-v3/frozen/geometry_raw_load.v',
 'src/fpga/common/geometry_transform_light.v':P/'unified-load-components-v3/frozen/geometry_transform_light.v'}
assert set(changed)==set(expected)
for n,p in expected.items():assert prep['source_sha256'][n]==sha(p);bind(p)
for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n;bind(ROOT/n)
review=json.loads((P/'timing-mux-independent-review-v1/review.json').read_text());assert review['passed']and not review['deployment_ready']
for n,h in review['evidence_sha256'].items():assert sha(ROOT/n)==h,n
bind(P/'timing-mux-independent-review-v1/review.json')
manifest(P/'cpu-fp-rs3-mux-v1/manifest.json');manifest(P/'unified-load-components-v3/manifest.json');manifest(P/'lod5-live-native-v2/manifest.json')
gpu=manifest(P/'gpu-request-review-v10d/manifest.json');assert gpu['passed_for_map_only']and gpu['candidate_sha256']==prep['source_sha256']['src/fpga/common/gpu_core.v']
manifest(P/'gpu-request-mux-v10/divider-proof.json')
# If the separately owned GPU map has finished, bind its complete attribution.
gpu_maps=list(P.glob('gpu-request-map-v10*/manifest.json'))
for p in gpu_maps:manifest(p)
r=json.loads((OUT/'map-core/result.json').read_text());assert r['passed_map']and not r['full_fit_attempted']and r['prepared_sha256']==sha(OUT/'prepared.json')
for n in ['registers','memory_bits','dsp']:assert all(d[n]==0 for d in r['delta'].values())
report=OUT/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
if not report.exists():report=next((OUT/'map-core/job').rglob('ap_core.map.rpt'))
assert sha(report)==r['candidate_report_sha256'];bind(report)
inv=json.loads((OUT/'map-core/invocation.json').read_text());assert inv['seed']==2 and not inv['fitter_invoked']
qsf=next((OUT/'map-core/job').rglob('ap_core.qsf'));assert sha(qsf)==inv['qsf_sha256'];assert re.findall(r'^set_global_assignment -name SEED (\d+)$',qsf.read_text(),re.M)==['2'];bind(qsf)
assert prep['seed']==2 and not prep['seed_sweep'] and prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32 and prep['configured_mhz']==100
runner=ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_datapath_fit.py';inherit=ROOT/'tools/experiments/os30_fps25_20261005/phase10_compact_lod5_fit.py'
assert sha(runner)==prep['stage_recipe_sha256']and sha(inherit)==prep['recipe_sha256']
s=runner.read_text();assert "review['evidence_sha256'][n] == fit.sha(CPU_COMP / 'manifest.json')"in s
s=inherit.read_text();assert "review['passed_for_resource_fit'] and not review['deployment_ready']"in s and "'--seed-offsets', '0', '--jobs', '1'"in s
for p in [OUT/'prepared.json',BASE/'prepared.json',OUT/'integration.patch',OUT/'map-core/result.json',OUT/'map-core/invocation.json',OUT/'map-core/map.log',runner,inherit,Path(__file__)]:bind(p)
live=P/'lod5-live-compact-v3/manifest.json';live_status='pending, not required for resource fit'
if live.exists():j=manifest(live);assert j['passed'];live_status='passed; exact composed RTL plus full-RGB compiled client nominal stress'
result=dict(passed_for_resource_fit=True,deployment_ready=False,prepared_sha256=sha(OUT/'prepared.json'),only_changed_files=changed,
 scope='Authorize one saved-seed2 AREA resource/timing fit of exact reviewed compact-v3; no deployment or production changes.',
 findings=['Exactly four RTL files change; all platform/clock/QSF/SDC and all other sources are byte-identical to compact-v2.','CPU changes only binary-equivalent RS3 forwarding combinational data mux. Native lighting and directed RAW/WAW/FDIV/redirect tests are bound by final CPU manifest.','LOAD changes only rawSCAN consumers to equivalent existing synchronous position RAM output; native/random arithmetic, runtime equality assertion and unchanged wrapper cycles are bound.','GPU source is bound to exact12native+4boundary color/depth/fence/cycle replay and actualRTL divider arithmetic proof.','Combined map passes with125 fewer wholeALUT; allFF, logicalRAM andDSP counts unchanged. These are synthesis metrics, not fitted resource/timing guarantees.','Resource runner checks nativeCPU manifest, exact prepared/evidence hashes and independent gate before one seed2/inventory/offset0 fit. It has no publication/deployment action.'],
 live_integration_rebind=live_status,limits=['No wholegame actualCPU/completecore performance claim.','Wholecore placement and >90MHz timing remain unproven for this source until the selected fitter/STA completes.'],evidence_sha256=evidence)
with(OUT/'independent-review.json').open('x')as f:json.dump(result,f,indent=2);f.write('\n')
print(sha(OUT/'independent-review.json'))
