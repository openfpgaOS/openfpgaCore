#!/usr/bin/env python3
"""Bind the private shared-FMA culling feasibility evidence."""
from pathlib import Path
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=BASE/'float-cull';TOOLS=ROOT/'tools/experiments/os30_fps25_20261005'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
rtl=OUT/'rtl';rtl.mkdir()
for n in ['phase10_float_cull.v','phase10_float_cull_probe.v']:shutil.copy2(TOOLS/n,rtl/n)
shutil.copy2(BASE/'fpu-feasibility/rtl/GeometryFmaProbe.v',rtl/'GeometryFmaProbe.v')
checks={n:json.loads((OUT/(n+'-result.json')).read_text()) for n in ['native','random']}
assert all(r['mismatches']==0 and r['unsupported']==0 for r in checks.values())
fits={};files=[Path(__file__),*rtl.iterdir()]
for part in ['controller','combined']:
 d=OUT/('fit-'+part);s=(d/'output_files/probe.fit.summary').read_text();t=(d/'output_files/probe.sta.rpt').read_text()
 fits[part]={'alms':int(re.search(r'Logic utilization \(in ALMs\) : ([\d,]+)',s)[1].replace(',','')),'dsps':int(re.search(r'Total DSP Blocks : (\d+)',s)[1]),'memory_bits':int(re.search(r'Total block memory bits : ([\d,]+)',s)[1].replace(',','')),'worst_reported_fmax_mhz':min(float(v) for v in re.findall(r';\s+([0-9.]+) MHz\s+;\s+[0-9.]+ MHz\s+;\s+clk',t))}
 files += [d/n for n in ['probe.qsf','timing.sdc','output_files/probe.fit.summary','output_files/probe.fit.rpt','output_files/probe.sta.rpt']];files+=list(d.glob('*.v'))
files += [TOOLS/n for n in ['phase10_float_cull.v','phase10_float_cull_probe.v','phase10_float_cull_native.cpp','phase10_float_cull_vectors.cpp']]
files += [OUT/n for n in ['native-result.json','random-result.json','random-inputs.json','random-vectors.txt','F11-gfx_cpu_indexed_run.asm']]
files += [BASE/'cpu-audit/cull-native'/n for n in ['result.json','vectors.txt','manifest.json','kernel.c','kernel.asm','prepare.py','run.py','analyze.py']]
result={'passed_feasibility_gates':True,'deployable':False,'production_changed':False,'physical_fps_measured':False,'checks':checks,'standalone_fits':fits,
 'contract':'One external exclusive arithmetic port;9 operations per arithmetic triangle. Center NDC uses two rounded multiplies; four differences preserve original fused multiply-subtract; cross uses two rounded multiplies plus subtract with original W-parity order. Clip-AND has priority, cullboth and nocull short circuit. Partial-clip OR is preserved, not processed. Four phases take38cycles; trivial cases1cycle; ready/valid result holds under backpressure.',
 'native_scope':'920 mode-tagged triples assembled from230 fresh actual-A transformed vertices, plus128 controls. Separate retained-A CPU inline-assembly operation probe enforces original F11 DAG. These are not replayed original display-list triangle calls.',
 'fit_scope':'Standalone saved-seed2 virtual-pin fits. Combined includes one424ALM-class FMA primitive; controller alone has no DSP. Do not sum the two rows or interpret difference as guaranteed integrated marginal area.',
 'rtl_sha256':{p.name:sha(p) for p in rtl.iterdir()},'evidence_sha256':{str(p.relative_to(ROOT)):sha(p) for p in files},
 'remaining':['Shared-port arbitration and integrated vertex-state ownership.','Full partial clipping/exception fallback ordering.','Raw-DMA/rasterizer integration, full command/image equivalence.','Whole-os30 fit/timing and coupled performance validation.']}
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(OUT/'REPORT.txt').write_text('''Exact shared-FMA triangle rejection (private feasibility)

All1,048 native CPU operation cases and24,000 independent333-bit-oracle
random/control cases pass, including crossfloat bits, rejectclass andclipOR.
75,142 output-backpressure cycles retain the full result correctly.

The controller owns an external FMA port; it contains no additional DSP.
Nine arithmetic operations preserve the F11 compiled cull order, including
its four fused differences. Precomputing rounded NDC for all vertices would
change those differences. This implementation avoids that approximation.
Arithmetic triangles take38cycles; trivial rejection/disabled cull takes1.

Standalone saved-seed2 fits:
  Controller alone449ALM, noDSP/RAM, worst228.05MHz.
  Controller plusFMA715ALM,1DSP, noRAM, worst116.29MHz.
Rows overlap and must not be added. Integrated placement remains unproven.

The920 native cases use assembled triples of230 native-transformed vertices,
not original display-list triangle calls. A separate retained-A inline-asm
probe preserves the original instructions and repeats byte-exact under
quiet and nominal traffic.128 additional control cases cover winding,
clip-AND priority, cull modes and partial clipOR propagation.

Partial clipping is only reported, not implemented. Arithmetic exceptions
must request an ordered fallback. DMA/state ownership, arbitration,
rendering equivalence, integrated fitting and FPS remain unqualified.
No shipping source or package changed.
''')
print(json.dumps({'passed':True,'fits':fits,'rtl_sha256':result['rtl_sha256']},indent=2))
