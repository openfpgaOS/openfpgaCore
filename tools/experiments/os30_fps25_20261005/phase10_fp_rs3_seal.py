#!/usr/bin/env python3
"""Recheck frozen RS3 sources and all proof logs before sealing the trial."""
import hashlib,json,re,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-fp-rs3-mux-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def clean(t):return re.sub(r'\n?\[c=\d+\][^\n]*\n    m1_ar:[^\n]*\n    arb_st=[^\n]*\n','',t.split('=== UART BEGIN ===')[0])
prepared=json.loads((O/'prepared.json').read_text())
assert sha(O/'baseline.v')==prepared['original_cpu_sha256']
assert sha(O/'VexiiRiscv.v')==prepared['candidate_cpu_sha256']
old=(O/'original-assignment.txt').read_text().rstrip('\n');new=(O/'candidate-assignment.txt').read_text().rstrip('\n')
assert (O/'baseline.v').read_text().replace(old,new)==(O/'VexiiRiscv.v').read_text()
comb=json.loads((O/'combinational/result.json').read_text());assert comb['passed'] and comb['selector_vectors']==1024 and comb['checks']==355328
runs={};seen=0
for kind in ('native','hazards'):
 for mode in ('quiet','nominal'):
  d=O/kind;t=(d/(mode+'.log')).read_text();cl=clean(t)
  assert 'KERNEL PASS HAL init' in cl and 'KERNEL FAIL' not in t and 'exit=0' in t
  assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in t
  assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0' in t
  if kind=='native':
   def rows(s):
    a=[tuple(int(x,16) for x in m.split()) for m in re.findall(r'LIGHT ((?:[0-9a-f]{8} ){3})',clean(s))]
    assert len(a)==1227 and [x[0] for x in a]==list(range(1227));return a
   assert rows(t)==rows((P/'cpu-audit/lighting-native'/f'{mode}.log').read_text());n=1227
  else:
   a=[tuple(int(x,16) for x in m) for m in re.findall(r'RS3_HAZARD ([0-9a-f]{8}) ([0-9a-f]{8})',cl)]
   assert a==[(i,0x41200000) for i in range(208)];n=208
  m=re.search(r'FP_RS3_COVER checks=(\d+) freezes=(\d+) redirects=(\d+) multiple=(\d+) seen=([0-9a-f]+)',t);assert m
  cv=dict(zip(('checks','freezes','redirects','multiple','seen'),[int(x,16 if i==4 else 10) for i,x in enumerate(m.groups())]))
  assert all(cv[k]>0 for k in ('checks','freezes','redirects'))
  if kind=='hazards':assert cv['multiple']>0
  seen|=cv['seen'];runs[kind+'_'+mode]=dict(exact_rows=n,coverage=cv,log_sha256=sha(d/(mode+'.log')))
for source,dest in [('phase10_fp_rs3_native.py','native/final-recipe.py'),('phase10_fp_rs3_hazards.py','hazards/final-recipe.py'),('phase10_fp_rs3_seal.py','seal-recipe.py')]:
 shutil.copy2(ROOT/'tools/experiments/os30_fps25_20261005'/source,O/dest)
generator=ROOT/'src/fpga/vendor/vexriscv/VexiiRiscv/src/main/scala/vexiiriscv/execute/ExecuteLanePlugin.scala'
build=ROOT/'src/fpga/vendor/vexriscv/generate_vexii.sh'
(O/'regeneration-hook.json').write_text(json.dumps(dict(generator_source=str(generator.relative_to(ROOT)),generator_sha256=sha(generator),build_source=str(build.relative_to(ROOT)),build_sha256=sha(build),
 proposed='If this timing trial is selected, add a checked local Scala generator patch under vendor/vexriscv/patches. Specialize only FloatRegFile/RS3 with9 sorted bypass stages into3 priority groups while keeping the original matching predicates and regfile default. The existing generate_vexii.sh source_patch loop applies or verifies every patches/*.patch before sbt. Generation must then be re-proven against this private source. No generator/build file was edited in this trial.',
 private_reproduction='phase10_fp_rs3_mux.py prepare/prove rewrites exactly one checked assignment in the frozen compactv2 CPU. It is not yet a production regeneration hook.'),indent=2)+'\n')
result=dict(passed=True,scope=prepared['scope'],candidate_sha256=sha(O/'VexiiRiscv.v'),source_replacement_only=True,added_state_bits=0,added_cycles=0,combinational=comb,native_runs=runs,total_live_reference_checks=sum(r['coverage']['checks'] for r in runs.values()),native_seen_mask=hex(seen),
 limits=['Native tests cover the observed forwarding stages, freezes, branch redirects and synchronous traps; exhaustive binary selector/bit-basis proof supplies all priority combinations, including unobserved stage matches.','Selection treats payloads as32 opaque bits; arithmetic, rounding and exception logic are unchanged. Four-state X-propagation is not claimed equivalent.','No map/fit/Fmax/performance qualification is claimed by this component proof.'],
 prior_lighting_oracle_manifest_sha256=sha(P/'cpu-audit/lighting-native/manifest.json'),no_cpu_regeneration=True,no_flags_changed=True)
(O/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(O/'REPORT.txt').write_text('Private RS3-only floating-point forwarding selector trial.\n\nThe original nine-stage plus register-file selection is factored into three local3-stage priority groups and one group-priority selector. Stage ordering, matching predicates, payload width and register-file default are unchanged. Only one combinational assignment is replaced; no register, enable, CPU ISA/cache/FPU setting, arithmetic or cycle changes.\n\nExhaustive actual-RTL proof compares literal extracted original logic and the candidate against an independent first-enabled oracle: all1024 enable vectors with320 payload basis vectors,10 tagged FP-pattern rotations,16 random vectors and zero perselector;355328 comparisons pass. Because the selectors operate independently on opaque payload bits, this covers arbitrary binary payload values. No four-state X equivalence claim.\n\nActual selected CPU replay compares the original RS3 expression directly every live clock:3068320 checks pass across four runs. The1227-vector native lighting corpus matches all earlier retained-A outputs under quiet/nominal SDRAM traffic (seenmask0x294; no simultaneousmatches in that corpus). A separate directed test performs repeated writes to the same FPdestination, RAWFMA chains, FDIV freeze, gap0..12, branch loops and16 synchronous traps;208 independently expected exactresults/run,150 simultaneousmatches/run,mask0x3b3. Union0x3b7 leaves two native stage positions unobserved, covered by exhaustive selectionproof. Bus/write scoreboards remain clean.\n\nTest-development limitations are retained: initial host finalization assumption failed before modelbuild; the first native analysis demanded a multi-match not present in ordinary lighting. UART status interleaving is removed using the pre-existing native parser pattern. Directed testing then supplies the missing priority overlap. Docker sandbox refusal is retained alongside successful narrowly scoped compilation. None was an RTL equivalence failure.\n\nSource-generation hook is documented in regeneration-hook.json: a selected fix should enter the existing checked Scala patch loop before sbt. This trial changes no production generator or RTL and makes no map/Fmax claim.\n')
files={}
for p in sorted(O.rglob('*')):
 if not p.is_file() or p.name=='manifest.json':continue
 rel=p.relative_to(O)
 if any(x.startswith('obj') or x=='__pycache__' or x=='native-initial-host-final-error' for x in rel.parts):continue
 files[str(rel)]=sha(p)
(O/'manifest.json').write_text(json.dumps(files,indent=2)+'\n')
print('sealed',sha(O/'manifest.json'),'live checks',result['total_live_reference_checks'])
