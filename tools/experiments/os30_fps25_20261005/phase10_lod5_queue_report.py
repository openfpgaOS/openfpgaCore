#!/usr/bin/env python3
"""Seal queue-only LOD5 map/native evidence without a fitter invocation."""
import hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
M=P/'lod5-queues-map-v1';N=P/'lod5-queue-native-v1';Q=P/'cpu-queues-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')
mr=json.loads((M/'result.json').read_text());assert mr['passed_map'] and not mr['full_fit_attempted']
histogram=json.loads((P/'bram-alias-native-v1/result.json').read_text());hist={int(k):v for k,v in histogram['histogram'].items()}
native={}
for traffic in ('quiet','nominal'):
 a=json.loads((N/'control'/f'{traffic}-result.json').read_text());b=json.loads((N/'queues'/f'{traffic}-result.json').read_text())
 assert a['passed'] and b['passed'] and a['firmware_sha256']==b['firmware_sha256']
 assert len(a['alias'])==len(b['alias'])==192 and len(a['submission'])==len(b['submission'])==12
 for x,y in zip(a['alias'],b['alias']):assert x[:5]==y[:5]
 for x,y in zip(a['submission'],b['submission']):assert x[:3]==y[:3]
 weighted={}
 for kind,label in [(0,'SDRAM_source'),(1,'exact_alias_source')]:
  values=[]
  for run in (a,b):
   costs={n:statistics.mean((v[5]+v[6])/v[4] for v in run['alias'] if v[1]==kind and v[2]==n) for n in hist}
   values.append(sum(costs[n]*count for n,count in hist.items())/histogram['frames']/100000)
  weighted[label]=dict(control_ms=values[0],queue_ms=values[1],delta_percent=100*(values[1]/values[0]-1))
 native[traffic]=dict(weighted_invalidation_plus_cold_copy=weighted,submission=[dict(cold=x[0],words=x[1],repeats=x[2],control_total_cycles=x[3],queue_total_cycles=y[3],delta_percent=100*(y[3]/x[3]-1))for x,y in zip(a['submission'],b['submission'])],alias_rows=192,submission_rows=12)
result=dict(passed_correctness=True,full_game_qualified=False,full_fit_attempted=False,only_generator_changes=json.loads((Q/'recipe.json').read_text())['changes'],map_delta=mr['delta'],native=native,
 native_scope='Actual original/queue-only CPU RTL, unchanged128KiB D$ and32KiB I$, exact selected LOD5 readonly alias shim with1cycle synthetic result RAM, real SDRAM/scanout and actual GPU MMIO/ring/parser. No accelerator compute, GPU raster load, CPU-only reset stress, or full-frame CPU-cache history in this fixture.',
 inherited_tradeoffs='Previously sealed native triangle kernels +0.14% quiet/+0.27–0.28% scanout; broad CPU stress +3.48%/+4.15%; fullgame remains unmeasured.',
 attribution='CPU subtree saves285ALUT/144FF; cpu_system saves289/161; complete mapped core332/161.1304fewer memory bits are queue storage, not cache capacity. Changed placement can amplify or erase synthesis gains; no ALM/LAB/Fmax claim.',
 retained_capabilities=['128KiB2way Dcache','32KiB2way Icache','64-bit minimum fetch width','BTB1024/gshare4096/RAS','full original ISA including atomics','radix4 division','pipelined I2F','original reduced-accuracy FMA'],
 evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in [M/'prepared.json',M/'result.json',N/'prepared.json',N/'firmware.json',Q/'final-manifest.json',Q/'regression/result.json',P/'bram-alias-native-v1/result.json',Path(__file__)]})
save(N/'result.json',result)
(N/'REPORT.txt').write_text('Queue-only CPU with LOD5/span-v6: map and native compatibility screen\n\nExactly five generator options change: I-cache refill2→1, D-cache refill2→1, D writeback2→1, store buffer2slots/16operations→1slot/4operations. Cache sets/ways, predictors, full ISA, fetch width, radix4 division, pipelined I2F and FMA arithmetic remain unchanged. This reuses the previously generated and tested CPU; no regeneration occurred. Six zero-width AXI ID ports are omitted by that generator; the wrapper supplies constantzero IDs to its unchanged external slices/fabric.\n\nThe one map-only run changes only CPU RTL and that required wrapper in the frozen LOD5/span-v6 AREA configuration. Seed2 and all Quartus options/clocks remain unchanged. CPU −285ALUT/−144FF; cpu_system −289/−161; complete mapped core −332/−161.1304 fewer inferred memory bits are queue storage. There was no fitter invocation and no ALM/LAB/timing estimate is claimed.\n\nFresh actual-native control/candidate models use the exact current LOD5 readonly alias shim (not the earlier simplified alias), a1cycle completed result RAM, actual CPU caches/AXI target, retained SDRAM/controller, and real GPU CPU-ring transport/parser. Counts1..32, three destination positions, warm/cold alias and SDRAM reads, signed UV conversion, cache invalidation and all9 output fields pass.192 cases/run. Twelve submission cases/run cover16..4094 words, warm/cold CPU source, eight repeats each, GPU publication pointer, wrap, final idle/read pointer and exact final fence. All four quiet/nominal A/B runs pass data, AXI IDs/burst boundaries/write completion checks.\n\nWeighted invalidation+cold alias-copy cost is exactly2.016243397969ms/frame for both CPUs in both traffic modes. This weighting uses the existing361-frame G_VTX size histogram and is not an application speed measurement. SDRAM-copy nominal cost rises0.075%; nominal submissions64..4094words are cycle-identical; first16word case adds13cycles across8repeats. Existing broad stress penalty3.48–4.15% and trianglekernel0.14–0.28% remain relevant. Full SM64/Quake2 performance and accelerator/fabric concurrency are not established.\n\nInitial native logs retained only8KiB UART and could not support complete per-case analysis; preserved under logging-attempt-1. Only host log retention was enlarged; HDL/firmware stayed unchanged for the final four runs. Earlier firmware before explicit publication checking is separately preserved and was not used for final results. No production app/core/SDK/SD changes and no full fit.\n')
for directory in (M,N):
 files=[]
 for p in directory.rglob('*'):
  if not p.is_file() or p.name=='final-manifest.json':continue
  rel=p.relative_to(directory)
  if 'obj' in rel.parts and p.name!='Vtb_system':continue
  if 'db' in rel.parts or 'incremental_db' in rel.parts:continue
  files.append(p)
 files += [ROOT/'tools/experiments/os30_fps25_20261005'/n for n in ['phase10_lod5_queue_report.py','phase10_lod5_queue_native.py','phase10_lod5_queues_map.py','phase10_cpu_queue_adapter.py']]
 files += [Q/'final-manifest.json',Q/'recipe.json',Q/'generated.json',Q/'VexiiRiscv.v',Q/'regression/result.json']
 manifest=dict(passed=True,fitter_invoked=False,qualified_for_deployment=False,evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(files))})
 save(directory/'final-manifest.json',manifest);print(directory.name,sha(directory/'final-manifest.json'),len(files))
