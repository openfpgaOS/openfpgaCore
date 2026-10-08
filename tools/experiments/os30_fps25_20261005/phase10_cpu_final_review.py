#!/usr/bin/env python3
"""Independent source/provenance review; no synthesis or production mutation."""
from pathlib import Path
import hashlib,importlib.util,json,re,shutil
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-final-independent-review-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
evidence={}
def bind(p):evidence[str(p.relative_to(ROOT))]=sha(p);return json.loads(p.read_text()) if p.suffix=='.json' else p.read_text()
def manifest(d):
 p=P/d/'manifest.json';v=bind(p)
 for n,h in v['files'].items():assert sha(p.parent/n)==h,(d,n)
 return v
for n in ['cpu-ready-factor-v1','cpu-int-wb-factor-v1','cpu-fp-rs2-mux-v1','cpu-final-paths-v1','cpu-os30-repro-v4']:manifest(n)
spec=importlib.util.spec_from_file_location('iwb',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_int_wb_factor.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
base=bind(P/'cpu-ready-factor-v1/VexiiRiscv.v');final=bind(P/'cpu-final-paths-v1/VexiiRiscv.v');old=bind(P/'cpu-fp-rs2-mux-v1/original-assignment.txt').rstrip();new=bind(P/'cpu-fp-rs2-mux-v1/candidate-assignment.txt').rstrip()
assert base.count(m.OLD)==1 and base.count(old)==1
assert base.replace(m.OLD,m.NEW).replace(old,new)==final
assert base.replace(old,new).replace(m.OLD,m.NEW)==final
assert final.replace(new,old).replace(m.NEW,m.OLD)==base
assert re.findall(r'^\s*reg\s+[^;]*;',base,re.M)==re.findall(r'^\s*reg\s+[^;]*;',final,re.M)
assert re.findall(r'^\s*(?:parameter|localparam)\s+[^;]*;',base,re.M)==re.findall(r'^\s*(?:parameter|localparam)\s+[^;]*;',final,re.M)
v3=P/'cpu-os30-repro-v3/proposed';v4=P/'cpu-os30-repro-v4/proposed';changed=[]
assert sorted(str(p.relative_to(v3)) for p in v3.rglob('*') if p.is_file())==sorted(str(p.relative_to(v4)) for p in v4.rglob('*') if p.is_file())
for p in v4.rglob('*'):
 if p.is_file() and p.read_bytes()!=(v3/p.relative_to(v4)).read_bytes():changed.append(str(p.relative_to(v4)))
assert changed==['src/fpga/vendor/vexriscv/factor_os30_frontend.pl']
f=bind(v4/changed[0]);assert "my $after='"+sha(P/'cpu-final-paths-v1/VexiiRiscv.v')+"';" in f
for marker,want in [('INTWBOLD',m.OLD),('INTWBNEW',m.NEW),('RS2OLD',old),('RS2NEW',new)]:
 actual=re.search(r"my \$(?:old|new)=<<'"+marker+r"';\n(.*?)\n"+marker+r'\n',f,re.S)[1];assert actual==want,marker
for n in ['native/result.json','native/extra/result.json','native/combined-coverage.json']:
 v=bind(P/'cpu-final-paths-v1'/n);assert v['passed']
assert len(json.loads((P/'cpu-final-paths-v1/native/result.json').read_text())['runs'])==6
assert len(json.loads((P/'cpu-final-paths-v1/native/extra/result.json').read_text())['runs'])==4
assert bind(P/'cpu-os30-repro-v4/checks/result.json')['passed']
O.mkdir(exist_ok=False)
report='''Independent combined CPU and checked-regeneration source review

PASS: final CPU022f46fbbb0834bba1499df65ecdda79ecf13447fb33eff85effcb3bbda1e73d is exactly the ready-factor726cb parent with the separately qualified integer formatted-DATA gate and FP-RS2 selector substitutions. Both substitution orders produce the same bytes; reversing both restores the parent byte-for-byte. All component manifests and frozen CPU hashes match.

The integer formatter zero-preserving property applies for every width/sign combination: no source hits gives raw0 and formatted0. Lane qualification remains on DATA; architectural hits, wb_valid, write_valid, commit/cancel and issue are untouched. FP-RS2 still selects the earliest matching stage4..12, then register file; local priority groups are disjoint and maintain global priority. No state bits, registers, latency or arithmetic semantics are added. All register declarations and parameter/localparam text are unchanged. Ports, clock/reset logic, caches, ISA, predictors, FPU arithmetic and original issue/control/valid/write assignments remain byte-identical outside these data expressions. The already-proved ready-only equation is inherited unchanged, and has live reference assertions.

The final combined native proof contains ten exact reference/candidate UART-and-cycle pairs: frontend, integer/FPU/atomic/trap/cache stress, native lighting, exhaustive halfword handling, and directed FP RAW/WAW/FMA/FDIV/traps, each under quiet and active scanout schedules. Original ready, RS2, RS3, integer DATA and decoder assertions run together. This review verifies the sealed results and source binding; it does not repeat those simulations.

The checked cpu-os30-repro-v4 proposal differs from v3 in exactly one file, factor_os30_frontend.pl: two literal, uniquely-matching data substitutions plus final expected SHA. Its before-hash, os30-only invocation, ready topology guards, atomic temporary-file replacement and failure behavior remain unchanged. Other proposed files, cache/FPU/generator flags and hook order are identical. Existing bounded generator-flow/freshness/failure checks pass. Actual Scala regeneration is a separate required gate; this review does not substitute the captured-flow test for it.

Additional requested read-only GPU audit finds no blocker in v12/v13. Clamp threshold L=floor(abs(old)/4)-1 modulo2^30 uses bit29 only to exclude a zero-threshold underflow; positive new N<=L and negative ones-complement(N-1)<L both reproduce N<floor(abs(old)/4), including INT_MIN bounds. Q29 uses the same exact zero/sign test. dv_attr_onehot initializes on the only first-attribute entry and advances with the only dv_attr increment; loops stop at3/6/9. Every owned derivative read follows initialization and all field widths/order match. Unowned/invalid attributes10..15 differ from the former default and are outside the equivalence contract; separate live ownership/native GPU proof remains required.

No production files, RTL candidates, physical options or artifacts were modified. No map, fitter, timing closure or physical Pocket performance claim follows from this review.
'''
(O/'REPORT.txt').write_text(report);shutil.copy2(__file__,O/'recipe.py')
review={'passed':True,'candidate_sha256':sha(P/'cpu-final-paths-v1/VexiiRiscv.v'),'exact_two_data_substitution_composition':True,'unchanged_registers_parameters_ports_clocks':True,'architectural_valid_write_issue_unchanged':True,'ready_factor_inherited_unchanged':True,'checked_proposal_only_one_helper_changed':True,'native_pairs':10,'actual_Scala_regeneration_separate_gate':True,'findings':[],'evidence_sha256':evidence}
(O/'review.json').write_text(json.dumps(review,indent=2)+'\n');(O/'manifest.json').write_text(json.dumps({p.name:sha(p) for p in O.iterdir() if p.is_file()},indent=2)+'\n');print(sha(O/'manifest.json'))
