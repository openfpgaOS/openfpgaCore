#!/usr/bin/env python3
"""Seal full-strobe wait shortcut, no Quartus invocation."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OUT=P/'cache-write-speculate-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 source=OUT/'gpu_color_depth_cache.sv'
 assert sha(source)=='e42f06c03b8b2aa8cfc69d330c937f51fe7283bc15d9f1b034b657dbeed7afe0'
 result=json.loads((OUT/'protocol/result.json').read_text())
 extra=json.loads((OUT/'protocol/full-wait-result.json').read_text())
 assert result['passed'] and result['default_cycle_identical'] and extra['passed']
 totals={}
 for r in extra['runs']:
  if '/enabled/' not in r['log'] and '/ranged/' not in r['log']:continue
  for k,v in r['counts'].items():totals[k]=totals.get(k,0)+v
 report=f'''Full-word acceptance during existing W_WAIT (private v2)

Source SHA256 {sha(source)}
Parent: cache-write-speculate-v1, SHA256
0777f44dd1be0cca3443ca2ed57d436f3f81f75bf882c9956cf04505f0d297ef

PASS all12 component runs: original-v1 default, v2 default, v2 enabled
CACHE_ALL, and v2 enabled with address ranges, each seeds1/2/3. Default0
preserves exact cycles and memory transactions against v1 default0. Enabled
mode intentionally changes W handshake timing, and random stimuli consume
RNG per clock; enabled aggregate cycle counts are NOT a performance comparison.

The only new acceptance is SPECULATE_WRITE_HOT0 && state==W_WAIT &&
s_wstrb==15. It uses the original W_GET storage, bounds and burst-advance code.
Default next state is explicitly W_GET, so an accepted non-final same-line
beat cannot accidentally remain in W_WAIT. Full strobes overwrite all40 bits
with {{4'hf,4'hf,s_wdata}}, independently of stale/poisoned data_q. No extra
register, RAM, DSP, state or storage port is introduced. Likely logic cost is
one4-bit full-strobe test and shared state/accept qualification; exact ALUT or
Fmax requires later synthesis/fit and is not asserted here.

Both W_WAIT predecessors are safe:
- Hot1 IDLE: accepted AW registered addr/slot/left; tag banks used accepted AW
  on that edge. active_meta therefore belongs to the owned line despite data_q
  holding speculative hot0 data. bounds_fresh=1 selects its original bounds.
- WCLEAR: ALLOC committed the owned tag/bounds before clearing the line. All
  clear beats are finished on entering W_WAIT, and metadata has been stable
  for the complete clear. Even the final-word read/write collision may be
  poisoned; a full beat does not consume that word. Registered slot/addr owns
  both RAM writes and metadata. Partial, zero-strobe or absent W still waits
  one cycle and obtains the correct existing word before entering W_GET.

For a remaining same-line burst, accepting in W_WAIT prefetches next-word data
on the same edge, ready for W_GET. Line crossing retains LOOK_WAIT. A final
beat emits B exactly once. Existing dirty bounds, byte merge, WLAST checks,
flush ownership, posted write hazards and reset behavior are reused unchanged.
READY depends on WSTRB only in W_WAIT; AXI upstream must hold W payload stable
while VALID is asserted, as in the existing producer. No new AW/W coupling or
new combinational dependency on downstream write READY is introduced.

Extra directed campaign:72 cases per configuration cover both predecessors,
first/last words,1/2/17-word bursts, masks0/5/15, and first W present/absent
during W_WAIT. First W can precede/concur with AW. Later AW/AR inputs are
poisoned and flush contends during the owned wait. The independent byte memory
scoreboard verifies untouched bytes and dirty-writeback bounds after reads
and flush. Existing randomized, posted, cross-range, collision-poison and
precise W_WAIT-reset cases remain enabled.
Enabled aggregate observer counts:
  full hot1 acceptances {totals['hot']}
  full post-clear acceptances {totals['clear']}
  partial wait cycles {totals['partial']}
  zero-strobe wait cycles {totals['zero']}
  absent-W wait cycles {totals['absent']}.
Every fast beat separately asserts full40 data, owned valid tag and dirty
metadata; every partial/zero beat asserts READY remains low in W_WAIT.

No dual-port RAM change was made. Current compact-v3 report maps the render
data array as2048x40 simple-dual-port using8 M10Ks. Reusing its write port for
an independent hot1 read would require a different true-dual-port inference,
an additional40-bit result/selection path and read-during-write ownership
proof; no unchanged resource/timing guarantee follows from bit capacity alone.
The approved full-word shortcut avoids those changes.

Limits: component functional/protocol proof only. Existing v1 control, v4
composition, app, sibling directories, SDK and SD remain unchanged. No
Quartus command or whole-frame performance claim. Independent coupled review
will determine whether the shortcut recovers the observed v1 latency cost.
'''
 with (OUT/'REPORT.txt').open('x') as f:f.write(report)
 files=[p for p in OUT.rglob('*') if p.is_file() and 'obj' not in p.parts]
 files += list((OUT/'protocol').glob('*/obj/Vgpu_color_depth_cache'))
 files += [P/'cache-write-speculate-v1/manifest.json',HERE/'phase10_cache_full_write.py',
  HERE/'phase10_cache_full_write_seal.py',HERE/'phase10_cache_write_speculate.py']
 manifest=dict(passed=True,source_sha256=sha(source),default_cycle_identical=True,
  component_runs=12,new_register_bits=0,new_states=0,new_ram_bits=0,quartus_invoked=False,
  coupled_qualified=False,coverage=totals,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p) for p in sorted(set(files))})
 with (OUT/'manifest.json').open('x') as f:json.dump(manifest,f,indent=2);f.write('\n')
 print(OUT/'manifest.json');print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
