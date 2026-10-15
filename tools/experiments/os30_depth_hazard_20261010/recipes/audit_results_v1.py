#!/usr/bin/env python3
"""Independent complete-oracle revalidation and bounded counter interpretation."""
import hashlib,importlib.util,json
from pathlib import Path
D=Path(__file__).resolve().parents[1];Q=D.parent/'queued-front-v1';N=Q/'fixture-v2'
spec=importlib.util.spec_from_file_location('qcheck',Q/'check_replays.py');q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
read=lambda p:json.loads(p.read_text())
q.ref.N=N;q.ref.CFG=dict(x.split('=',1)for x in(N/'input.cfg').read_text().splitlines()if '='in x);q.ref.prior.health=q.health
labels=['baseline-v1','legacy-srw-v1','depth-v3','priority-v1'];result={};evidence={}
for label in labels:
    run=D/label/'run';build=read(run.parent/'build.json')
    assert Path(build['config']).resolve()==(N/'input.cfg').resolve()
    assert sha(Path(build['elf']))==read(N/'prepared.json')['elf_sha256']
    check,commands,golden=q.ref.validate(run);q.ref.front_preparation(run)
    assert [f['commands']for f in commands]==[f['commands']for f in golden]
    profile=read(run/'gpu-profile.json');warm=profile['windows']['2'];part=dict(zip(profile['partition_names'],warm['partition']));metrics=warm['metrics']
    result[label]={'oracle':'PASS_FULL_FUNCTIONAL_ORACLE_AND_HASH_BINDING','interval_cycles':check['health']['cycles'],
      'partition':part,'reads':warm['reads'],
      'cache':{k:metrics[k]for k in ('cache_hits','cache_misses','cache_writebacks','cache_flush_entries','cache_maintenance_cycles','cache_upstream_aw_wait','cache_upstream_w_wait','cache_downstream_write_words','cache_busy')},
      'limits':check['health']['latency_maxima']}
    for p in run.iterdir():
      if p.is_file():evidence[str(p)]=sha(p)
    evidence[str(run.parent/'build.json')]=sha(run.parent/'build.json')
base=result['baseline-v1']
for label,d in result.items():
 d['warm_cycle_change']=d['interval_cycles'][1]-base['interval_cycles'][1]
 d['warm_percent_change']=100*d['warm_cycle_change']/base['interval_cycles'][1]
 d['cache_delta_vs_baseline']={k:v-base['cache'][k]for k,v in d['cache'].items()}
 d['partition_delta_vs_baseline']={k:v-base['partition'][k]for k,v in d['partition'].items()}
for p in [Path(__file__),Q/'check_replays.py',q.T/'tests/check_replays.py',q.ref.OLD,N/'prepared.json',D/'observer-v3/gpu_observer.hpp']:
 evidence[str(p)]=sha(p)
report={'status':'PASS_INDEPENDENT_RESULTS_AUDIT_RETAIN_BASELINE','runs':result,
 'interpretation':[
 'Small selective depth gate removes239154 cycles from the depth-gate partition but adds220484 cycles to response-wait; frame improves only8104 cycles,0.1855856%. This is predominantly wait relocation, not removal.',
 'Read priority versus small gate reduces response-wait108711 cycles but increases depth-gate30907, read-drain/setup59821, write-backpressure11614, idle/cache-drain and other partitions. Warm frame becomes9155 cycles slower than small gate and1051 slower than baseline.',
 'Cache miss counts change by at most11 of12266, and writebacks by at most10 of19205. These tiny shifts do not support a cache-capacity or dramatic traffic-reduction explanation.',
 'Read priority increases cache upstream AW wait484736 and W wait456527 cycles versus baseline; these overlapping handshake-stall metrics are not additive wall time.',
 'Cache maintenance grows161824->165190->172533 cycles (baseline/small/priority), with flush entries44->47->55. This is a secondary scheduling/locality effect, not proof of an independent removable bottleneck.',
 'Retain the simpler baseline: no candidate demonstrates a material whole-frame benefit in these two prepared intervals; do not proceed to fit or deployment on this evidence.'],
 'scope':'Same ELF and fresh fixed fixture, exact color/depth/all buffer history, Surface pool, command stream, initial CPU state and all FP/FCSR; only established typed framebuffer/clear and later s5 timestamp normalization. Synthetic display/audio, two intervals; not sustained hardware FPS. No new simulation, RTL or fit performed by this audit.',
 'evidence_sha256':evidence}
assert report['runs']['depth-v3']['warm_cycle_change']==-8104
assert report['runs']['priority-v1']['warm_cycle_change']==1051
out=D/'review/results-audit-v1.json';assert not out.exists();out.write_text(json.dumps(report,indent=2)+'\n')
print(out)
for name,d in result.items():print(name,d['interval_cycles'],d['warm_percent_change'],d['cache_delta_vs_baseline'])
