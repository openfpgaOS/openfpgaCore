#!/usr/bin/env python3
"""Seal the private RS2-only data selector change and bounded native proofs."""
from pathlib import Path
import difflib,hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-fp-rs2-mux-v1';BASE=P/'cpu-ready-factor-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
old=(O/'original-assignment.txt').read_text().rstrip();new=(O/'candidate-assignment.txt').read_text().rstrip();a=(BASE/'VexiiRiscv.v').read_text();b=(O/'VexiiRiscv.v').read_text();assert a.count(old)==1 and b==a.replace(old,new)
assert (O/'baseline.v').read_bytes()==(BASE/'VexiiRiscv.v').read_bytes()
for n in ['combinational/result.json','native/result.json','native/rs2-result.json','hazards/quiet-result.json','hazards/nominal-result.json','hazards/comparison.json']:assert json.loads((O/n).read_text())['passed'],n
n=json.loads((O/'native/rs2-result.json').read_text());h=[json.loads((O/'hazards'/f'{mode}-result.json').read_text()) for mode in ('quiet','nominal')]
rows=n['runs']+[x['coverage'] for x in h];mask=0
for x in rows:mask|=x['seen']
checks=sum(x['checks'] for x in rows);multiple=sum(x['multiple'] for x in rows)
assert mask==1023 and multiple>0
(O/'candidate.patch').write_text(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='ready-factor-v1',tofile='RS2-grouped-v1')))
save(O/'result.json',dict(passed=True,parent_sha256=sha(BASE/'VexiiRiscv.v'),candidate_sha256=sha(O/'VexiiRiscv.v'),exact_single_assignment_change=True,added_state_bits=0,added_cycles=0,combinational_checks=355328,live_checks=checks,live_seen_mask=hex(mask),simultaneous_matches=multiple,matched_native_programs=4,matched_native_runs=8,all_UART_and_cycles_equal=True,ready_factor_live_assertions_retained=True,RS3_live_assertions_retained=True,map_run=False,fit_run=False,regenerated=False))
save(O/'regeneration-hook.json',dict(proposed='Extend the already qualified FloatRegFile RS3 nine-stage grouping to RS2 only, preserving first enabled stage order4..12 and register-file fallback. Existing ready factor and all matching/valid/state must remain unchanged. Regeneration is owned separately; this result is a private exact emitted-RTL transform.',parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),actual_regeneration_performed=False))
(O/'REPORT.txt').write_text(f'''RS2 forwarding priority/data selector, private exact factor

Only execute_ctrl1_down_float_RS2_lane0 changes over the qualified ready-factor CPU. Nine forwarding stages remain ordered4..12, followed by the register-file default. Three local three-stage priority groups and one group-priority selector replace the original first-enabled onehot/payload tree, exactly as in the earlier RS3 trial. Matching predicates, all registers/enables, caches, arithmetic, ISA and architectural cycle behavior remain unchanged. The ready-factor and RS3 fixes are retained.

Exhaustive literal extracted RTL comparison passes355328 tests: all1024 enable vectors,320 payload bit-basis vectors,10 tagged floating-point-pattern rotations,16 random vectors and zero perselector, also against an independent first-enabled oracle. Opaque bit-select linearity plus all selector states establishes arbitrary binary payload equivalence. There is no four-state X equivalence claim.

Eight matched actual-CPU runs cover frontend decoding/hazards, integer/FPU/atomic/trap/cache stress,1227 native lighting results, and a directed RS2 RAW/WAW/FMA/FDIV chain with gaps0..12 and16 synchronous traps. Quiet and scanout memory schedules preserve byte-exact UART results and exact final cycle counts against the unchanged ready-factor CPU. Every live clock also compares original RS2 selector output to the candidate: {checks} checks, {multiple} simultaneous matching-stage observations, union selector mask{mask:#x} (all ten positions). Existing ready-factor, RS3 and decoder assertions remain enabled. Read/write/scanout/bus scoreboards are clean. The directed program independently expects208 exact0x41700000 results/run.

The initial firmware Docker invocation was sandbox-denied before compilation; its log is retained. A narrowly scoped approved retry compiled the pinned existing toolchain. This was not an RTL or correctness failure. No map, fitter, Scala regeneration, production edits or deployment occurred, and no area/Fmax improvement is claimed.
''')
shutil.copy2(__file__,O/'seal-recipe.py')
files=[]
for p in O.rglob('*'):
 if not p.is_file() or 'obj' in p.parts or p.suffix=='.o':continue
 if p.name=='manifest.json':continue
 # The native source tree is bound by prepared.json, independently verify it.
 if 'frozen' in p.parts:continue
 files.append(p)
prep=json.loads((O/'native/prepared.json').read_text())
for name,hsh in prep['sources_sha256'].items():assert sha(O/'native'/name)==hsh,name
save(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in sorted(files)},parent_manifest_sha256=sha(BASE/'manifest.json'),candidate_sha256=sha(O/'VexiiRiscv.v'),scope='RS2-only data mux factoring; exact binary proof and live native equivalence. No map/fit/regeneration.'))
print(json.dumps(json.loads((O/'result.json').read_text()),indent=2));print('manifest',sha(O/'manifest.json'))
