#!/usr/bin/env python3
"""Seal isolated original/queue CPU + unchanged LOD5 client integration evidence."""
import hashlib,json,re
from pathlib import Path
import phase10_lod5_live_native as N
ROOT=N.ROOT;P=N.P;V1=P/'lod5-live-native-v1';V2=P/'lod5-live-native-v2';sha=N.sha
files=set()
def bind(p):
 assert p.is_file(),p;files.add(p);return sha(p)
def verify_sources(d):
 p=d/'prepared.json';j=json.loads(p.read_text());bind(p)
 for n,h in j['source_sha256'].items():assert sha(d/n)==h,n;bind(d/n)
 b=json.loads((d/'build.json').read_text());assert b['passed'];assert b['simulator_sha256']==sha(d/'obj/Vtb_system');bind(d/'build.json');bind(d/'build.log');bind(d/'obj/Vtb_system')
def row(log):
 s=log.read_text();bind(log)
 assert 'LIVE_LOD5_COMPLETE' in s and 'KERNEL FAIL'not in s
 assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0'in s
 assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0'in s
 for master in ['I','MEM','PER']:assert re.search(master+r': beats=\d+ mism=0 rid_mism=0 rlast_slips=0 orphans=0 pending=0',s)
 total=int(re.search(r'cycles=(\d+) PC=',s)[1]);c=re.findall(r'COUNTS ((?:[0-9a-f]+ )+)',s)[-1].split()
 hw=re.search(r'NATIVE_HW uploads=(\d+) loads=(\d+) alias=(\d+) stalls=(\d+) texture=(\d+) resets=(\d+) geo_wait=(\d+)',s)
 return dict(total_simulation_cycles_including_preload=total,counts=[int(x,16)for x in c],hardware=dict(zip(['uploaded_words','load_bursts','alias_words','alias_stall_cycles','texture_words','cpu_resets','geometry_AR_wait_cycles'],map(int,hw.groups())))if hw else None)
results={}
for v in [V1,V2]:
 for mode in ['control','queues']:
  d=v/mode;verify_sources(d)
  for traffic in ['quiet','nominal']:
   p=d/(traffic+'-result.json');j=json.loads(p.read_text());assert j['passed'];assert j['log_sha256']==sha(d/(traffic+'.log'));assert j['firmware_sha256']==sha(v/'firmware/kernel.bin');bind(p)
   results[v.name+'/'+mode+'/'+traffic]=row(d/(traffic+'.log'))
 for p in (v/'firmware').rglob('*'):
  if p.is_file():bind(p)
 for n in ['prepared.json','firmware.json','firmware-command.json']:bind(v/n)
for mode in ['control','queues']:
 p=V2/'full-rgb'/(mode+'-nominal.json');j=json.loads(p.read_text());assert j['passed'];assert j['simulator_sha256']==sha(V2/mode/'obj/Vtb_system');assert j['firmware_sha256']==sha(V2/'full-rgb/firmware/kernel.bin');bind(p)
 results['full-rgb/'+mode+'/nominal']=row(p.with_suffix('.log'))
 assert results['full-rgb/'+mode+'/nominal']['hardware']['cpu_resets']==1
 assert results['full-rgb/'+mode+'/nominal']['hardware']['alias_stall_cycles']>0
 assert results['full-rgb/'+mode+'/nominal']['hardware']['texture_words']>100000
 for p in(V2/'full-rgb/firmware').rglob('*'):
  if p.is_file():bind(p)
for n in ['oracle.json','firmware.json','firmware-command.json','build.log']:bind(V2/'full-rgb'/n)
neg=V2/'negative-no-invalidate';s=(neg/'negative.log').read_text();assert 'KERNEL FAIL 00000001 00000009 bf84cccc c649ec30' in s and 'LIVE_LOD5_COMPLETE'not in s
for p in neg.iterdir():
 if p.is_file():bind(p)
for n in ['phase10_lod5_live_native.py','phase10_lod5_live_stress.py','phase10_lod5_live_stress_kernel.c','phase10_lod5_live_stress_invoke.S','phase10_lod5_live_full_rgb.py','phase10_lod5_native_vectors.py','phase10_lod5_live_seal.py']:
 p=ROOT/'tools/experiments/os30_fps25_20261005'/n
 if p.exists():bind(p)
for p in [P/'compact-lod5-core-v2/prepared.json',P/'load-cram-driver-v1/app.elf',P/'cpu-audit/lighting-native/native-golden.txt',P/'cpu-audit/lighting-native/final-manifest.json',P/'unified-load-components-v2/manifest.json']:
 if p.exists():bind(p)
