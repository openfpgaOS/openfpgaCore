#!/usr/bin/env python3
"""Reconcile passive backend costs and reuse; no application/RTL mutation."""
import csv,hashlib,json,re,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'selected-backend-profile-v2';D=O/'run-warm';B=P/'load-submit-coupled-v1/run-warm';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
for f in ['coupled.json','scene.csv','geometry.csv','culling.csv','geometry-operations.csv']+[f'frame_{i:05}.bin' for i in range(360,368)]:assert (B/f).read_bytes()==(D/f).read_bytes(),f
rows={int(r['pc'],16):{k:int(v) for k,v in r.items() if k!='pc'} for r in csv.DictReader((D/'backend-pc.csv').open())}
asm={int(m[1],16):m[2].strip() for s in (O/'indexed.asm').read_text().splitlines() if (m:=re.match(r'^([0-9a-f]+):\s+(.*)$',s))}
ranges=[('entry_material_call',[0x104dc680,0x104dc6e0]),('job_decode_popcount',[0x104dc6e0,0x104dc71e]),('reserve_available',[0x104dc71e,0x104dc752]),('ring_poll_wait',[0x104dc874,0x104dc8dc]),('prepared_packet_emit',[0x104dc8dc,0x104dca28]),('draw_commit_counters_kick',[0x104dca28,0x104dca70]),('loop_exit',[0x104dca70,0x104dca98]),('flush_or_kick_paths',[0x104dca98,0x104dd356]),('viewport_first_state',[0x104dd356,0x104dd5b0]),('other_reserve_flush',[0x104dc752,0x104dc874])]
groups={}
for name,(lo,hi) in ranges:
 rr=[v for pc,v in rows.items() if lo<=pc<hi];groups[name]={k:sum(r[k] for r in rr) for k in next(iter(rows.values()))};groups[name]['ms_per_frame']=groups[name]['cyc']/800000
assert sum(v['cyc'] for v in groups.values())==sum(r['cyc'] for r in rows.values())
def metrics(pcs):
 rr=[rows[pc] for pc in pcs if pc in rows];return dict(instructions=sum(r['count'] for r in rr),cycles=sum(r['cyc'] for r in rr),ms_per_frame=sum(r['cyc'] for r in rr)/800000,pcs=[f'{pc:08x}' for pc in pcs if pc in rows])
diag=[0x104dc8e2,0x104dc90a,0x104dc90e,0x104dc918,0x104dc926,0x104dc92a,0x104dc978,0x104dc97c,0x104dc986,0x104dc988,0x104dc998,0x104dc9e4,0x104dc9f4,0x104dc9f6,0x104dca02,0x104dca28,0x104dca40,0x104dca4a,0x104dca50,0x104dca60]
# These four operations reconstruct the original already-packed15bit draw index.
repack=[0x104dca2c,0x104dca2e,0x104dca34,0x104dca38]
# Source reads and loop overhead of the selected CPU-ring copies; preserve all MMIO stores.
copyranges=[(0x104dcc90,0x104dcde8),(0x104dcea0,0x104dcfe8),(0x104dd030,0x104dd188)]
copy_nonstores=[pc for pc in rows if any(a<=pc<b for a,b in copyranges) and not asm[pc].startswith('sw\t')]
copy_stores=[pc for pc in rows if any(a<=pc<b for a,b in copyranges) and asm[pc].startswith('sw\t')]
packetstores=[pc for pc in rows if 0x104dc8ec<=pc<0x104dca28 and asm[pc].startswith('sw\t') and '(gp)' not in asm[pc]]+[0x104dca4c,0x104dca54]
pollsdiag=[0x104dc894,0x104dc898,0x104dc89a,0x104dc8c0,0x104dc8c4]
getprof=lambda p:{r['function']:r for r in csv.DictReader(p.open(),delimiter='\t')};lo=getprof(D/'profile-0360.tsv');hi=getprof(D/'profile.tsv');f='gfx_gpu_vtx_cache_batch';cycles=int(hi[f]['self_cycles'])-int(lo[f]['self_cycles']);assert cycles==sum(r['cyc'] for r in rows.values())
reuse=next(csv.DictReader((D/'backend-reuse.csv').open()));reuse={k:int(v) for k,v in reuse.items()};hist=list(csv.DictReader((D/'backend-hist.csv').open()))
x=dict(passed=True,selected_parent_manifest_sha256=sha(P/'load-submit-coupled-v1/manifest.json'),all8_buffers_and_exact_cycles=True,backend_ms_per_frame=cycles/800000,exclusive_groups=groups,prepared_reuse=reuse,batch_mean=reuse['triangles']/reuse['batches'],diagnostic_wire_counter_only=metrics(diag),packed_draw_index_reconstruction=metrics(repack),ring_minimum_free_diagnostic=metrics(pollsdiag),CPU_ring_copy_nonstore_upperbound=metrics(copy_nonstores),CPU_ring_copy_required_MMIO_stores=metrics(copy_stores),prepared_packet_stores=metrics(packetstores),count_histogram=hist,limitations=['PC costs are selected qsim timing attribution with actual GPU/LOAD/SDRAM/CRAM RTL; not native deviceFPS.','Deleting wait-loop instructions may only increase polling frequency: current attributed waitcost is not removable preparation.','Direct FIFO emission still needs all MMIO store cycles; nonstore+staging costs are an optimistic scheduling-independent ceiling.','Prepared reuse tracks each slot generation after G_VTX returns, allowing adjacent-submit overlap to consume the preceding generation; zero same-generation repeats found.','No app/RTL behavior changed.'])
(O/'result.json').write_text(json.dumps(x,indent=2)+'\n');shutil.copy2(__file__,O/'analysis-recipe.py');shutil.copy2(ROOT/'tools/experiments/os30_fps25_20261005/phase10_backend_profile.py',O/'observer-recipe.py')
print(json.dumps({k:v for k,v in x.items() if k not in ['exclusive_groups','count_histogram','limitations']},indent=2));print('GROUPS',[(k,round(v['ms_per_frame'],6)) for k,v in groups.items()])
