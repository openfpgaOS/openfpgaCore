#!/usr/bin/env python3
"""Seal the private LOAD MMIO component tests and unsuccessful whole-core fit."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'raw-load-mmio-v2';FIT=OUT/'fit-seed2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
t=read(OUT/'tests/result.json');assert t['passed']
prepared=read(OUT/'prepared.json')
for p,h in prepared['source_sha256'].items():assert sha(OUT/'frozen'/p)==h,p
f=read(FIT/'results/pocket-os30-s2/result.json');assert f['state']=='failed' and not f['valid'] and f['returncodes']=={'map':0,'fit':3}
report=FIT/'jobs/pocket-os30-s2/src/fpga/targets/pocket/output_files/ap_core.fit.rpt';s=report.read_text(errors='replace')
def number(pattern):return int(re.search(pattern,s).group(1).replace(',',''))
metrics=dict(alms_needed=number(r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)'),reported_placed_estimate=number(r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)'),labs_required=number(r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)'),m10ks_preliminary=number(r';\s*M10K blocks\s*;\s*([\d,]+)'),dsps=number(r';\s*Total DSP Blocks\s*;\s*([\d,]+)'))
assert metrics==dict(alms_needed=19041,reported_placed_estimate=18645,labs_required=1935,m10ks_preliminary=306,dsps=45)
entities={}
for line in s.split('; Fitter Resource Utilization by Entity')[1].splitlines():
 r=[a.strip() for a in line.split(';')][1:-1]
 if len(r)>=16 and (r[-2] in ['geometry_load_mmio','geometry_raw_load','geometry_loaded_vertex_writer','GeometryFmaProbe','geometry_float_recip2','geometry_lighting_batch','geometry_color_rtz','axi_periph_slave','axi_sdram_arbiter']):
  entities[r[-2]]=dict(alms_needed_estimate=float(r[1].split()[0]),placed_estimate=float(r[2].split()[0]),aluts=int(r[6].split()[0]),registers=int(r[7].split()[0]))
save(OUT/'result.json',dict(passed_component_correctness=True,whole_core_fit_successful=False,recommended_for_deployment=False,private_only=True,deployed=False,seed=2,seed_sweep=False,metrics=metrics,entities=entities,over_capacity=dict(alms=561,labs=87),native_vertices=230,independent_native_RGB_vertices=145,mmio_tests=t,startup_por_verified=True,cpu_only_reset_preserves_inflight_job=True,limitations=['Resource counts are from an unsuccessful preplacement fit; there is no final placement, bitstream or timing result.','The raw loader infers3 M10Ks, but failed whole-fit306 count is preliminary and cannot establish final physical packing.','The test instantiates real axi_periph_slave and complete LOAD/writer RTL with randomized AXI responder; full memory/GPU contention is separate evidence.','145 native RGB references are independent; other native RGB bytes originated from external memo state and are excluded from equality claims.','No production application, core, OS or SD file was changed.','The selected software direction is a separate LOD3 stride64 experiment; this LOD2 packed36 design remains preserved unchanged.']))
(OUT/'REPORT.txt').write_text('''Private LOAD MMIO v2 — correctness passes; full os30 does not fit

The original CPU and validated narrow GPU cache are retained. A new0x4B
MMIO interface owns matrix/context/descriptors through raw vertex DMA,
transform/reciprocal/lighting and packed36-byte result DMA. Completion/success
appear only after the final AXI B response. Busy writes are ignored. Raw
failure prevents result writes; invalid result pointers issue no AW; BRESP
failure marks partial results uncommitted. There is no local abort.
Startup-only3clock POR initializes the new requester before its first bus
transaction. CPU-only reset does not cancel DMA; software must wait/quiesce
before reuse. Existing fabric reset remains connected. CPU cache ownership
and fog work remain the software adapter's responsibility.

Tests use the actual retained axi_periph_slave with randomized AR/R/AW/W/B
delays and CPU R stalls. They cover20 native jobs/230 vertices,2070 result
words,5120 debug words, twelve debug-read/final-B timing alignments, blocked
busy and partial writes, matrix and result bursts, invalid pointers, raw
compute/read/write errors, startup without external reset, CPU-only reset
during a job, and global reset/recovery. Native RGB is independently checked
for145 vertices; external memo-reference RGB is intentionally masked.

One saved-seed2 whole-core fit fails:1935 LABs required vs1848 (87 excess),
19041 needed ALMs vs18480 (561 excess),306 preliminary M10Ks,45 DSPs.
The geometry subtree is1546 ALMs by preplacement estimate: raw LOAD1179.8,
writer210.8, wrapper155.3. Raw arithmetic/storage infers3 M10Ks. Failed-fit
packing estimates are not a successful placement; there is no Fmax or
bitstream. The whole integration must shrink before it can be tested on
hardware. No sweep, production or SD change. LOD3 stride64 is separate.
''')
evidence={}
for folder in [OUT/'tests',FIT/'results']:
 for p in folder.rglob('*'):
  if p.is_file() and 'obj' not in p.relative_to(folder).parts:evidence[str(p.relative_to(ROOT))]=sha(p)
for p in OUT.iterdir():
 if p.is_file() and p.name!='final-manifest.json':evidence[str(p.relative_to(ROOT))]=sha(p)
for p in [FIT/'frozen-sources.json',FIT/'inventory.json',FIT/'experiment.json',FIT/'results.json']:
 evidence[str(p.relative_to(ROOT))]=sha(p)
for p in report.parent.glob('ap_core.*'):
 if p.suffix in ['.rpt','.summary']:evidence[str(p.relative_to(ROOT))]=sha(p)
for p,h in prepared['source_sha256'].items():
 assert sha(FIT/'frozen'/p)==h,p
 evidence[str((OUT/'frozen'/p).relative_to(ROOT))]=h
for name in ['phase10_load_mmio.py','phase10_load_mmio_dma.py','phase10_load_mmio_dma.v','phase10_load_mmio_dma_test.py','phase10_load_mmio_dma_test.cpp','phase10_load_mmio_report.py']:
 p=Path(__file__).with_name(name);evidence[str(p.relative_to(ROOT))]=sha(p)
save(OUT/'final-manifest.json',dict(passed_component_correctness=True,whole_core_fit_successful=False,recommended_for_deployment=False,evidence_sha256=evidence))
# LOD3 is a separate immutable component snapshot, not a successful FPGA fit.
v3=P/'raw-load-mmio-v3';p3=read(v3/'prepared.json');t3=read(v3/'tests/result.json');assert t3['passed'] and t3['range_jobs']==528
e3={}
for p,h in p3['source_sha256'].items():
 assert sha(v3/'frozen'/p)==h,p;e3[str((v3/'frozen'/p).relative_to(ROOT))]=h
for p in v3.rglob('*'):
 if p.is_file() and 'obj' not in p.relative_to(v3).parts and 'frozen' not in p.relative_to(v3).parts:e3[str(p.relative_to(ROOT))]=sha(p)
for name in ['phase10_load_mmio_stride.py','phase10_loaded_vertex_writer_stride.v','phase10_load_mmio_stride_test.py','phase10_load_mmio_stride_test.cpp']:
 p=Path(__file__).with_name(name);e3[str(p.relative_to(ROOT))]=sha(p)
save(v3/'component-manifest.json',dict(passed_component_correctness=True,whole_core_fit_successful=False,whole_core_fit_attempted=False,production_modified=False,scope='LOD3 fixed64byte outputstride;528count/dest combinations preserve padding; no software/cache/contention/FPS qualification.',evidence_sha256=e3))
print(json.dumps(dict(v2_manifest_sha256=sha(OUT/'final-manifest.json'),v3_manifest_sha256=sha(v3/'component-manifest.json'))))
