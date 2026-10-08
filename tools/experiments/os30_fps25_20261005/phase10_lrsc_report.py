#!/usr/bin/env python3
"""Seal the unsuccessful private LR/SC-only whole-core resource experiment."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/lrsc-only-v1'
FIT=OUT/'fit-narrow-seed2'
BASE=OUT.parent/'vcache-narrow-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,x):
    with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
tests=read(OUT/'cpu-tests/result.json');regression=read(OUT/'regression/result.json');isa=read(OUT/'isa-contract-audit.json')
assert tests['passed'] and regression['passed'] and isa['passed']
assert all(r['lrsc_cycles']==234265 and r['amo_checks']==144 for r in tests['runs'])
assert all(r['uart_and_component_cycles_exact'] and r['whole_probe_cycles_exact'] for r in regression['runs'])
correction=read(OUT/'cpu-test-count-correction.json')
assert correction['original_result_sha256']==sha(OUT/'cpu-tests/result.json') and correction['actual_iterations_per_run']==4608
control=read(OUT/'control/generated.json');candidate=read(OUT/'candidate/generated.json')
assert control['retained_A_exact'] and sha(OUT/'control/VexiiRiscv.v')==control['rtl_sha256']
assert sha(OUT/'candidate/VexiiRiscv.v')==candidate['rtl_sha256']
for label in ['control','candidate']:
 for p,h in read(OUT/label/'sources.json').items():assert sha(OUT/label/'source'/p)==h,p
f=read(FIT/'results/pocket-os30-s2/result.json');assert f['valid'] and f['state']=='complete'
ft=(FIT/'results/pocket-os30-s2/ap_core.fit.rpt').read_text(errors='replace')
def num(pattern):return int(re.search(pattern,ft).group(1).replace(',',''))
metrics=dict(alms_needed=f['alms'],placed_alms=num(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)'),labs=num(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)'),m10ks=f['m10ks'],dsps=num(r';\s*Total DSP Blocks\s*;\s*([\d,]+)'),worst_slack_ns=f['worst'])
st=(FIT/'results/pocket-os30-s2/ap_core.sta.rpt').read_text(errors='replace')
fm=re.findall(r';\s*([\d.]+) MHz\s*;\s*[\d.]+ MHz\s*;\s*ic\|mp_ram\|',st);assert len(fm)==2
metrics['main_fmax_mhz']=dict(slow85=float(fm[0]),slow0=float(fm[1]))
bm=read(BASE/'result.json')['metrics']
delta={k:metrics[k]-bm[k] for k in ['alms_needed','placed_alms','labs','m10ks','dsps']}
frozen=read(FIT/'frozen-sources.json');old=read(BASE/'fit-seed2/frozen-sources.json')
cpu='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
assert [p for p in frozen if frozen[p]!=old[p]]==[cpu]
for p,h in frozen.items():assert sha(FIT/'frozen'/p)==h,p
entity={}
for line in ft.split('; Fitter Resource Utilization by Entity')[1].splitlines():
 r=[s.strip() for s in line.split(';')][1:-1]
 if len(r)>16 and r[-2] in ['VexiiRiscv','cpu_system','gpu_core']:
  entity[r[-2]]=dict(alms_needed=float(r[1].split()[0]),placed_alms=float(r[2].split()[0]),aluts=int(r[6].split()[0]),registers=int(r[7].split()[0]))
result=dict(passed_component_correctness=True,recommended_for_adoption=False,reason='No useful whole-core area saving at saved seed2; ISA compatibility reduction is not justified.',private_only=True,deployed=False,whole_core_fit_successful=True,full_100mhz_timing_closed=False,seed=2,seed_sweep=False,control_cpu_sha256=control['rtl_sha256'],candidate_cpu_sha256=candidate['rtl_sha256'],metrics=metrics,delta_from_narrow_cache=delta,entities=entity,lrsc_iterations_per_run=4608,timed_lrsc_cycles_baseline_and_candidate=234265,arithmetic_amo_checks_per_run=144,regression_runs=6,regression_cycles_exact=True,isa_contract=isa['contract'],limitations=['Static compatibility applies to four exact audited ELFs only, not future applications or general RV32A software.','Clears misa.A and traps all nine arithmetic word AMO operations; LR/SC remains implemented.','No full application CPU/GPU execution or Pocket test was performed for this private CPU variant.','One saved seed measures this fitted design; logical removal does not guarantee resource savings after packing.','The successful narrower vertex cache remains independently selectable with the original CPU.'])
save(OUT/'result.json',result)
(OUT/'REPORT.txt').write_text('''Private LR/SC-only CPU resource experiment — do not adopt

The private generator adds --lsu-lrsc-only: preserve LR/SC and reservation
semantics, remove arithmetic AMO decode/ALU, and clear misa.A. The unchanged
control regenerates retained A exactly after provenance-comment normalization.
The candidate is a reduced ISA; it is not generally compatible with RV32A.

Actual CPU and retained memory RTL pass all four aq/rl LR/SC forms, success,
failure and intervening-store reservation invalidation. Each run performs
512 warm plus4096 timed iterations (4608 total; the earlier metadata typo is
preserved and explicitly corrected in cpu-test-count-correction.json).
Timed LR/SC work is exactly234265 cycles for A and candidate, with scanout
both inactive and active. All144 arithmetic AMO cases execute correctly on A
and precisely trap as illegal on the candidate without register/memory writes.
Six existing integer/FPU/SM64/vector/cache/frontend probes preserve UART,
result hashes and cycle counts exactly. The exact F11 normal/diagnostic,
Quake2 and retained OS ELFs contain LR/SC but no arithmetic AMO or misa access.
This closed static audit is not a guarantee for future or dynamically loaded
programs; production ISA metadata/policy would need updating before adoption.

One saved-seed2 whole-core fit uses the validated narrow vertex cache and
changes only the CPU RTL. Results:17210 needed ALMs,18023 placed ALMs,
1846/1848 LABs,302/308 M10Ks,42 DSPs. Versus narrow cache with original CPU:
+126 needed ALMs,+18 placed,+2 LABs,no BRAM/DSP change. Vexii itself has121
fewer ALUTs and84.8 fewer placed ALMs, but whole-design packing erases the
saving; its needed share falls only2.6 ALMs. This is not a useful space lever.

Timing is90.11 MHz slow85 /92.15 MHz slow0; worst setup−1.098 ns, hold+0.077,
recovery+4.018, removal+0.393. It does not close100 MHz and has a worse minimum
than the narrow-cache original-CPU91.58 MHz. No sweep, production source,
application package or SD change. Keep the original CPU for integration.
''')
evidence={}
for p in OUT.rglob('*'):
 if not p.is_file():continue
 parts=p.relative_to(OUT).parts
 if any(a in parts for a in ['source','obj','frozen','jobs']):continue
 if p.name in ['final-manifest.json','.driver.lock']:continue
 evidence[str(p.relative_to(ROOT))]=sha(p)
for label in ['control','candidate']:
 for rel,h in read(OUT/label/'sources.json').items():evidence[str((OUT/label/'source'/rel).relative_to(ROOT))]=h
for p,h in frozen.items():evidence[str((FIT/'frozen'/p).relative_to(ROOT))]=h
for name in ['phase10_lrsc_only.py','phase10_lrsc_cpu.py','phase10_lrsc_regression.py','phase10_lrsc_fit.py','phase10_lrsc_report.py']:
 p=Path(__file__).with_name(name);evidence[str(p.relative_to(ROOT))]=sha(p)
for p in [BASE/'final-manifest.json',OUT.parent/'resource-audit/atomics/result.json',OUT/'cpu-model/obj/Vtb_system']:
 evidence[str(p.relative_to(ROOT))]=sha(p)
save(OUT/'final-manifest.json',dict(passed_component_correctness=True,recommended_for_adoption=False,evidence_sha256=evidence))
print(json.dumps(dict(metrics=metrics,delta=delta,manifest_sha256=sha(OUT/'final-manifest.json'))))

