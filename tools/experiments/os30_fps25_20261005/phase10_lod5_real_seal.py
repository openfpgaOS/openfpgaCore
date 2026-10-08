#!/usr/bin/env python3
"""Seal actual real-client cold/warm coupled results and their model limits."""
import csv,json
from pathlib import Path
import phase10_lod5_real_coupled_v2 as m
P=m.P;O=m.OUT;sha=m.sha;write=m.write
prepared=json.loads((O/'prepared.json').read_text())
for n,h in prepared['sources'].items():assert sha(O/n)==h,n
build=json.loads((O/'model/build.json').read_text());assert sha(O/'model/obj/Vtb_gpu')==build['simulator_sha256']
results={}
for suffix,first in[('',360),('-cold',234)]:
 a=O/('run-baseline'+suffix);b=O/('run-candidate'+suffix)
 for d in(a,b):
  inv=json.loads((d/'inputs.json').read_text());assert inv['success']
  for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
  assert 'FATAL'not in(d/'run.log').read_text()
 exact={n:(a/n).read_bytes()==(b/n).read_bytes()for n in('scene.csv','geometry.csv','culling.csv')};assert all(exact.values())
 buffers=[]
 for i in range(first,first+8):
  name=f'frame_{i:05}.bin';assert(a/name).read_bytes()==(b/name).read_bytes();buffers.append(dict(frame=i,sha256=sha(b/name),actual_color_and_depth_exact=True))
 x=json.loads((a/'coupled.json').read_text());y=json.loads((b/'coupled.json').read_text());bx=x['cpu_cycles']/800000;cy=y['cpu_cycles']/800000
 ops=list(csv.DictReader((b/'geometry-operations.csv').open()))
 frames=[];af=list(csv.DictReader((a/'frames.csv').open()));bf=list(csv.DictReader((b/'frames.csv').open()))
 for i in range(first,first+8):frames.append(dict(frame=i,baseline_ms=int(af[i]['est_cycles'])/100000,candidate_ms=int(bf[i]['est_cycles'])/100000))
 results['cold'if suffix else'warm']=dict(first=first,last=first+7,baseline_span_v3_ms=bx,candidate_real_lod5_ms=cy,time_reduction_percent=100*(1-cy/bx),throughput_increase_percent=100*(bx/cy-1),additional_source_upload_words=y['texture_uploads']-x['texture_uploads'],scene_records_exact=exact,actual_render_outputs=buffers,per_frame=frames,load_jobs=len(ops),load_vertices=sum(int(x['count'])for x in ops),lit_load_jobs=sum(int(x['lit'])for x in ops),load_wait_ms_per_frame=sum(int(x['cycles'])for x in ops)/800000,manifest_baseline_sha256=sha(a/'inputs.json'),manifest_candidate_sha256=sha(b/'inputs.json'))
# The earlier full-memory warm attempt is unaffected by correcting its obsolete
# cold-upload assertion. Retained spanv3 baseline also remains cycle-identical.
for n in('baseline','candidate'):
 old=json.loads((P/'lod5-real-coupled-v1'/('run-'+n)/'coupled.json').read_text());new=json.loads((O/('run-'+n)/'coupled.json').read_text());assert old==new
parent=P/'span-nextchunk-coupled-v1/simple/run-window-8';base=O/'run-baseline'
assert json.loads((parent/'coupled.json').read_text())['cpu_cycles']==json.loads((base/'coupled.json').read_text())['cpu_cycles']
for i in range(360,368):assert(parent/f'frame_{i:05}.bin').read_bytes()==(base/f'frame_{i:05}.bin').read_bytes()
f11=json.loads((P/'gpu-critical-bound-v2/coupled/run-baseline-8/coupled.json').read_text())['cpu_cycles']/800000
# Exact eight-frame self profile, including live wait and instrumented lookup.
def prof(path):return {r['function']:r for r in csv.DictReader(path.open(),delimiter='\t')}
a=prof(O/'run-candidate/profile-0360.tsv');b=prof(O/'run-candidate/profile.tsv');hot=[]
for n,r in b.items():
 cycles=int(r['self_cycles'])-int(a.get(n,{}).get('self_cycles',0));calls=int(r['calls'])-int(a.get(n,{}).get('calls',0));hot.append(dict(function=n,self_cycles=cycles,self_ms_per_frame=cycles/800000,calls=calls))
