#!/usr/bin/env python3
"""Read-only cost bounds for remaining LOD5 host preparation and profiling."""
import csv,json
from pathlib import Path
from phase10_experiment import CAMP,sha,write
P=CAMP/'phase10';SRC=P/'load-cram-driver-v1';CO=P/'lod5-real-coupled-v2';OLD=CAMP/'phase9/checks-state-f11';OUT=P/'lod5-residual-host-audit-v1'
OUT.mkdir(exist_ok=False)
def prof(n):return {r['function']:r for r in csv.DictReader((CO/'run-candidate'/n).open(),delimiter='\t')}
a=prof('profile-0360.tsv');b=prof('profile.tsv');names=['gfx_cpu_indexed_run','gfx_gpu_vtx_cache_batch','load_alias_copy.constprop.0','load_alias_vertex','emit_tri_state','gfx_gpu_cram_vertex_source','gfx_gpu_vtx_cache_begin'];rows={}
for n in names:
 d={k:int(b[n][k])-int(a.get(n,{}).get(k,0))for k in ['calls','self_insns','self_cycles','icache_miss','dcache_miss','mispredict']}
 d.update(self_ms_per_frame=d['self_cycles']/800000,instruction_base_ms_per_frame=d['self_insns']/800000,penalty_ms_per_frame=(d['self_cycles']-d['self_insns'])/800000);rows[n]=d
geom=json.loads((CO/'run-candidate/geometry.json').read_text());ops=list(csv.DictReader((CO/'run-candidate/geometry-operations.csv').open()));wait=sum(int(r['cycles'])for r in ops)/800000
contexts=dict(loads_per_frame=geom['loads']/8,register_writes_per_frame=geom['writes']/8,register_writes_per_load=geom['writes']/geom['loads'],observed_load_interval_ms_per_frame=wait,adapter_self_ms_per_frame=rows['load_alias_vertex']['self_ms_per_frame'],adapter_outside_observed_load_upper_bound_ms_per_frame=rows['load_alias_vertex']['self_ms_per_frame']-wait,result_alias_fill_cycles_ms_per_frame=geom['alias_cycles']/800000,limits='LOAD interval includes poll-loop CPU work; adapter self excludes copy and source helper. Alias fills are a subset of copy cost. Backend penalties include ring wait and cannot be counted as removable packing.')
oldprof={}
for first in [360,600]:
 x=json.loads((OLD/f'coupled-normal-{first}.json').read_text());y=json.loads((OLD/f'coupled-diagnostic-{first}.json').read_text())
 oldprof[str(first)]=[]
 for l,r in zip(x['comparisons'],y['comparisons']):
  assert l['traffic']==r['traffic'];oldprof[str(first)].append(dict(traffic=l['traffic'],normal_ms=l['candidate_ms'],diagnostic_ms=r['candidate_ms'],extra_ms=r['candidate_ms']-l['candidate_ms'],time_increase_percent=100*(r['candidate_ms']/l['candidate_ms']-1)))
write(OUT/'result.json',dict(read_only=True,window='LOD5 actual-coupled360..367,8frames',functions=rows,contexts=contexts,current_build=dict(PROFILE=0,PERF_DIAG=0,renderer_scopes='Compile-time no-ops',SDK_timer_hooks='Compile-time absent',direct_PROF_TMR='TARGET_POCKET absent because SM64_PROFILE0',native_perf_functions_executed=0),older_same_renderer_profiler_bounds=oldprof,profiler_limits='OlderF11 IEEE-FMA/timing harness; cannot substitute for native-FMA LOD5 or physicaldevice. The exact same8input source hashes were compiled with PERF_DIAG0 versus1 and DETAILS0.',rejected_batch_reserve=json.loads((P/'batch-reserve-v1/decision.json').read_text())))
(OUT/'REPORT.txt').write_text('''Read-only currentLOD5 host preparation audit.

Largest CPU block is gfx_cpu_indexed_run5.268754ms self/frame:
3.916021ms instructionbase plus1.352731ms penalties. It performs exact
clip/winding rejection, TRI2atomicfallback, W-range/equalW guards, per-slot
dirtymask bookkeeping and unique dirtyvertex formatting. Each acceptedrun
has immutable material/slots; preparation already occurs once perdirtyslot,
notoncepertrianglecorner. Unused/allculled runs avoidmaterial preparation.

Backend batch3.441994ms includesringbackpressure; its instructionbase is
1.793428ms, not3.44ms of removable packing. Existing batch-reserve-v1 already
moved SDKreservations to boundedchunks and savedonly0.226% modeledCPUtime;
it changedpublication/MMIOinterleaving andwasnotqualified. Do not repeatit.
Materialstate emission0.817360ms alreadyhasdirty/dynamic-field and fullimage
memos. Eligibilitymemo0.136556ms is small. Counting allstate/eligibilitywork
as avoidablewouldstillyieldlessthan1ms, andrequiredchangedstate cannotgoaway.

LOADadapter3.564518ms includes2.303478ms observedSTART-to-DONE; atmost
1.261040ms isoutside that interval. Contextsarealreadydirty-tracked:
7.54registerwrites/job total, versusfullmatrix+light on everyjob. Copy
1.771403ms isseparate; aliaslinefill0.399ms isincludedthere. Eliminatingcopy
requiresa differentcanonicalstorage/ownershiparchitecture, notaguardcleanup.
Canonical32B packedUV wasalreadynegative; ordinarysame-layout copy isrequired.
The sourcecache's0.306789ms includesretainedmeasurementcountersandlookup;
thereisno evidenceofseveralms redundant context/key work inthisclient.

Currentnormal coupledcontrol isalreadyPROFILE0/PERF_DIAG0. Detailed/top
renderer scopes, SDKtimer hooks anddirectPROFILEtimer blocks arecompiledout;
noneofthe sm64_perf helpers execute. OlderexactpairedF11 diagnostic build
PERF1/DETAILS0 costs0.285..0.325ms inmatchingquiet-busywindows (~0.85..0.99%).
That olderIEEE-FMA model isonlyan overheadbound, notcurrentLOD5 qualification
oraphysicalFPSclaim. It cannotexplainseveralms oftherecentframegap.

No newlarge, evidence-backed software-only removal wasidentified inthis
bounded audit. Current fit/ownership qualification shouldproceedwithout
repeatingmemo,reservation,packedUV orraw-cull candidates alreadyclosed.
No code, rendering, workload, appbuild, hardware, or deploymentchanged.
''')
inputs=[SRC/'gfx_pc.c',SRC/'gfx_gpu.c',SRC/'perf_pocket.h',SRC/'perf_pocket.c',SRC/'build-normal.json',CO/'run-candidate/profile-0360.tsv',CO/'run-candidate/profile.tsv',CO/'run-candidate/geometry.json',CO/'run-candidate/geometry-operations.csv',P/'batch-reserve-v1/decision.json']+[OLD/f'coupled-{mode}-{first}.json'for mode in ['normal','diagnostic']for first in [360,600]]+[OLD/'build-normal.json',OLD/'build-diagnostic.json']
write(OUT/'manifest.json',dict(inputs={str(p.relative_to(CAMP)):sha(p)for p in inputs},outputs={p.name:sha(p)for p in OUT.iterdir()},helper_sha256=sha(Path(__file__))))
print('manifest',sha(OUT/'manifest.json'))