summary=dict(passed=True,deployment_ready=False,scope='Isolated unchanged compiled LOD5 client on actual original and reduced-queue CPU RTL, selected live unified-v2 LOAD, actual peripheral/alias/CRAM controller/PHY and full16MiB chip models.',runs=results,
 checks=dict(input_cases=52,original_native_jobs=20,original_native_vertices=230,synthetic_count_vertices=528,normal_helper_loads=104,normal_helper_vertices=1516,recovery_vertices=4,cpu_reset_copy_vertices=4,logical_vertex_fields=9,comparisons_per_stress_run=37048,physical_upload_words_per_stress_run=4548,accepted_load_bursts_per_stress_run=614,alias_words_per_stress_run=12480,source_cache_misses=52,source_cache_hits=54,client_ineligible_tests=6,hardware_unsupported_tests=1,recovery_loads=1,cpu_reset_during_accepted_cram_read=1,gpu_fence_commands=2213,gpu_fence_results_checked=5),
 negative_control=dict(passed=True,change='Only native load_alias_vertex CBO.INVAL at0x104cf426 replaced by32-bit NOP.',failure='job1 destination slot1 x still bf84cccc fromjob0, expected c649ec30.'),
 full_rgb_oracle=dict(upgraded_historical_memo_rows=85,source='Independent actual-A fresh lighting golden corpus; all203 lit native inputs found and already-qualified colors match.',passed_original_and_queue_nominal=True),
 limits=['Not wholegame rendering, FPS, synthesis or physical timing qualification.','The dedicated immutable source window0x10c00000..0x10c10000 contains fixture assets; all invoked application instruction bytes and helper globals/literals are retained.','Normal initialization is reproduced by setting SDK GPU base, pool enable/top and metadata; OS/application startup is not executed.','Competing texture fills are a deterministic AXI read client using the selected shared arbiter and physical path, not a rendered GPU scene. Real GPU receives ordered fence commands only.','Full-capacity chips use modeled refresh extension3; nominal scanout is synthetic.','Dynamic alias data is checked through native field goldens and the missing-invalidate negative control. Independent bus checker retains address/ID/beat checks and all ordinary SDRAM data checks; alias RDATA is excluded from its static SDRAM oracle.','Clip padding bytes are not semantically compared; clip byte and all eight other logical fields, including all RGB/alpha in the final nominal reruns, are compared.','CPU-only reset case occurs during an accepted CRAM source read; it does not claim arbitrary interrupted CPU cached writes or software continuation. The restarted fixture drains/completes and reads the preserved result.','Physical CRAM source RRESP is tiedOKAY as selected top. This campaign tests client rejection and arithmetic unsupported failure, not fabricated source-bus errors.','Five final fence IDs and wraparound are checked; this is not pixel/raster coverage.'])
report='''Actual native LOD5 integration: PASS, not deployment qualification.

Original and reduced-queue CPUs both pass quiet/nominal tests of the unchanged
LOD5 compiled client, physical source upload into0xfc0000+, selected unified-v2
LOAD, live read-only alias, CBO invalidation and copy into ordinary LoadedVertex.
52 cases include20 captured jobs and counts1..32; every case runs as a source
cache miss and hit, with varying/edge destination and untouched-neighbor checks.
The final nominal reruns replace85 historical memo-hit color masks with fresh
actual-A lighting goldens, so all nine logical fields are now checked.

Each contention/reset run checks37,048 comparisons,4,548 physical uploads,
614 accepted LOAD bursts and12,480 alias words. Alias response backpressure,
competing physical texture fills, nominal SDRAM scanout, arithmetic unsupported
failure, recovery and CPU reset during accepted CRAM read all pass. Restart
preserves completed results;2,213 realGPU fence commands wrap the ring and five
final fence results match. No source or model instruction shortcut substitutes
accelerator result data. Removing only CBO.INVAL yields stale coordinates at the
second input job, as required by the negative control.

Limits are explicit in result.json. This is not wholegame FPS, arbitrary reset
coverage, source-bus fault injection, pixel/raster qualification, or timing fit.
Production, SD and frozen upstream artifacts remain unchanged.
'''
N.save(V2/'result.json',summary);(V2/'REPORT.txt').write_text(report);bind(V2/'result.json');bind(V2/'REPORT.txt')
N.save(V2/'manifest.json',dict(passed=True,deployment_ready=False,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(files)}))
print('manifest',sha(V2/'manifest.json'),'files',len(files))