hot.sort(key=lambda r:r['self_cycles'],reverse=True);assert sum(x['self_cycles']for x in hot)==round(results['warm']['candidate_real_lod5_ms']*800000)
write(O/'hot-profile.json',dict(first=360,last=367,self_only=True,worker_wait_and_ring_wait_charged_to_calling_function=True,functions=hot))
write(O/'result.json',dict(passed=True,results=results,original_f11_ms=f11,original_f11_time_reduction_percent=100*(1-results['warm']['candidate_real_lod5_ms']/f11),original_f11_throughput_increase_percent=100*(f11/results['warm']['candidate_real_lod5_ms']-1),old_ideal_source_parent_ms=34.65349625,real_client_warm_net_added_ms=results['warm']['candidate_real_lod5_ms']-34.65349625,warm_baseline_same_cycles_and_buffers_as_retained_span_v3=True,actual_full16MiB_chip_sha256=sha(O/'model/frozen/cram_chip_model.v'),source_word_bytes_checked_at_every_actual_LOAD=True,qualified_for_deployment=False,limits=['Estimated retainedCPU/cache timing; actualLOD5/GPU/SDRAM/CRAM hardware and worddata. Synthetic backgroundtraffic and fixed simulationstep.','No physicalPocketFPS claim, no complete nativeCPU bus coupling, no kernelPCM/interrupt cost.','Warmup uses actualapp source lookup/upload work with functionalLOD5 math then copies residentbytes into physicalmemory at idlehandoff. Cold window includes actual firstBOB uploads.','No host pointerremapping or immutablepoolpreload table; source addresses are actual top-of16MiB CRAM offsets.','Appsourcepool neverrecyles; futurelevels mayexhaust256KiB and useCPUfallback.','Inheritedv1 cold guard rejected intended highsourceuploads; v2 changes only testbenchownershipassertions and passivecount/light tracing. ComponentRTL/client identical.']))
(O/'REPORT.txt').write_text('''Real LOD5 client with actual full16MiB CRAM and span lookahead v3.

Warm360..367: retained spanv3 37.11836875ms -> real client34.73734000ms.
Original simple F11 was37.82757375ms:8.1693% lower modeled frame time,
8.895999% greater modeled throughput. This is not a physicalFPS result.
All8 actual color/depth buffers and368 scene/geometry/culling records match.
No source misses in the warmwindow. Real lookup costs0.306789ms/frame SELF,
including all built-in counters; net end-to-end cost versus the old ideal
preloaded/remapped source model is0.083844ms/frame because overlap changes.

Cold BOB234..241: spanv3 49.9776925ms -> real client48.968385ms mean.
All8 actual buffers and242 scene/geometry/culling records match. Candidate
performs23100 extra real source-upload words (92400bytes=90.234375KiB).
Frame234 grows134.87882->142.04381ms (+7.16499ms net cold impact).
Frames235..241 improve1.9..3.0ms. This firstframe difference includes offload
and GPU overlap; it is not a separately isolated upload-only duration.

Each realLOAD verifies physical source words against its immutableCPU pointer
from the appcache. Both8MiB dies and exacttopoffsets are modeled without alias.
Textures maynotfetch reservedpool; source uploads maynotoverlap activeLOAD.
NativeCPU/cache model, fixed game cadence and synthetic traffic limitations
remain. Functional failures/fullpool/absence are separately sealed under
load-cram-functional-v1. No production edits, deployment or FPGAfit performed
by this experiment. A separate wholecore fit remains a release gate.
''')
write(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p)for p in O.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts},helpers={Path(m.__file__).name:sha(Path(m.__file__)),Path(__file__).name:sha(Path(__file__))},functional_manifest_sha256=sha(P/'load-cram-functional-v1/manifest.json'),physical_highaddress_proof_sha256='3e3da3782fbfacc03861dd025a776b77ba0da7567a368bc9fa06fb637b0d8636'))
print(json.dumps({k:{n:v[n]for n in('baseline_span_v3_ms','candidate_real_lod5_ms','time_reduction_percent','additional_source_upload_words')}for k,v in results.items()},indent=2))
