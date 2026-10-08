#!/usr/bin/env python3
"""Independent single-seed compact-v7 fit gate. Never invokes the fitter."""
import hashlib, json, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'compact-lod5-core-v7';B=P/'compact-lod5-core-v6';evidence={}
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def bind(p):
 evidence[str(p.relative_to(ROOT))]=sha(p)
 return json.loads(p.read_text())if p.suffix=='.json'else p.read_text()
def verify(p):
 v=bind(p)
 if 'files'in v:
  for n,h in v['files'].items():assert sha(p.parent/n)==h,(p,n)
 elif v and all(isinstance(x,str)and len(x)==64 for x in v.values()):
  for n,h in v.items():assert sha(p.parent/n)==h,(p,n)
 for n,h in v.get('evidence_sha256',{}).items():assert sha(ROOT/n)==h,(p,n)
 return v
def main():
 prep=bind(O/'prepared.json');base=bind(B/'prepared.json')
 for n,h in prep['source_sha256'].items():assert sha(O/'frozen'/n)==h,n
 for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 assert set(prep['source_sha256'])==set(base['source_sha256'])
 cpu='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';gpu='src/fpga/common/gpu_core.v';cache='src/fpga/common/gpu_color_depth_cache.sv'
 selected={cpu:('cpu-rvc-plane-v7/VexiiRiscv.v','8fca28712f731dab4b8c81c3e57ddf2529c6bf4222c292f120e4108c758689f1'),gpu:('gpu-origin-default-v14/gpu_core.v','3c171d36bce942735040d3cdc2d96a5f9959b46b2b5c4896455d1c4763ffa4d1'),cache:('cache-owned-lookup-v3/gpu_color_depth_cache.sv','2f68b6571a5fbaa88ec016d571d84f0403acaf0b000efab70daa9b4ed2f12aea')}
 changed=sorted(n for n,h in prep['source_sha256'].items()if h!=base['source_sha256'][n])
 assert changed==sorted(prep['only_changed_files'])==sorted(selected)
 for n,(path,h)in selected.items():assert prep['source_sha256'][n]==sha(P/path)==h,n
 assert prep['parent_prepared_sha256']==sha(B/'prepared.json')
 assert prep['seed']==2 and not prep['seed_sweep'] and prep['configured_mhz']==100 and prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32
 for n in ('compact-lod5-core-v6/independent-review.json','compact-lod5-core-v6/final-manifest.json','compact-timing-audit-v6/manifest.json','cpu-rvc-plane-v7/manifest.json','cpu-os30-repro-v5/manifest.json','cpu-os30-regenerate-v5/manifest.json','normal-build-integration-v4/manifest.json','gpu-origin-review-v14/manifest.json','cache-owned-lookup-review-v3/manifest.json','cache-write-lod5-coupled-v2/manifest.json','load-submit-coupled-v1/manifest.json'):verify(P/n)
 for n in ('cpu-rvc-plane-v7/result.json','cpu-os30-repro-v5/checks/result.json','cpu-os30-regenerate-v5/result.json','normal-build-integration-v4/result.json','gpu-origin-review-v14/result.json','cache-owned-lookup-review-v3/review.json','cache-owned-lookup-v3/result.json','cache-owned-lookup-v3/protocol/result.json','cache-write-lod5-coupled-v2/result.json','load-submit-coupled-v1/result.json'):assert bind(P/n)['passed'],n
 assert json.loads((P/'cpu-os30-regenerate-v5/result.json').read_text())['output_sha256']==prep['source_sha256'][cpu]
 # Parent bridges are exact per-component, not an assertion of unmeasured
 # whole-native-game behavior. Nonchanged source closure is byte-identical.
 assert sha(P/'cpu-final-paths-v1/VexiiRiscv.v')==base['source_sha256'][cpu]
 assert sha(P/'gpu-attribute-v13/gpu_core.v')==base['source_sha256'][gpu]
 assert sha(P/'cache-owned-lookup-v3/baseline.sv')==base['source_sha256'][cache]
 mapr=bind(O/'map-core/result.json');inv=bind(O/'map-core/invocation.json')
 assert mapr['passed_map']and not mapr['full_fit_attempted']and not inv['fitter_invoked']and inv['seed']==2
 assert mapr['prepared_sha256']==sha(O/'prepared.json')==inv['prepared_sha256']
 qsf=O/'map-core/job/src/fpga/targets/pocket/ap_core.qsf';assert sha(qsf)==inv['qsf_sha256'];bind(qsf)
 rpt=O/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt';assert sha(rpt)==mapr['candidate_report_sha256'];bind(rpt)
 normal=bind(P/'normal-build-integration-v4/checks/selected-binding.json')
 assert normal['mapped_qsf_sha256']==sha(qsf)and normal['selected_prepared_sha256']==sha(O/'prepared.json')
 assert(O/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()==(B/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()
 # Bind the exact direct invocation route and the generic driver's required
 # prepared_sha256 schema; no compatibility adapter or fitter is executed.
 for n in ('phase10_compact_remaining_v7_fit.py','phase10_compact_datapath_fit.py','phase10_compact_lod5_fit.py'):bind(Path(__file__).with_name(n))
 driver=Path(__file__).with_name('phase10_compact_lod5_fit.py').read_text()
 assert "review['prepared_sha256'] == sha(OUT / 'prepared.json')"in driver
 assert prep['recipe_sha256']==sha(Path(__file__).with_name('phase10_compact_lod5_fit.py'))
 shutil.copy2(__file__,O/'independent-review-recipe.py');bind(O/'independent-review-recipe.py');bind(O/'stage-recipe.py');bind(O/'integration.patch')
 review=dict(passed_for_resource_fit=True,component_and_coupled_proofs_complete=True,deployment_ready=False,reviewer='displaylist_compilation',prepared_sha256=sha(O/'prepared.json'),only_changed_files=changed,seed=2,seed_sweep=False,physical_options_unchanged=True,configured_clock_mhz=100,required_routed_mhz_greater_than=90,cpu_instruction_cache_kib=32,cpu_data_cache_kib=128,map_summary=mapr['delta'],findings=[
 'CPU changes only combinational compressed-decoder output bit16/18 planes. The full32 instruction is compared across65536 lower encodings and19 upper patterns, including illegal compressed values and normal32-bit bypass. Ten native pairs preserve outputs and cycles with live decoder/ready/RS2/RS3/integer-data checks. No pipeline/register/cache/ISA/configuration change.',
 'Checked os30-only generator helper and actual private offline Scala regeneration reproduce exact CPU8fca2871. Source topology, input/output hashes, unchanged other variants, freshness and atomic publication gates remain enabled.',
 'GPU changes only a default unowned shared-DSP A operand under selected dedicated-span macros. B remains zero in that case; every nonzero-B writer explicitly supplies unchanged A on the same edge. All functional signed64 products are exact for binary inputs. Twelve native and four boundary pairs preserve color/depth/fence bytes and all cycle counts, with30829320 live product comparisons. No register/state/cycle added.',
 'Cache R_ACCEPT replaces hot_slot with its literal owned-address expression. R_ACCEPT is not IDLE, so lookup_addr equals addr and both expressions have identical widths and every-bit semantics. Eighteen selected-parameter component runs pass data scoreboards, original-expression assertions, hostile ownership/reset cases and old/new cycle/transaction counts.',
 'The remaining compact-v6 source closure is byte-identical, including LOAD math/protocol, top, SDRAM, clocks, cache capacities, constraints and physical options. Parent coupled evidence and explicit zero-cycle component bridges remain applicable; this is not a new whole-native-game measurement.',
 'Normal-build-v4 and frozen-project generators reproduce exact compact-v7 source/QIP/SDC/MIF/define/settings closure via six os30-only overrides. Common/master sources and os20/os25 closures stay unchanged. Negative map, freshness and atomic publication checks pass.',
 'Map passes: top-5ALUT, CPU+24ALUT, GPU-42ALUT; top registers/memory/DSP unchanged. Other local optimizer shifts occur without source changes. These synthesis counts do not establish fitted ALMs/LABs or routed frequency.',
 'The selected software remains load-submit-overlap-v1. The separately qualified wire-counter cleanup is slower and unselected; it is not included in this hardware gate.'
 ],limits=[
 'Authorize only the requested single saved-seed2 AREA resource/timing fit through phase10_compact_remaining_v7_fit.py fit, retaining CPU SPEED override and all physical options. This reviewer invoked no fitter.',
 'Routed setup/hold/clock requirements remain a separate gate; no greater-than90MHz,25FPS or deployment claim is made here.',
 'Native and raster corpora are bounded. Binary all-input claims apply only to the proven expressions; GPU unknown-value propagation is not claimed equivalent.',
 'No production promotion, sibling modification or SD write is authorized.'
 ],evidence_sha256=evidence)
 with(O/'independent-review.json').open('x')as f:json.dump(review,f,indent=2);f.write('\n')
 print(sha(O/'independent-review.json'))
if __name__=='__main__':main()
