#!/usr/bin/env python3
"""Independent compact-v10 geometry write-data composition/fit gate; never invoke fitter."""
import json,shutil
from pathlib import Path
from phase10_compact_v7_review import ROOT,P,sha,bind,verify,evidence
O=P/'compact-lod5-core-v10';B=P/'compact-lod5-core-v9'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
RAW='src/fpga/common/geometry_raw_load.v'
def main():
 prep=bind(O/'prepared.json');base=bind(B/'prepared.json')
 for n,h in prep['source_sha256'].items():assert sha(O/'frozen'/n)==h,n
 for n,h in prep['evidence_sha256'].items():assert sha(ROOT/n)==h,n
 assert set(prep['source_sha256'])==set(base['source_sha256'])
 changed=sorted(n for n,h in prep['source_sha256'].items()if h!=base['source_sha256'][n])
 assert changed==sorted(prep['only_changed_files'])==[RAW]
 assert prep['source_sha256'][CPU]==sha(P/'cpu-aligner-enable-v9/VexiiRiscv.v')=='b511dba179677c9f2b3fe433caeb0c5399a4fe1dc688e5d85587f6888d9cbeab'
 assert base['source_sha256'][CPU]==prep['source_sha256'][CPU]
 assert prep['source_sha256'][RAW]==sha(P/'geometry-clip-write-v1/geometry_raw_load.v')=='19b54ebca9c671612b2b8a6e1000b5c20ab5416ae94f574023fadb485113e6f6'
 assert prep['parent_prepared_sha256']==sha(B/'prepared.json')
 assert prep['seed']==2 and not prep['seed_sweep']and prep['configured_mhz']==100 and prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32
 proofs=['compact-lod5-core-v9/independent-review.json','compact-lod5-core-v9/final-manifest.json','compact-timing-audit-v9/manifest.json','geometry-clip-write-v1/manifest.json','geometry-clip-write-review-v1/manifest.json','cpu-aligner-enable-v9/manifest.json','cpu-aligner-enable-review-v9/manifest.json','cpu-os30-repro-v7/manifest.json','cpu-os30-regenerate-v7/manifest.json','normal-build-integration-v7/manifest.json','cache-write-lod5-coupled-v2/manifest.json','load-submit-coupled-v1/manifest.json','load-submit-delivery-v1/manifest.json','load-submit-delivery-review-v1/manifest.json']
 for n in proofs:verify(P/n)
 for n in ['geometry-clip-write-v1/result.json','geometry-clip-write-review-v1/review.json','cpu-aligner-enable-v9/proof/result.json','cpu-aligner-enable-v9/native/result.json','cpu-aligner-enable-v9/native/extra/result.json','cpu-aligner-enable-v9/result.json','cpu-aligner-enable-review-v9/review.json','cpu-os30-repro-v7/checks/result.json','cpu-os30-regenerate-v7/result.json','normal-build-integration-v7/result.json','cache-write-lod5-coupled-v2/result.json','load-submit-coupled-v1/result.json']:assert bind(P/n)['passed'],n
 assert json.loads((P/'cpu-os30-regenerate-v7/result.json').read_text())['output_sha256']==prep['source_sha256'][CPU]
 mapr=bind(O/'map-core/result.json');inv=bind(O/'map-core/invocation.json')
 assert mapr['passed_map']and not mapr['full_fit_attempted']and not inv['fitter_invoked']and inv['seed']==2
 assert mapr['prepared_sha256']==sha(O/'prepared.json')==inv['prepared_sha256']
 qsf=O/'map-core/job/src/fpga/targets/pocket/ap_core.qsf';assert sha(qsf)==inv['qsf_sha256'];bind(qsf)
 rpt=O/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt';assert sha(rpt)==mapr['candidate_report_sha256'];bind(rpt)
 normal=bind(P/'normal-build-integration-v7/checks/selected-binding.json')
 assert normal['mapped_qsf_sha256']==sha(qsf)and normal['selected_prepared_sha256']==sha(O/'prepared.json')
 assert(O/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()==(B/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()
 # Generic driver schema and source recipe remain exact; no invocation or
 # monkey-patched fitter is needed by this review.
 generic=Path(__file__).with_name('phase10_compact_lod5_fit.py');bind(generic)
 assert "review['prepared_sha256'] == sha(OUT / 'prepared.json')"in generic.read_text()
 assert prep['recipe_sha256']==sha(generic)
 bind(Path(__file__).with_name('phase10_compact_v7_review.py'))
 shutil.copy2(__file__,O/'independent-review-recipe.py');bind(O/'independent-review-recipe.py');bind(O/'stage-recipe.py');bind(O/'integration.patch')
 # Every bound stage helper must remain present; its original name is already
 # captured in prepared.evidence_sha256, independent of this review's label.
 evidence.update(prep['evidence_sha256'])
 review=dict(passed_for_resource_fit=True,component_and_coupled_proofs_complete=True,deployment_ready=False,reviewer='displaylist_compilation',prepared_sha256=sha(O/'prepared.json'),only_changed_files=changed,seed=2,seed_sweep=False,physical_options_unchanged=True,configured_clock_mhz=100,required_routed_mhz_greater_than=90,cpu_instruction_cache_kib=32,cpu_data_cache_kib=128,map_summary=mapr['delta'],findings=[
 'Only geometry_raw_load.v changes from compact-v9. The default attribute-RAM write data becomes the existing clipping classification. Higher-priority UV, RGBA and computed RGB sources, SCAN write enable and write address remain unchanged.',
 'Unowned attribute data may differ, but every accepted write retains exact address, byte enables and data. No float comparison, arithmetic, pipeline state, register or cycle is added; numerical and ownership component proofs are bound separately.',
 'CPU b511dba, all GPU/cache/SDRAM/CRAM/top/clock sources and source settings remain byte-identical to compact-v9. Existing CPU native, checked generator and actual Scala reproduction remain selected unchanged.',
 'Normal-build-v7 and frozen generators select the exact mapped source/QIP/SDC/MIF/define/settings closure via the same six os30-only overrides. Common/master inputs and os20/os25 remain unchanged; rejection/freshness/publication tests pass.',
 'Existing actual GPU/LOAD/memory coupled performance proofs and explicitly qualified cycle-equivalent component bridges remain bound. Selected software is exact load-submit-overlap-v1 bbb1c31a; delivery review and normal rebuild match it. No new whole-native-game or physical FPS result is implied.',
 'Map passed; exact local and top deltas are recorded in map_summary. Synthesis counts are not fitted ALMs/LABs or routed frequency. Only a routed comparison can establish the effect on the remaining cold-corner geometry write path.'

 ],limits=[
 'Authorize only the parent-requested single saved seed 2 AREA fit with retained CPU SPEED override and inherited physical options. This reviewer invoked no fitter.',
 'Strict greater than 90 MHz across corners and configured 100 MHz setup remain separate measured gates. No clock closure, 25 FPS or deployment claim is made.',
 'Proofs have stated binary-input/topology and bounded native/raster scope; no universal four-state or whole-game native equivalence claim.',
 'No production promotion, sibling modification or SD write is authorized.'
 ],evidence_sha256=evidence)
 with(O/'independent-review.json').open('x')as f:json.dump(review,f,indent=2);f.write('\n')
 print(sha(O/'independent-review.json'))
if __name__=='__main__':main()
