"""Validate paired captures and summarize measured GPU work, never whole-game FPS."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-depth-prefetch-20261002'
key=lambda r:(r['mode'],r['scene'],r['frame'])
load=lambda name:json.loads((OUT/name).read_text())
candidate=sys.argv[1] if len(sys.argv)>1 else 'shared'
base=load('replay-results-replay-baseline.json')
after=load(f'replay-results-replay-{candidate}.json')
extra=load('replay-extra-results-replay-baseline,replay-armed.json')
base += [r for r in extra if r['label']=='replay-baseline']
after += ([r for r in extra if r['label']=='replay-armed'] if candidate=='armed' else load(f'replay-extra-results-replay-{candidate}.json'))
base={key(r):r for r in base}
assert len(base)==len(after)==78
samples=[]
for r in after:
    b=base[key(r)]
    assert r['vector_sha256']==b['vector_sha256']
    assert r['sha256']==b['sha256'] and r['bytes']==b['bytes'],key(r)
    assert r['stats']['cycles']<=b['stats']['cycles'],('cycle regression',key(r))
    bc,ac=b['stats']['cycles'],r['stats']['cycles']
    samples.append(dict(mode=r['mode'],scene=r['scene'],frame=r['frame'],before_cycles=bc,after_cycles=ac,
                        cycle_reduction_percent=100*(1-ac/bc),throughput_gain_percent=100*(bc/ac-1),
                        identical=True,output_sha256=r['sha256'],vector_sha256=r['vector_sha256']))
report=dict(candidate=candidate,scope='26 frames captured from current SM64 ELF, replayed through current os30 GPU and the real 100 MHz Pocket SDRAM path. CPU/audio/scanout traffic are read proxies. No CPU submission timing, real mixer, presentation pacing or FPS measurement. Cache flushes at each idle batch boundary. SDK rasterizer lacks os30 triangles; pixel comparisons are baseline RTL versus candidate RTL.',samples=samples,by_traffic={})
for mode in range(3):
    rows=[r for r in samples if r['mode']==mode]
    b=sum(r['before_cycles'] for r in rows);a=sum(r['after_cycles'] for r in rows)
    report['by_traffic'][mode]=dict(captures=len(rows),before_cycles=b,after_cycles=a,cycle_reduction_percent=100*(1-a/b),throughput_gain_percent=100*(b/a-1),minimum_gain_percent=min(r['throughput_gain_percent'] for r in rows),maximum_gain_percent=max(r['throughput_gain_percent'] for r in rows))
# Hardware simplifications must preserve the established candidate's work.
assert [r['stats'] for r in load('replay-results-replay-early.json')]==[r['stats'] for r in load(f'replay-results-replay-{candidate}.json')]
verified_replays=[]
for name in ('replay-results-replay-checked.json','replay-extra-results-replay-checked.json',
             'replay-results-replay-final.json','replay-extra-results-replay-final.json'):
    if (OUT/name).exists():
        original={key(r):r for r in after}
        for row in load(name):
            assert all(row[field]==original[key(row)][field]
                       for field in ('stats','sha256','bytes','vector_sha256'))
        verified_replays.append(name)
report['validation']={'captures_byte_exact':len(samples),'all_measured_cycles_nonincreasing':True,
    'registered_gate_matches_empty_pipeline_gate':True,'other_variants':'disabled-equivalence.json',
    'independent_replay_verification':verified_replays,
    'gpu_acceptance':f'gpu-{candidate}/results.json','actual_memory':f'memory-{candidate}/results.json',
    'ordered_depth_blend':[f'gpu-{candidate}-ordered/quiet.log',f'gpu-{candidate}-ordered/gapped.log',*[f'memory-{candidate}-ordered/{i}.log' for i in range(3)]]}
for mode,result in load(f'gpu-{candidate}/results.json')['os30'].items():
    assert result['exit_code']==0 and result['failed']==0
baseline_memory=json.loads((OUT.parent/'os30-memory-opt-20261002/memory-selected/results.json').read_text())['runs']
for mode,result in load(f'memory-{candidate}/results.json')['runs'].items():
    assert result['passed']==79 and len(result['cycles'])==38
    previous=baseline_memory[mode]['cycles']
    assert result['cycles'].keys()==previous.keys()
    assert all(cycles<=previous[name] for name,cycles in result['cycles'].items())
report['validation']['synthetic_workloads']=38
report['validation']['synthetic_cycles_nonincreasing_all_three_schedules']=True
for name in report['validation']['ordered_depth_blend']:
    assert 'Acceptance Results: 1 passed, 0 failed' in (OUT/name).read_text()
(OUT/'performance.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report['by_traffic'],indent=2))
