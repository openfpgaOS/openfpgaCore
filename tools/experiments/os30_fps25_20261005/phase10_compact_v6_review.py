#!/usr/bin/env python3
"""Independent private compact-v6 resource-fit gate; no fitter invocation."""
from pathlib import Path
import hashlib,json,shutil
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'compact-lod5-core-v6';B=P/'compact-lod5-core-v5'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest();evidence={}
def bind(p):
 evidence[str(p.relative_to(ROOT))]=sha(p)
 return json.loads(p.read_text()) if p.suffix=='.json' else p.read_text()
def verify(p):
 v=bind(p)
 if 'files' in v:
  for n,h in v['files'].items():assert sha(p.parent/n)==h,(p,n)
 elif v and all(isinstance(x,str) and len(x)==64 for x in v.values()):
  for n,h in v.items():assert sha(p.parent/n)==h,(p,n)
 for n,h in v.get('evidence_sha256',{}).items():assert sha(ROOT/n)==h,(p,n)
 return v
prep=bind(O/'prepared.json');base=bind(B/'prepared.json')
for n,h in prep['source_sha256'].items():assert sha(O/'frozen'/n)==h,n
for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n
changed=sorted(n for n,h in prep['source_sha256'].items() if h!=base['source_sha256'][n]);assert changed==sorted(prep['only_changed_files'])==['src/fpga/common/gpu_core.v','src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
assert set(prep['source_sha256'])==set(base['source_sha256'])
assert prep['seed']==2 and not prep['seed_sweep'] and prep['configured_mhz']==100 and prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32
proofs=['compact-lod5-core-v5/independent-review.json','compact-lod5-core-v5/final-manifest.json','cpu-ready-factor-v1/manifest.json','cpu-int-wb-factor-v1/manifest.json','cpu-fp-rs2-mux-v1/manifest.json','cpu-final-paths-v1/manifest.json','cpu-final-independent-review-v1/manifest.json','cpu-os30-repro-v4/manifest.json','cpu-os30-regenerate-v4/manifest.json','normal-build-integration-v3/manifest.json','gpu-clamp-review-v12/manifest.json','gpu-attribute-review-v13/manifest.json','cache-write-lod5-coupled-v2/manifest.json','load-submit-coupled-v1/manifest.json']
for n in proofs:verify(P/n)
for n in ['cpu-final-paths-v1/native/result.json','cpu-final-paths-v1/native/extra/result.json','cpu-os30-repro-v4/checks/result.json','cpu-os30-regenerate-v4/result.json','normal-build-integration-v3/result.json','gpu-attribute-review-v13/result.json','gpu-attribute-review-v13/paths-result.json','gpu-attribute-review-v13/attribute-result.json','cache-write-lod5-coupled-v2/result.json','load-submit-coupled-v1/result.json']:
 assert bind(P/n)['passed'],n
cpu='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';gpu='src/fpga/common/gpu_core.v'
assert prep['source_sha256'][cpu]==sha(P/'cpu-final-paths-v1/VexiiRiscv.v')=='022f46fbbb0834bba1499df65ecdda79ecf13447fb33eff85effcb3bbda1e73d'
assert prep['source_sha256'][gpu]==sha(P/'gpu-attribute-v13/gpu_core.v')=='61f4776cada85fb3d209204b82444027054f18ced59eb46e970458c783110888'
assert base['source_sha256'][gpu]==json.loads((P/'gpu-attribute-review-v13/manifest.json').read_text())['baseline_sha256']
assert json.loads((P/'cpu-os30-regenerate-v4/result.json').read_text())['output_sha256']==prep['source_sha256'][cpu]
mapr=bind(O/'map-core/result.json');inv=bind(O/'map-core/invocation.json');assert mapr['passed_map'] and not mapr['full_fit_attempted'] and not inv['fitter_invoked'] and inv['seed']==2
assert mapr['prepared_sha256']==sha(O/'prepared.json')==inv['prepared_sha256']
qsf=O/'map-core/job/src/fpga/targets/pocket/ap_core.qsf';assert sha(qsf)==inv['qsf_sha256'];bind(qsf)
rpt=O/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt';assert sha(rpt)==mapr['candidate_report_sha256'];bind(rpt)
# The normal-build closure is independent of map statistics and compares the
# recursive source, SDC/QIP/MIF, macro and physical setting contents.
normal=bind(P/'normal-build-integration-v3/checks/selected-binding.json');assert normal['mapped_qsf_sha256']==sha(qsf) and normal['selected_prepared_sha256']==sha(O/'prepared.json')
assert (O/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()==(B/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()
shutil.copy2(__file__,O/'independent-review-recipe.py');bind(O/'independent-review-recipe.py');bind(O/'stage-recipe.py');bind(O/'integration.patch')
review=dict(passed_for_resource_fit=True,component_and_coupled_proofs_complete=True,deployment_ready=False,reviewer='displaylist_compilation',candidate_prepared_sha256=sha(O/'prepared.json'),only_changed_files=changed,seed=2,seed_sweep=False,physical_options_unchanged=True,configured_clock_mhz=100,required_routed_mhz_greater_than=90,cpu_instruction_cache_kib=32,cpu_data_cache_kib=128,map_summary=mapr['delta'],findings=[
'Final CPU is exact composition of qualified ready factor, RS2 data priority grouping and integer formatted-DATA qualification removal. Registers/ports/cache/FPU/ISA settings and architectural write/issue/control remain unchanged except the equivalent ready expression. Ten final native pairs preserve outputs and exact cycles with all live reference checks enabled.',
'Actual offline Scala regeneration plus the entire checked helper chain reproduces final CPU022f46f exactly; proposal input/output hashes, os30-only gates, topology checks and atomic publication protect against unqualified generator changes.',
'GPUv13 threshold and attribute decode updates retain owned arithmetic values, command outputs and cycles. Literal clamp proof plus16 actual pixel/fence/cycle pairs bind the exact v11 parent used by compact-v5. All ten owned attributes and initialization/advance ownership are observed; invalid/unowned10..15 and liveQ29 disabled coverage limits remain explicit.',
'All other compact-v5 source files are byte-identical, including LOAD arithmetic/protocol, full-strobe render-cache shortcut, SDRAM address decode, top wiring, PLLs and constraints. Parent reviewed component/coupled bridges remain applicable.',
'Normal and frozen build generators resolve exact compact-v6 source/define/setting/constraint/init-image closure through the same six os30 source overrides. Shared/master sources and os20/os25 are unchanged. Negative maps fail atomically; existing CPU freshness/publication and variant-timing checks pass.',
'Map passes: whole top+15ALUT/+10registers, RAM/DSP unchanged; CPU-42ALUT/+1register, GPU+71ALUT/+10registers. Map counts and optimizer-local changes do not establish fitted ALMs/LABs or physical frequency.',
'Selected recycler/cache-v2 actual warm/cold coupled records remain exact; optional software submission overlap also preserves all16 actual buffers/histories and five functional modes. Timing is modeled CPU plus actual GPU/LOAD/memory RTL and separately proven zero-cycle component bridges, not current native whole-game CPU or physical Pocket FPS.'
],limits=[
'Authorize only the parent-requested single saved-seed2 AREA resource/timing fit, with originalCPU SPEED override and all inherited physical options. This reviewer did not invoke a fitter.',
'The >90MHz requirement remains unproven until routed setup/hold/timing reports pass; no25FPS or deployment claim follows from this gate.',
'Native and raster corpora are bounded. Binary combinational/math proofs cover the explicitly stated all-input portions; no four-state X equivalence or all-scenes native rendering is claimed.',
'No production promotion, sibling modification or SD write is authorized by this review.'
],evidence_sha256=evidence)
with (O/'independent-review.json').open('x') as f:json.dump(review,f,indent=2);f.write('\n')
print(sha(O/'independent-review.json'))
