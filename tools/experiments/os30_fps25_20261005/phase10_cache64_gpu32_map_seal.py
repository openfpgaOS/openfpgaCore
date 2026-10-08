#!/usr/bin/env python3
"""Seal GPU32 synthesis/size evidence; physical packing remains unqualified."""
import json,pathlib,re
import phase10_cache64_gpu32_map as m
R,P,O,BASE=m.R,m.P,m.O,m.BASE
sha,put=m.sha,m.put
d=m.checked();completion=json.loads((O/'map-core/completion.json').read_text());assert completion['passed']
a=next((BASE/'map-core/job').rglob('ap_core.map.rpt'));b=next((O/'map-core/job').rglob('ap_core.map.rpt'));wd=b.parent.parent
assert not list((O/'map-core/job').rglob('*.fit.rpt')) and sha(wd/'ap_core.qsf')==sha(a.parent.parent/'ap_core.qsf')
def entities(p):
 out=m.entities(p)
 for line in p.read_text(errors='replace').split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
  c=[x.strip() for x in line.split(';')][1:-1]
  if len(c)>=10 and c[-2]=='gpu_color_depth_cache':out[c[-2]]=dict(aluts=c[1],registers=c[2],memory_bits=c[3],dsp=c[4],path=c[-3])
 return out
ea,eb=entities(a),entities(b);delta={n:{k:int(eb[n][k].split()[0].replace(',',''))-int(ea[n][k].split()[0].replace(',','')) for k in ('aluts','registers','memory_bits','dsp')} for n in ea}
def totals(p):
 text=p.read_text(errors='replace').split('; Analysis & Synthesis Resource Usage Summary',1)[1].split('; Analysis & Synthesis Resource Utilization by Entity',1)[0]
 keys={'estimated_alms':'Estimate of Logic utilization (ALMs needed)','logic_aluts':'Combinational ALUT usage for logic','memory_aluts':'Memory ALUT usage','registers':'Dedicated logic registers','mlab_bits':'Total MLAB memory bits','block_memory_bits':'Total block memory bits','dsps':'Total DSP Blocks'}
 return {k:int(re.search(r';\s*'+re.escape(v)+r'\s*;\s*([\d,]+)',text).group(1).replace(',','')) for k,v in keys.items()}
ta,tb=totals(a),totals(b)
def rams(p):
 out={}
 for l in p.read_text(errors='replace').splitlines():
  c=[x.strip() for x in l.split(';')][1:-1]
  if len(c)==9 and '|gpu_color_depth_cache:gpu_render_cache|altsyncram:' in c[0] and c[0].endswith('|ALTSYNCRAM'):
   n=c[0].split('|altsyncram:',1)[1].split('|',1)[0];out[n]=dict(type=c[1],mode=c[2],depth=int(c[3]),width=int(c[4]),bits=int(c[7]))
 return out
ra,rb=rams(a),rams(b);assert len(ra)==len(rb)==3
native=P/'native-renderer-render32-check-v1';n=json.loads((native/'result.json').read_text());assert n['passed']
result=dict(passed_map=True,full_fit_attempted=False,only_changed_synthesis_source=m.TOP,edit='gpu_render_cache SET_BITS6→8; all other parameters and CPU unchanged',cpu_sha256=d['cpu_sha256'],effective_qsf_identical=True,seed=2,configured_mhz=100,baseline_entities=ea,candidate_entities=eb,entity_delta=delta,baseline_totals=ta,candidate_totals=tb,total_delta={k:tb[k]-ta[k] for k in ta},baseline_render_rams=ra,candidate_render_rams=rb,native_cold_call_saving=n['saving'],native_comparison_manifest_sha256=sha(native/'manifest.json'),physical_m10ks=None,fitted_alms=None,fmax_mhz=None,packing_scenarios=dict(reference_fit_total_m10ks=279,reference_render_data_m10ks=8,reference_render_tag_m10ks=2,ideal_segmented_candidate_data_m10ks=32,ideal_project_total_if_other_packing_unchanged=303,full_depth_one_bit_candidate_data_m10ks=40,full_depth_project_total_if_other_packing_unchanged=311,device_m10ks=308,guaranteed=False),limits=['The current synthesis estimate increases by143ALMs; resource-fit feasibility is not proven.','Data RAM8192x40 may pack as segmented32blocks or a deeper40block organization; these are scenarios, not a compiler-selected/fitted count.','Tags become256x22 and are expected to remain one M10K each, but actual compiler packing remains unqualified.','The positive native result is one cold renderer call, not warm/full-frame/Pocket FPS.','Prior analytic GPU32 screen was a small positive1.77% gain with originalD128 and unfittable storage; it was not a slowdown.'],production_modified=False,deployment_ready=False)
put(O/'result.json',result)
files=[pathlib.Path(__file__),pathlib.Path(m.__file__),O/'prepared.json',O/'candidate.patch',O/'recipe.py',O/'result.json',O/'map-core/prepared.json',O/'map-core/completion.json',O/'map-core/map.log',a,b,wd/'ap_core.qsf',native/'manifest.json',native/'result.json',P/'cache64-v7-fit-v1-retry1/manifest.json']+[O/'frozen'/x for x in d['source_sha256']]
put(O/'manifest.json',dict(deployment_ready=False,full_fit_attempted=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}));print(json.dumps({k:result[k] for k in ('total_delta','native_cold_call_saving','packing_scenarios')},indent=2));print('MANIFEST',sha(O/'manifest.json'))
