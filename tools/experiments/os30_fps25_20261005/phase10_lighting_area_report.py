#!/usr/bin/env python3
"""Freeze the selected shared-encoder/operand-register lighting revision."""
from pathlib import Path
import hashlib,json,re,shutil
ROOT=Path(__file__).resolve().parents[3]
TOOLS=ROOT/'tools/experiments/os30_fps25_20261005'
BASE=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=BASE/'lighting-area/pipelined-v3';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
checks={key:json.loads((OUT/(key+'-result.json')).read_text()) for key in ['native','combined','raw']}
assert checks['native']['mismatches']==checks['native']['unsupported_jobs']==checks['native']['oracle_mismatches_vs_CPU']==0
assert checks['combined']['passed'] and checks['raw']['passed']
assert all(v==7*int(n)+54 for n,v in checks['native']['cycles_by_count'].items())
old=json.loads((BASE/'transform-light/v2/result.json').read_text())
cycle_delta={n:v-old['cycles_by_vertex_count'][n] for n,v in checks['combined']['cycles_by_vertex_count'].items()}
assert all(0<=v<=5 for v in cycle_delta.values())
fits={};files=[Path(__file__)]
for key,d in [('baseline_raw',BASE/'raw-load-v1/fit-v1'),('rejected_shared_unregistered',BASE/'lighting-area/shared-v2/fit'),('selected_raw',OUT/'fit-raw')]:
 s=(d/'output_files/ap_core.fit.summary').read_text();t=(d/'output_files/ap_core.sta.rpt').read_text()
 fits[key]={'alms':int(re.search(r'Logic utilization \(in ALMs\) : ([\d,]+)',s)[1].replace(',','')),'dsps':int(re.search(r'Total DSP Blocks : (\d+)',s)[1]),'ram_blocks':int(re.search(r'Total RAM Blocks : (\d+)',s)[1]),'worst_reported_fmax_mhz':min(float(v) for v in re.findall(r';\s+([0-9.]+) MHz\s+;\s+[0-9.]+ MHz\s+;\s+clk',t))}
 fits[key]['scope']='Transform+light arithmetic only; no rawloader' if key=='rejected_shared_unregistered' else 'Complete raw-load component'
 files += [d/n for n in ['ap_core.qsf','timing.sdc','output_files/ap_core.fit.summary','output_files/ap_core.fit.rpt','output_files/ap_core.sta.rpt']];files+=list(d.glob('*.v'))
source=TOOLS/'phase10_lighting/geometry_lighting_batch_pipelined.v'
shutil.copy2(source,OUT/'geometry_lighting_batch.v')
files += [source,TOOLS/'phase10_lighting/geometry_lighting_batch_shared.v',TOOLS/'phase10_lighting/test.cpp',TOOLS/'phase10_lighting/test_top.v',TOOLS/'phase10_float_batch/transform_light_test.cpp']
files += [OUT/(key+'-result.json') for key in checks]
files += [BASE/'raw-load-v1'/n for n in ['native.bin','phase10_raw_load_test.cpp','manifest.json','result.json']]
files += [BASE/'cpu-audit/lighting-native'/n for n in ['native-golden.txt','result.json']]
files += [BASE/'lighting-area/sta-raw-v1'/n for n in ['paths.tcl','setup-paths.rpt']]
result={'passed_component_gates':True,'deployable':False,'production_changed':False,'physical_fps_measured':False,'source_sha256':sha(source),'checks':checks,'component_fits':fits,
 'contract':'Same ports and exact F11 lighting operations as original. Share one normal/diffuse byte encoder plus one ambient encoder. Register FMA operands andvalid for onecycle. FMA tags extend7to8stages; drain waits for pending registered FMAvalid. Lighting cycles7*N+54 instead7*N+49; one operation eachclock throughput unchanged.',
 'integrated_cycle_delta_by_count':cycle_delta,
 'critical_path_evidence':'Original rawloader worst12setup paths: registered normalbyte through lighting encoder and FMA operand mux into FMA firststage exponent,11.431nsdata at85C. Private detailedSTA copy is retained.',
 'scope':'Standalone whole raw-load component, not the whole os30 FPGA. Native/light/random/DMA tests retain original exactness scope; no render/FPS deployment claim.',
 'remaining':['Parent integration of selected controller and optional small colour converter.','Triangle/material/clipping ownership and GPU scheduling.','Whole-os30 area/timing and coupled performance qualification.'],
 'evidence_sha256':{str(p.relative_to(ROOT)):sha(p) for p in files}}
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
(OUT/'REPORT.txt').write_text('''Selected lighting area/timing revision (private component)

Sharing the byte encoders alone reduced area but lengthened the critical
normal-to-FMA path and failed timing. The selected revision adds an operand
register after the shared encoders and aligns all tags and drain conditions.

Complete raw-loader saved-seed2 standalone fit:
  Baseline2030ALM,2M10K,3DSP,84.20MHz worst reported Fmax.
  Selected1757ALM,2M10K,3DSP,98.18MHz worst reported Fmax.
This is a273ALM reduction and resolves the observed normal-to-FMA timing path.
It is not an integrated os30 fit or proof of100MHz timing closure.

All1227 retained-A lighting vectors pass, with5655total lighting vertices.
The combined random test passes23992coordinates,5998RGB and5998reciprocals,
including20resets. All164rawDMA cases pass, including230native vertices,
145independent nativeRGB results,2128synthetic vertices anderror recovery.

Lighting latency changes from7*N+49 to7*N+54. In the combined test it adds
atmost fivecycles forN<=15; reciprocal overlap hides it atN>=16.
The source at geometry_lighting_batch.v is frozen and hash-bound inresult.json.
No shipping core, package or SD card changed.
''')
print(json.dumps({'passed':True,'source_sha256':result['source_sha256'],'component_fits':fits},indent=2))
