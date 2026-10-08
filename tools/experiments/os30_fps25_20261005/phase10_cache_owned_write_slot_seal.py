#!/usr/bin/env python3
"""Seal accepted-AW slot ownership and twelve exact protocol comparisons."""
import hashlib,json,re,shutil
from pathlib import Path
import phase10_cache_owned_write_slot as recipe
R,P,O=recipe.R,recipe.P,recipe.O
T=R/'tools/experiments/os30_fps25_20261005'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')

old=(O/'baseline.sv').read_text();new=(O/'gpu_color_depth_cache.sv').read_text()
assert recipe.transform(old)==new
assert len(re.findall(r'\bslot\s*<=',old))==len(re.findall(r'\bslot\s*<=',new))==6
assert sha(O/'baseline.sv')=='a88bf7835c4f8f7fc78328a3d34c5994b1fe881ea724a96b0f5fb214c30c432d'
assert sha(O/'gpu_color_depth_cache.sv')=='ab1f1aeeb135cd2f80e44adb39616c35f28f1e164ab639b2f5645fbb447000ae'
p=json.loads((O/'protocol/result.json').read_text());assert p['passed']
rows=[r for runs in p['runs'].values() for r in runs];assert len(rows)==12
keys=('checks','accepted_aw','speculative_aw','slot_differences','unowned_reads','unowned_outputs','owned_slots','owned_outputs','memory_reads','memory_writes')
assert all(all(r[k]>0 for k in keys) for r in rows)
previous=json.loads((P/'render-cache-speculative-read-v1/protocol/result.json').read_text())
assert p['configurations']==previous['configurations']
for name,runs in p['runs'].items():
 for a,b in zip(runs,previous['runs'][name]):
  for k in ('seed','cycles','memory_reads','memory_writes','checks'):
   assert a[k]==b[k],(name,k,a[k],b[k])
proof='''Accepted-AW slot ownership

Source reconstruction proves the only difference is relocating the one
IDLE AW slot writer from its cache-hit branch into that same accepted-AW
branch, and selecting with the owned AW address. All five other slot
writers, other registers, state transitions, RAM layout, read/write muxes
and external handshakes retain exact source bytes.

AW acceptance remains state==IDLE, !flush_req, s_awvalid (s_awready is
state==IDLE && !flush_req, independent of lookup). With s_awvalid true,
lookup_addr==s_awaddr for either REGISTER_READ_LOOKUP setting. Thus the
direct hot0-valid/tag comparison and hot0/hot1 selection equal hot_slot
for every input, including both tags matching and invalid hot entries.
Reset and flush priority are unchanged.

On a genuine hot write, both versions capture the same slot at the same
edge and enter the same W_GET/W_WAIT state. Otherwise the new captured
value remains unowned: a cache miss enters LOOK, which chooses its RAM
read address from selected independently of slot, then overwrites slot
before allocation/writeback/data handling. BY states use addr and bus
data, never slot. On leaving bypass, a later read/hot write/LOOK/dirty
maintenance operation captures the proper slot before any consumer.
Maintenance uses walk until it captures slot=walk before WB_FIRST.
WB_FIRST consumes slot to issue the correct RAM read but not the preceding
unowned data_q. Its result is available before writeback data is consumed.
Reset sets both slots to zero. Unused default RAM reads may differ while
the speculative slot remains unowned; every actual write is identical.

The miter tracks only a specifically accepted AW that the original would
not use to write slot. Every slot difference requires that epoch and the
exact captured candidate value; all six source writers are represented.
All slot-consumer states require equality. Differing read addresses must
be the unowned default {slot,word_index}; a RAM-output difference requires
that exact preceding read mismatch and is forbidden in every data
consumer or RAM-write cycle. State, request bookkeeping, hot entries,
victim/metadata updates, and all owned bus responses/requests remain exact.
Each of the twelve runs must exercise speculative captures, slot and RAM
differences, and owned consumers. The existing independent memory
scoreboard, partial writes, flush, bypass, backpressure, range boundaries,
all banks/ways, reset, and hostile read/write collision poisoning remain.
The host is byte-identical to the prior speculative-read proof, and every
run has identical total cycles and memory transaction counts.

No architectural register/cycle, synthesis-area or routed-timing claim
follows from this component proof. Native composition and CAD are pending.
'''
(O/'ownership.txt').write_text(proof)
put(O/'result.json',dict(passed=True,source_sha256=sha(O/'gpu_color_depth_cache.sv'),parent_sha256=sha(O/'baseline.sv'),literal_parent_reconstruction=True,slot_writers=6,unchanged_other_slot_writers=5,added_registers=0,added_cycles=0,protocol_runs=12,totals={k:sum(r[k] for r in rows)for k in keys},same_host_and_cycle_counts_as_prior=True,unowned_difference='Tracked accepted non-hot AW candidate slot only; unconsumed default RAM addresses/outputs may differ. All consumer slots/data, state, actual writes and owned bus signals are exact.',native_full_renderer_pending=True,physical_timing_qualification=False,production_modified=False))
shutil.copy2(__file__,O/'seal.py')
files=[p for p in O.rglob('*')if p.is_file()]+[Path(__file__),Path(recipe.__file__),T/'phase10_cache_owned_write_slot_tests.py',P/'render-cache-speculative-read-v1/manifest.json',P/'render-cache-speculative-read-v1/protocol/main.cpp',P/'render-cache-speculative-read-v1/protocol/result.json',P/'parallel-control-paths-v1/manifest.json',P/'parallel-control-paths-v1/mapped-cones.json']
put(O/'manifest.json',dict(passed=True,quartus_invoked=False,production_modified=False,native_full_renderer_pending=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in sorted(set(files))}))
print(json.dumps(json.loads((O/'result.json').read_text()),indent=2));print('MANIFEST',sha(O/'manifest.json'))
