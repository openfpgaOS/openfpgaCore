#!/usr/bin/env python3
"""Seal diagnostic-only backend counter cleanup; no device-speed claim."""
import csv, json, shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, INPUTS, sha, write
P=CAMP/'phase10';APP=P/'backend-wire-counters-v1';OUT=P/'backend-wire-functional-v1';OLD=P/'load-submit-overlap-v1';OLDTEST=P/'load-submit-functional-v1'
def main():
 source=json.loads((APP/'source.json').read_text());build=json.loads((APP/'build-normal.json').read_text())
 elf=sha(APP/'app.elf');assert elf==build['elf_sha256']==sha(OUT/'app.elf')
 for n in INPUTS:
  assert sha(APP/n)==source['sources'][n]==build['inputs'][n]['sha256']
  if n!='gfx_gpu.c':assert sha(APP/n)==sha(OLD/n)
 assert source['update_count']==28 and source['reverse_reconstruction_byte_exact']
 for n in ('counter-proof/result.json','codegen/result.json'):assert json.loads((APP/n).read_text())['passed']
 prep=json.loads((OUT/'prepared.json').read_text());assert prep['elf_sha256']==elf and prep['simulator_sha256']==sha(OUT/'functional-qsim/qsim')
 for n,h in prep['sources'].items():assert sha(OUT/'functional-qsim'/n)==h
 modes={}
 for mode in ('normal','faults','absent','timeout','fullpool'):
  result=json.loads((OUT/f'result-{mode}.json').read_text());obs=json.loads((OUT/f'observer-result-{mode}.json').read_text())
  assert result['passed'] and result['elf_sha256']==elf and obs['passed'] and obs['frames']==721
  capture=OUT/('capture-'+mode);inv=json.loads((capture/'inputs.json').read_text());assert inv['success']
  for n,h in inv['results_sha256'].items():assert sha(capture/n)==h,(mode,n)
  assert 'FATAL'not in(capture/'run.log').read_text()
  # Diagnostic gating must not change driver counters, source-pool behavior,
  # or the prepared-batch ownership proof, including injected failures.
  prior=json.loads((OLDTEST/f'result-{mode}.json').read_text())
  assert result['driver_stats']==prior['driver_stats']
  assert {k:v for k,v in result['pool_stats_last'].items()if k!='probes'}=={k:v for k,v in prior['pool_stats_last'].items()if k!='probes'}
  assert sha(OUT/f'observer-{mode}.csv')==sha(OLDTEST/f'observer-{mode}.csv')
  modes[mode]=dict(records=721,ordered_streams=8,driver_stats_unchanged=True,pool_outcomes_unchanged=True,pointer_hash_probes_delta=int(result['pool_stats_last']['probes'])-int(prior['pool_stats_last']['probes']),ownership_observer_exact_to_parent=True,queued_batches=int(obs['last']['queued']))
 a=list(csv.DictReader((OLDTEST/'capture-normal/frames.csv').open()))[360:]
 b=list(csv.DictReader((OUT/'capture-normal/frames.csv').open()))[360:]
 cost={}
 for k in ('insns','est_cycles','dcache_miss','icache_miss_sdram','mispredict'):
  x=sum(int(r[k])for r in a)/len(a);y=sum(int(r[k])for r in b)/len(b);cost[k]=dict(parent=x,candidate=y,delta=y-x)
 result=dict(passed=True,qualified_for_coupled_measurement=True,deployment_ready=False,elf_sha256=elf,modes=modes,synthetic_functional_model_per_gameplay_frame=cost,only_changed_input='gfx_gpu.c',limitations=['Functional qsim executes the actual ELF and native-FMA arithmetic but uses synthetic LOAD completion and simplified GPU semantics; 721 records are not721 actual RTL-rendered frames.','Eight ordered render streams per mode are exact after established linked-pointer padding and source-pool upload normalization.','No Pocket FPS, coupled frame-time saving, or 25FPS claim. Native enabled-counter proof covers complete backend executable sections and relocations separately for PROFILE1 and DIAG1; extracted actual-C test covers all four flag combinations.'])
 write(OUT/'result.json',result)
 text='Backend wire-counter cleanup\n\nOnly gfx_gpu.c changes from load-submit-overlap-v1. All 28 diagnostic-only updates are gated by SM64_PROFILE || SM64_PERF_DIAG. Counter-off getter returns four zeros; g_vc_kick and all renderer state remain unchanged. No LTO or build-option change. Reverse source reconstruction is byte-exact.\n\nNative normal object/ELF contain no diagnostic counter symbols or references. Enabled PROFILE1 and DIAG1 backend executable sections/relocations each exactly match the parent. Four-configuration extracted actual-C test passes 100,000 update/reset iterations each, including unsigned wrap.\n\nNormal/fault/absent/timeout/full-pool actual-ELF functional checks each preserve 721 scene/geometry/cull records and 8 ordered render streams. Parent LOAD counters, source-pool outcomes and prepared-batch ownership observers are exact; 64,175 deferred batches per mode. Pointer-hash probe totals change with linked asset addresses (+1544 normal/fault, -1885 fullpool over the full corpus); no upload/hit/fallback/recycle count changes. An initial over-strict seal assertion requiring identical probe counts was rejected before writing any result; this qualification is explicit. This is not 721 RTL-rendered frames.\n\nSynthetic gameplay timing removes 12,846 instructions/frame but adds 7,156 modeled cycles/frame due to ELF-layout/cache/branch effects; matched actual coupled measurement remains required. No speed improvement, Pocket FPS, timing closure or 25FPS claim. No production/sibling/SD changes.\n'
 with(OUT/'REPORT.txt').open('x')as f:f.write(text)
 shutil.copy2(Path(__file__),OUT/'seal-recipe.py')
 paths=[p for p in OUT.rglob('*')if p.is_file() and p.suffix not in('.o',) and p.name!='manifest.json']
 # Bind APP inputs/evidence without duplicating its inherited full SM64 tree.
 paths += [APP/n for n in INPUTS]+[APP/n for n in ('app.elf','source.json','candidate.patch','recipe.py','harness-stage.json','build-normal.json','build-normal.log','build-normal-invocation.json','build-normal-recipe.py','counter-proof/result.json','counter-proof/counter_test.c','codegen/result.json','codegen/normal-gfx_gpu.asm')]
 paths += [p for p in(APP/'codegen').iterdir()if p.suffix in('.o','.c','.log')]
 paths += [OLDTEST/'manifest.json']+[Path(__file__).with_name(n)for n in ('phase10_backend_wire_counters.py','phase10_backend_wire_codegen.py','phase10_backend_wire_functional.py','phase10_backend_wire_seal.py')]
 write(OUT/'manifest.json',dict(passed=True,qualified_for_coupled_measurement=True,deployment_ready=False,elf_sha256=elf,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(paths))}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
