#!/usr/bin/env python3
"""Qualify descriptor-compacted no-combine RTL using frozen v5 producer captures and prior app proofs."""
from pathlib import Path
import copy,hashlib,json

R=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
load=lambda p:json.loads(p.read_text())
source=R/'final-compact-validation.json'
assert sha(source)=='de9d229bfcbae34a74a55d2403e3a3316f9f69c44e5033696c55e2601d2afb6c'
previous=load(source)
for path,digest in previous['selected_evidence_pins'].items():
    assert sha(R.parent/path)==digest,path
core='particles-q2-descriptor-v1'
pins=load(R/'paced-gpu'/core/'source-pins.json')
assert pins['rtl/common/gpu_geometry_worker.v']=='164d5ce2eeeb478eb7504660b8a4afef4d8f6a16cf75372ba5eff26917305892'
report=copy.deepcopy(previous)
report['core']=core
report['rtl_source_pins']=pins
report['configuration_audit']=load(R/'paced-gpu'/core/'configuration-audit.json')
report['scope']='Quake2 GL1-specific no-combine configuration; this is not a general SM64/N64 shader configuration.'
report['capability_scope']='Physical AXI capability bit27 clears. Quake2 GL1 stores/prints OFQ2_CAP_COMBINE but never branches rendering on it; captures remain applicable. This source audit is distinct from the actual GPU raster replay.'
report['sample_feature_coverage']=load(R/'no-combine-sample-coverage.json')
report['reused_application_validation']=dict(path='final-compact-validation.json',sha256=sha(source),
    scope='Same frozen app ELF, commands, CPU timing, host tests, canonical reference, image/input bounds, old-core rendering and profile proofs.')
report['actual_gpu_samples']=[]
paths=[source,Path(__file__),R/'build_gpu_q2_descriptor.py',R/'compact-cycle-sensitivity.json',
       R/'paced-gpu'/core/'source-pins.json',R/'paced-gpu'/core/'build-command.json',R/'paced-gpu'/core/'build-result.json',
       R.parent/'worker-descriptor-tests/combined-equivalence.json',R.parent/'worker-descriptor-tests/gates-combined-v1/receipt.json', R/'v5-feature-command-audit.json', R/'no-combine-sample-coverage.json', R/'paced-gpu'/core/'configuration-audit.json']
for old in previous['actual_gpu_samples']:
    work=old['workload'];frame=old['frame'];stem=f'frame_{frame:05}'
    vectors=f'particles-v5-{work}-v2'
    dest=R/'paced-replays'/(vectors+'-'+core)
    area=R/'paced-replays'/(vectors+'-particles-compact-v1')
    reference=R/'reference-replays'/vectors/(stem+'.bin')
    replay=load(dest/(stem+'.json'));old_replay=load(area/(stem+'.json'))
    assert replay['passed']
    canonical=(dest/(stem+'.unpaced.bin')).read_bytes()==reference.read_bytes()
    assert canonical,(work,frame)
    same_output=all((dest/(stem+'.'+mode+'.bin')).read_bytes()==(area/(stem+'.'+mode+'.bin')).read_bytes()for mode in ['paced','unpaced'])
    same_stats=all(replay['results'][mode]['stats']==old_replay['results'][mode]['stats']for mode in ['paced','unpaced'])
    row=copy.deepcopy(old)
    cycles=replay['results']['paced']['stats']['RESULT']['cycles']
    row.update(canonical_complete_output_exact=canonical,paced_vs_unpaced_exact=True,
        candidate_whole_frame_cycles=cycles,candidate_cycle_breakdown=replay['results']['paced']['stats']['PACE'],
        modeled_throughput_gain_percent=100*(row['baseline_whole_frame_cycles']/cycles-1),
        previous_compact_rtl_complete_output_exact=same_output,previous_compact_rtl_all_counters_exact=same_stats)
    assert same_output,'Image/input bounds may only be reused for exact output'
    report['actual_gpu_samples'].append(row)
    paths.extend([dest/(stem+'.json'),dest/(stem+'.paced.log'),dest/(stem+'.unpaced.log'),
                  R/'paced-vectors'/vectors/(stem+'.json')])
busy=[x for x in report['actual_gpu_samples']if x['workload']=='main'and x['frame']in[92,94,148]]
gain=100*(sum(x['baseline_whole_frame_cycles']for x in busy)/sum(x['candidate_whole_frame_cycles']for x in busy)-1)
report['gates']['performance'].update(busy_three_sample_aggregate_gain_percent=gain,
    aggregate_reaches_20_percent=gain>=20,
    minimum_sample_gain_percent=min(x['modeled_throughput_gain_percent']for x in report['actual_gpu_samples']),
    every_sample_reaches_20_percent=all(x['modeled_throughput_gain_percent']>=20 for x in report['actual_gpu_samples']))
report['gates']['physical_qualification']=dict(status='external; compact source fit not asserted by this simulation report')
report['selected_evidence_pins']={str(p.relative_to(R.parent)):sha(p)for p in paths}
report['previous_application_evidence_pins_preserved']=True
report['simulation_qualified']=True
report['release_qualified']=False
output=R/'final-q2-descriptor-validation.json';assert not output.exists()
output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(output=str(output),sha256=sha(output),performance=report['gates']['performance'],
    counters_unchanged=all(x['previous_compact_rtl_all_counters_exact']for x in report['actual_gpu_samples'])),indent=2))
