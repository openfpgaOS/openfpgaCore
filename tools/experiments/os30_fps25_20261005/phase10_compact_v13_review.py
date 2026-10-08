#!/usr/bin/env python3
"""Independent compact-v13 composition/normal-build gate; no fitter."""
import json,shutil
from pathlib import Path
from phase10_compact_v7_review import ROOT,P,sha,bind,verify,evidence
O=P/'compact-lod5-core-v13'
B=P/'compact-lod5-core-v12'
N=P/'normal-build-integration-v10'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
GPU='src/fpga/common/gpu_core.v'

def main():
    prep=bind(O/'prepared.json'); base=bind(B/'prepared.json')
    for n,h in prep['source_sha256'].items(): assert sha(O/'frozen'/n)==h,n
    for n,h in prep['evidence_sha256'].items(): assert sha(ROOT/n)==h,n
    assert set(prep['source_sha256'])==set(base['source_sha256'])
    selected={CPU:('cpu-fetch-hazard-v11/VexiiRiscv.v','6c02b722fe4a66f91ad88408e035dbadec92686e18000559cf13a93b5f541b54'),
      GPU:('gpu-span-control-v17/gpu_core.v','7076c8e7c9d41361aa01bda2612089dcff924cc38885d958e2956a39ca0037a2')}
    changed=sorted(n for n,h in prep['source_sha256'].items() if h!=base['source_sha256'][n])
    assert changed==sorted(prep['only_changed_files'])==sorted(selected)
    for n,(path,h) in selected.items(): assert prep['source_sha256'][n]==sha(P/path)==h,n
    assert prep['parent_prepared_sha256']==sha(B/'prepared.json')
    assert prep['seed']==2 and not prep['seed_sweep'] and prep['configured_mhz']==100
    assert prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32
    proofs=['compact-lod5-core-v12/independent-review.json','compact-lod5-core-v12/final-manifest.json',
      'compact-timing-audit-v12/manifest.json','cpu-fetch-hazard-v11/manifest.json',
      'cpu-fetch-hazard-review-v11/manifest.json','cpu-os30-repro-v9/manifest.json',
      'cpu-os30-regenerate-v9/manifest.json','gpu-span-control-v17/manifest.json',
      'gpu-span-control-review-v17/manifest.json','gpu-span-control-source-review-v17/manifest.json',
      'normal-build-integration-v10/manifest.json','cache-write-lod5-coupled-v2/manifest.json',
      'load-submit-coupled-v1/manifest.json','load-submit-delivery-v1/manifest.json',
      'load-submit-delivery-review-v1/manifest.json']
    for name in proofs: verify(P/name)
    for name in ['cpu-fetch-hazard-v11/result.json','cpu-fetch-hazard-review-v11/review.json',
      'cpu-os30-repro-v9/checks/result.json','cpu-os30-regenerate-v9/result.json',
      'gpu-span-control-v17/result.json','gpu-span-control-source-review-v17/review.json',
      'normal-build-integration-v10/result.json','cache-write-lod5-coupled-v2/result.json',
      'load-submit-coupled-v1/result.json']:
        assert bind(P/name)['passed'],name
    assert bind(P/'cpu-os30-regenerate-v9/result.json')['output_sha256']==prep['source_sha256'][CPU]
    old=bind(Path(__file__).with_name('phase10_normal_build_stage_v9.py'))
    wanted=old
    for a,b in [('compact-lod5-core-v12','compact-lod5-core-v13'),
      ('cpu-os30-repro-v8','cpu-os30-repro-v9'),('normal-build-integration-v9','normal-build-integration-v10'),
      ('compact-v12','compact-v13'),('compact v12','compact v13'),('cpu-os30-regenerate-v8','cpu-os30-regenerate-v9')]:
        assert a in wanted,a
        wanted=wanted.replace(a,b)
    assert wanted==bind(Path(__file__).with_name('phase10_normal_build_stage_v10.py'))
    normalprep=bind(N/'prepared.json')
    assert len(normalprep['overrides'])==8 and len(normalprep['inventory'])==21
    assert len(normalprep['proposed_sha256'])==31
    for name in ('audio_mixer.v','gpu_tex_cache.v'):
        assert normalprep['overrides']['../../common/'+name]=='../../common/os30/'+name
        assert normalprep['proposed_sha256']['src/fpga/common/os30/'+name]==prep['source_sha256']['src/fpga/common/'+name]
    mapr=bind(O/'map-core/result.json'); invocation=bind(O/'map-core/invocation.json')
    assert mapr['passed_map'] and not mapr['full_fit_attempted']
    assert not invocation['fitter_invoked'] and invocation['seed']==2
    assert mapr['prepared_sha256']==sha(O/'prepared.json')==invocation['prepared_sha256']
    qsf=O/'map-core/job/src/fpga/targets/pocket/ap_core.qsf'
    assert sha(qsf)==invocation['qsf_sha256'];bind(qsf)
    rpt=O/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
    assert sha(rpt)==mapr['candidate_report_sha256'];bind(rpt)
    normal=bind(N/'checks/selected-binding.json')
    assert normal['mapped_qsf_sha256']==sha(qsf) and normal['selected_prepared_sha256']==sha(O/'prepared.json')
    assert (O/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()==(B/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()
    generic=Path(__file__).with_name('phase10_compact_lod5_fit.py');bind(generic)
    assert "review['prepared_sha256'] == sha(OUT / 'prepared.json')" in generic.read_text()
    assert prep['recipe_sha256']==sha(generic)
    for name in ('phase10_compact_v7_review.py','phase10_compact_span_control_fit.py'):
        bind(Path(__file__).with_name(name))
    shutil.copy2(__file__,O/'independent-review-recipe.py')
    for name in ('independent-review-recipe.py','stage-recipe.py','integration.patch'):bind(O/name)
    evidence.update(prep['evidence_sha256'])
    review=dict(passed_for_resource_fit=True,component_and_coupled_proofs_complete=True,
      deployment_ready=False,reviewer='displaylist_compilation',prepared_sha256=sha(O/'prepared.json'),
      only_changed_files=changed,seed=2,seed_sweep=False,physical_options_unchanged=True,
      configured_clock_mhz=100,required_routed_mhz_greater_than=90,cpu_instruction_cache_kib=32,
      cpu_data_cache_kib=128,normal_build_outputs=31,os30_source_overrides=8,map_summary=mapr['delta'],
      findings=[
        'Only the final CPU fetch-ready Boolean expression and GPU count-mask/terminal-step control differ from compact-v12. All other source/settings bytes match the parent.',
        'CPU 6c02b722 factors the late RS hazard at the final fetch-ready output. The exact Boolean identity leaves original dispatch/reservation/fire/decode and every register unchanged. Exhaustive32768 controls and ten direct482e native UART/cycle pairs pass, with14,519,017 live checks; independent source review and exact actual Scala reproduction are bound.',
        'GPU7076c8e7 predecodes nonzero into its existing four count-valid bits at all ten RAM write sites. Four clears and the SELECT snapshot are unchanged. The direct terminal predicate equals next_calc==NONE for every encoded step. Full literal/transition proof, actual native/boundary replay and an independent source review are required and bound; no new architectural register, state or cycle is introduced.',
        'Normal-build-v10 differs mechanically from v9 only in selected candidate/reproduction labels. Both normal and frozen generators resolve the exact mapped source/QIP/SDC/MIF/define/settings closure with31 proposed files and eight os30-only overrides. Common/master files and os20/os25 remain unchanged.',
        'The selected bbb1c31a load-submit-overlap client and its original actual coupled performance evidence are unchanged. Component zero-cycle bridges preserve their bounded scope; this is not a new native whole-game or Pocket FPS result.',
        'Map passes: top+40ALUT and0register/RAM/DSP; CPU+33ALUT and0register; GPU-11ALUT and+1mapped register. Architectural register/state count is unchanged; synthesis hierarchy counts may shift. No fitted ALM/LAB or Fmax claim follows from this map.'
      ],limits=[
        'Only the parent-requested saved seed2 AREA fit with unchanged CPU SPEED override and physical options is gated. This reviewer invokes no fitter.',
        'Both slow-corner frequency and hold requirements remain separate gates; configured100MHz closure,25FPS, promotion and deployment are not established.',
        'Functional evidence is bounded. Exhaustive claims apply to the binary expressions/state invariant, not universal four-state or whole-system equivalence.',
        'No production, sibling or SD change is authorized by this review.'
      ],evidence_sha256=evidence)
    with (O/'independent-review.json').open('x') as f:json.dump(review,f,indent=2);f.write('\n')
    print(sha(O/'independent-review.json'))
if __name__=='__main__':main()
