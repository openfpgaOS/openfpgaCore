#!/usr/bin/env python3
"""Seal actual-bus geometry integration and the failed private full-core fit."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-direct-core-v1';FIT=OUT/'fit-seed2';TEST=P/'gpu-direct-axi-native-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
prep=read(OUT/'prepared.json');t=read(TEST/'result.json');assert t['passed']
for p,h in prep['source_sha256'].items():
 assert sha(OUT/'frozen'/p)==h,p
 assert sha(FIT/'frozen'/p)==h,p
f=read(FIT/'results/pocket-os30-s2/result.json');assert not f['valid'] and f['returncodes']=={'map':0,'fit':3}
report=FIT/'jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.fit.rpt'
s=report.read_text(errors='replace')
def number(pattern):return int(re.search(pattern,s).group(1).replace(',',''))
metrics=dict(alms_needed=number(r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)'),reported_placed_estimate=number(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)'),labs_required=number(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)'),m10ks_preliminary=number(r';\s*M10K blocks\s*;\s*([\d,]+)'),dsps=number(r';\s*Total DSP Blocks\s*;\s*([\d,]+)'))
assert metrics==dict(alms_needed=19828,reported_placed_estimate=19415,labs_required=2016,m10ks_preliminary=307,dsps=45)
entities=[]
for line in s.split('; Fitter Resource Utilization by Entity')[1].splitlines():
 r=[x.strip() for x in line.split(';')][1:-1]
 if len(r)>=16 and (r[-2].startswith('geometry_') or r[-2] in ['GeometryFmaProbe','raw_vtx_dma','gpu_core','axi_periph_slave','axi_sdram_arbiter']):
  entities.append(dict(entity=r[-2],path=r[-3],alms_needed_estimate=float(r[1].split()[0]),exclusive_alms_estimate=float(re.search(r'\((.*?)\)',r[1]).group(1)),placed_estimate=float(r[2].split()[0]),aluts=int(r[6].split()[0]),registers=int(r[7].split()[0])))
save(OUT/'result.json',dict(passed_component_correctness=True,whole_core_fit_successful=False,recommended_for_deployment=False,private_only=True,deployed=False,seed=2,seed_sweep=False,metrics=metrics,over_capacity=dict(alms=1348,labs=168),delta_vs_narrow=dict(alms_needed=2744,labs_required=172),entities=entities,actual_bus_native=t,limitations=['No successful placement, STA/Fmax or bitstream; failed-fit packing counts are preliminary.','Actual bus/native test includes real AXI peripheral, GDR1 and GPU command parser/fence but behavioral M2 memory and no material state/fragments.','Twelve separate full captured GPU raster replays are exact through the new ring transport.','230 native vertices include145 independently valid RGB references; other native RGB bytes masked.','No production CPU/core/software/OS/SD files changed.']))
(OUT/'REPORT.txt').write_text('''Private complete GPU-direct frontend: functional checks pass; whole os30 does not fit

Original CPU and validated narrow GPU vertex cache retained. Raw LOAD, exact
culling, bounded original-Gfx PRECHECK, and atomic ordered0x56/0x54 publication
are integrated at0x4B with M2 read-only traffic. CPU material/state publication
precedes the geometry commit. No result DMA or accepted-path CPU readback.
New ring transport preserves GPU consumer concurrency, holds all CPU GPU
MMIO writes while owned, and discards the unpublished batch on abort.

Actual AXI-peripheral + GDR1 + actual GPU parser/fence test passes120 LOADs,
1947 vertices (230 native,145 independently valid RGB references),480 RUNs,
6098 draws,32320 independently checked packet words and30720 raw readbacks.
This fixture deliberately installs no material state, so no fragments draw.
Separately,12 full captured-frame replays through the new ring producer match
the frozen narrow GPU's complete color/depth/fence outputs under three memory
traffic schedules. Native M2 responder latency is behavioral, not a measured
whole-game hardware performance result.

One saved-seed2 whole-core compile passes synthesis but fails fitter capacity:
19828 needed ALMs /18480 (1348 excess);2016 LABs /1848 (168 excess);
307 preliminary M10Ks /308;45 DSPs. Needed ALMs increase2744 versus the
validated narrow-cache baseline17084. No final placement, Fmax or bitstream.

Geometry subtree estimate2538.8 ALMs: exclusive coordinator401.8;
raw frontend1416.4 (including FMA320.4, metadata/controller251.2, raw LOAD781.0,
cull controller63.9); commit422.2 (including formatter204.1);
RUN DMA/precheck298.4. Values are from the failed preplacement fit, not
guaranteed additive removal savings. Detailed hierarchy is inresult.json.

All work remains private. No seed sweep, production or SD mutation.
''')
# The bus test binds all actual sources through build.json; preserve its own seal.
ev={str(p.relative_to(ROOT)):sha(p) for p in TEST.iterdir() if p.is_file()}
q=Path(__file__).with_name('phase10_gpu_direct_axi_test.py');ev[str(q.relative_to(ROOT))]=sha(q)
save(TEST/'final-manifest.json',dict(passed=True,scope=t['scope'],evidence_sha256=ev))
ev={}
for p,h in prep['source_sha256'].items():ev[str((OUT/'frozen'/p).relative_to(ROOT))]=h
for directory in [OUT,FIT/'results']:
 iterator=directory.iterdir() if directory==OUT else directory.rglob('*')
 for p in iterator:
  if p.is_file() and p.name!='final-manifest.json':ev[str(p.relative_to(ROOT))]=sha(p)
for p in report.parent.glob('ap_core.*'):
 if p.suffix in ['.rpt','.summary']:ev[str(p.relative_to(ROOT))]=sha(p)
for p in [FIT/'frozen-sources.json',FIT/'inventory.json',FIT/'experiment.json',FIT/'results.json',TEST/'final-manifest.json',P/'gpu-direct-ring-v1/final-manifest.json',P/'gpu-direct-ring-replay-v1/final-manifest.json']:
 ev[str(p.relative_to(ROOT))]=sha(p)
for n in ['phase10_gpu_direct_fit.py','phase10_gpu_direct_report.py']:
 p=Path(__file__).with_name(n);ev[str(p.relative_to(ROOT))]=sha(p)
save(OUT/'final-manifest.json',dict(passed_component_correctness=True,whole_core_fit_successful=False,recommended_for_deployment=False,evidence_sha256=ev))
print(json.dumps(dict(core_manifest_sha256=sha(OUT/'final-manifest.json'),actual_bus_manifest_sha256=sha(TEST/'final-manifest.json'))))
