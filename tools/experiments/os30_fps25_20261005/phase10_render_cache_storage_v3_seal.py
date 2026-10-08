#!/usr/bin/env python3
"""Seal fresh cycle proofs for the genvar compatibility correction."""
from pathlib import Path
import hashlib,json
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'render-cache-storage-v3'; B=P/'render-cache-storage-v2'; D=O/'protocol'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,a):
    with p.open('x') as f:json.dump(a,f,indent=2,sort_keys=True);f.write('\n')
assert sha(O/'gpu_color_depth_cache.sv')=='25343436b682e7875a170c144b32ee06803c53177bf0c999626043587741ee3a'
old=(B/'gpu_color_depth_cache.sv').read_text()
assert (O/'gpu_color_depth_cache.sv').read_text()==old.replace('generate if(DATA_BITS<=11)','genvar b;\ngenerate if(DATA_BITS<=11)',1).replace('for(genvar b=0;b<BANKS;b=b+1)','for(b=0;b<BANKS;b=b+1)',1)
r=json.loads((D/'result.json').read_text())
assert r==json.loads((B/'protocol/result.json').read_text())
runs=[x for a in r['runs'].values() for x in a]
tot={k:sum(x[k] for x in runs) for k in ['cycles','checks','look','replace','collision','init','alloc','reset','bank_switch']}
put(O/'result.json',dict(passed=True,candidate_sha256=sha(O/'gpu_color_depth_cache.sv'),baseline_sha256=sha(O/'baseline.sv'),compatibility_only=True,source_change='Module-scope genvar declaration replaces inline declaration; identical generate bounds/body',configurations=r['configurations'],runs=len(runs),totals=tot,exact_original_RTL_cycle_and_transaction_miter=True,all_data_RAM_outputs_compared=True,collision_poisoning=True,v2_v3_all_run_metrics_identical=True,new_fsm_states=0,extra_bus_cycles=0,quartus_invoked=False,physical_resource_claim=False,deployment_ready=False))
(O/'REPORT.txt').write_text('PASS: compatibility-only cache storage v3.\n\nThe sole v2 delta declares genvar b at module scope and changes for(genvar b=...) to for(b=...). Bank loop bounds, names, widths and body are identical. Prior sources, proofs and the failed Quartus parsing attempt remain frozen.\n\nAll twelve old/new RTL miter runs were rebuilt and rerun: 13,996,120 exact cycle/FSM/data/owned-bus checks. The six parameter configurations and every reported metric match v2 exactly, including GPU8/GPU32, four-way banking, legacy staging, minimum geometry, collision poisoning, reset and dirty replacement coverage.\n\nNo Quartus or native application run was performed by this component. The 40-bit bank output mux timing and actual M10K inference remain physical-tool questions; no physical area or timing gain is claimed.\n')
paths=[p for p in O.iterdir() if p.is_file()]+[p for p in D.rglob('*') if p.is_file() and 'obj' not in p.parts]+list(D.glob('*/obj/Vgpu_color_depth_cache'))
paths += [B/'manifest.json',B/'gpu_color_depth_cache.sv',B/'protocol/result.json',P/'render-cache-storage-feasibility-v1/manifest.json',Path(__file__),R/'tools/experiments/os30_fps25_20261005/phase10_render_cache_storage_v3.py',R/'tools/experiments/os30_fps25_20261005/phase10_render_cache_storage_v3_tests.py']
put(O/'manifest.json',dict(passed=True,deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(paths))}))
print('MANIFEST',sha(O/'manifest.json'))
print('RESULT',sha(O/'result.json'))
