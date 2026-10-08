#!/usr/bin/env python3
"""Compact-v12 composition/normal-build fit gate; never invoke a fitter."""
import json
import shutil
from pathlib import Path
from phase10_compact_v7_review import ROOT,P,sha,bind,verify,evidence

O=P/'compact-lod5-core-v12'
B=P/'compact-lod5-core-v11'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
TEX='src/fpga/common/gpu_tex_cache.v'
AUDIO='src/fpga/common/audio_mixer.v'
N=P/'normal-build-integration-v9'

def main():
    prep=bind(O/'prepared.json'); base=bind(B/'prepared.json')
    for n,h in prep['source_sha256'].items(): assert sha(O/'frozen'/n)==h,n
    for n,h in prep['evidence_sha256'].items(): assert sha(ROOT/n)==h,n
    assert set(prep['source_sha256'])==set(base['source_sha256'])
    selected={
      CPU:('cpu-writeback-plane-v10/VexiiRiscv.v','482e13475702808f40d5bad7f6b6e6af9331ed5c895d99d7bf4630a3b047a303'),
      TEX:('texture-read-address-v1/gpu_tex_cache.v','c3fdc0adfe5ed583c37bf09ad3dbbbecb1e3c6ae1f5714cab5064eacc328e51c'),
      AUDIO:('audio-accum-carry-v1/audio_mixer.v','ef06e913079a02276d5260b26bd1d323b4b192233b1bba7b67836b6c75d9330e')}
    changed=sorted(n for n,h in prep['source_sha256'].items() if h!=base['source_sha256'][n])
    assert changed==sorted(prep['only_changed_files'])==sorted(selected)
    for n,(path,h) in selected.items(): assert prep['source_sha256'][n]==sha(P/path)==h,n
    assert prep['parent_prepared_sha256']==sha(B/'prepared.json')
    assert prep['seed']==2 and not prep['seed_sweep'] and prep['configured_mhz']==100
    assert prep['cpu_cache_kib']==128 and prep['instruction_cache_kib']==32
    proofs=[
      'compact-lod5-core-v11/independent-review.json','compact-lod5-core-v11/final-manifest.json',
      'compact-timing-audit-v11/manifest.json','cpu-writeback-plane-v10/manifest.json',
      'cpu-writeback-plane-review-v10/manifest.json','cpu-os30-repro-v8/manifest.json',
      'cpu-os30-regenerate-v8/manifest.json','texture-read-address-v1/manifest.json',
      'texture-read-review-v1/manifest.json','texture-read-address-source-review-v1/manifest.json',
      'audio-accum-carry-v1/manifest.json','audio-accum-carry-review-v1/manifest.json',
      'normal-build-integration-v9/manifest.json','cache-write-lod5-coupled-v2/manifest.json',
      'load-submit-coupled-v1/manifest.json','load-submit-delivery-v1/manifest.json',
      'load-submit-delivery-review-v1/manifest.json']
    for name in proofs: verify(P/name)
    for name in [
      'cpu-writeback-plane-v10/result.json','cpu-writeback-plane-review-v10/review.json',
      'cpu-os30-repro-v8/checks/result.json','cpu-os30-regenerate-v8/result.json',
      'texture-read-address-v1/result.json','texture-read-address-source-review-v1/review.json',
      'audio-accum-carry-v1/result.json','audio-accum-carry-review-v1/review.json',
      'normal-build-integration-v9/result.json','cache-write-lod5-coupled-v2/result.json',
      'load-submit-coupled-v1/result.json']:
        assert bind(P/name)['passed'],name
    assert bind(P/'cpu-os30-regenerate-v8/result.json')['output_sha256']==prep['source_sha256'][CPU]
    # Normal integration changes only the selected labels/counts over the
    # qualified v8 helper. Both new shared files need os30-only shadows.
    old=bind(Path(__file__).with_name('phase10_normal_build_stage_v8.py'))
    wanted=old
    for a,b in [('compact-lod5-core-v11','compact-lod5-core-v12'),
                ('cpu-os30-repro-v7','cpu-os30-repro-v8'),
                ('normal-build-integration-v8','normal-build-integration-v9'),
                ('len(changed) == 19','len(changed) == 21'),
                ('len(rtl) == 18','len(rtl) == 20'),
                ('len(overrides) == 6','len(overrides) == 8'),
                ('compact-v11','compact-v12'),('compact v11','compact v12'),
                ('cpu-os30-regenerate-v7','cpu-os30-regenerate-v8'),
                ('19 differences: 18 RTL','21 differences: 20 RTL'),
                ('The six changed shared/top','The eight changed shared/top')]:
        assert a in wanted,a
        wanted=wanted.replace(a,b)
    assert wanted==bind(Path(__file__).with_name('phase10_normal_build_stage_v9.py'))
    normalprep=bind(N/'prepared.json')
    assert len(normalprep['overrides'])==8 and len(normalprep['inventory'])==21
    assert len(normalprep['proposed_sha256'])==31
    for name in ('audio_mixer.v','gpu_tex_cache.v'):
        original='../../common/'+name
        assert normalprep['overrides'][original]=='../../common/os30/'+name
        assert normalprep['proposed_sha256']['src/fpga/common/os30/'+name]==prep['source_sha256']['src/fpga/common/'+name]
    # Selected QSF/SDC, all memory sizes and the physical options remain exact.
    mapr=bind(O/'map-core/result.json'); invocation=bind(O/'map-core/invocation.json')
    assert mapr['passed_map'] and not mapr['full_fit_attempted']
    assert not invocation['fitter_invoked'] and invocation['seed']==2
    assert mapr['prepared_sha256']==sha(O/'prepared.json')==invocation['prepared_sha256']
    qsf=O/'map-core/job/src/fpga/targets/pocket/ap_core.qsf'
    assert sha(qsf)==invocation['qsf_sha256']; bind(qsf)
    rpt=O/'map-core/job/src/fpga/targets/pocket/output_files/ap_core.map.rpt'
    assert sha(rpt)==mapr['candidate_report_sha256']; bind(rpt)
    normal=bind(N/'checks/selected-binding.json')
    assert normal['mapped_qsf_sha256']==sha(qsf) and normal['selected_prepared_sha256']==sha(O/'prepared.json')
    assert (O/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()==(B/'frozen/src/fpga/targets/pocket/ap_core.qsf').read_bytes()
    generic=Path(__file__).with_name('phase10_compact_lod5_fit.py'); bind(generic)
    assert "review['prepared_sha256'] == sha(OUT / 'prepared.json')" in generic.read_text()
    assert prep['recipe_sha256']==sha(generic)
    for name in ('phase10_compact_v7_review.py','phase10_compact_remaining_paths_fit.py','phase10_compact_datapath_fit.py'):
        bind(Path(__file__).with_name(name))
    shutil.copy2(__file__,O/'independent-review-recipe.py')
    for name in ('independent-review-recipe.py','stage-recipe.py','integration.patch'): bind(O/name)
    evidence.update(prep['evidence_sha256'])
    review=dict(passed_for_resource_fit=True,component_and_coupled_proofs_complete=True,
      deployment_ready=False,reviewer='displaylist_compilation',prepared_sha256=sha(O/'prepared.json'),
      only_changed_files=changed,seed=2,seed_sweep=False,physical_options_unchanged=True,
      configured_clock_mhz=100,required_routed_mhz_greater_than=90,
      cpu_instruction_cache_kib=32,cpu_data_cache_kib=128,normal_build_outputs=31,
      os30_source_overrides=8,map_summary=mapr['delta'],findings=[
        'Only CPU integer writeback DATA, texture port-A read-address selects and two audio accumulator expressions differ from compact-v11. The complete remaining source/settings closure is byte-identical.',
        'CPU 482e1347 uses prequalified format masks for the late DATA AND/OR plane. Raw source masks, architectural validity/commit/cancel, issue, caches, clocks, arithmetic and all sequential stages remain unchanged. Literal proof and ten direct b511 native pairs pass with 14,519,017 live original-DATA checks; a separate reviewer checked source and results.',
        'Checked os30 generator and one actual private offline Scala regeneration reproduce the exact selected CPU. Hash guards, atomic publication, freshness and other variant behavior remain qualified.',
        'Texture c3fdc0ad selects incoming address on req_valid and latched address otherwise. Accept/prime ownership proves every enabled RAM address exact; disabled read payload may differ. Literal proof, three cache sizes and 16 actual GPU native/boundary pairs preserve data and cycles; independent source review is bound.',
        'Audio ef06e913 decomposes the exact modulo21 signed-sample sum into low16 carry and early upper5 +/-1 choices. Saturation, multiply, registers, voice count, scheduling and sample emission are unchanged. Full-domain algebra,17,777,216 literal cases and paired actual mixer tests including32-voice throughput preserve outputs/cycles. The audio author is this composition reviewer; independent audio-accum-carry-review-v1 is separately required and bound.',
        'Normal-build-v9 and frozen-project generation select the mapped source/QIP/SDC/MIF/define/settings closure through eight os30-only overrides and31 proposed files. Common/master modules and os20/os25 remain unchanged. The two additional shadows are audio and texture; no source is globally replaced.',
        'Selected app is unchanged load-submit-overlap-v1 bbb1c31a. Actual coupled performance evidence and qualified zero-cycle component bridges remain bound with their original scope; this is not a new whole-native-game or Pocket FPS result.',
        'Map passed with the recorded local/top deltas. Mapped ALUT/register counts do not establish fitted ALMs/LABs, greater-than90MHz operation or configured100MHz timing closure.'
      ],limits=[
        'Only the parent-requested saved seed2 AREA fit with existing CPU SPEED override/physical options is gated. This reviewer performed no fit.',
        'Routed setup/hold and frequency requirements remain separate gates. No25FPS, clock closure, promotion or deployment is claimed.',
        'Whole-system correctness evidence is bounded; full-input claims apply to the proven binary expressions. No universal four-state equivalence claim.',
        'No production, sibling or SD modification is authorized by this gate.'
      ],evidence_sha256=evidence)
    with (O/'independent-review.json').open('x') as stream:
        json.dump(review,stream,indent=2); stream.write('\n')
    print(sha(O/'independent-review.json'))

if __name__=='__main__': main()
