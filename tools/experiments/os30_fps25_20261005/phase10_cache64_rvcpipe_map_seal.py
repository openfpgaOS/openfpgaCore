#!/usr/bin/env python3
"""Seal the isolated RVC pipeline synthesis resource gate; no physical timing claim."""
import json,pathlib,re
import phase10_cache64_rvcpipe_map as m
from phase10_lod5_queues_map import entities as original_entities
R,P,O,BASE=m.R,m.P,m.O,m.BASE
sha,put=m.sha,m.put
d=m.checked();assert json.loads((O/'map-core/completion.json').read_text())['passed']
a=next((BASE/'map-core/job').rglob('ap_core.map.rpt'))
b=next((O/'map-core/job').rglob('ap_core.map.rpt'));wd=b.parent.parent
assert not list((O/'map-core/job').rglob('*.fit.rpt'))
assert sha(wd/'ap_core.qsf')==sha(a.parent.parent/'ap_core.qsf')
assert sha(O/'native-map-gate.json')==json.loads((O/'map-core/prepared.json').read_text())['native_map_gate_sha256']
def entities(p):
 out=original_entities(p)
 for line in p.read_text(errors='replace').split('; Analysis & Synthesis Resource Utilization by Entity')[1].splitlines():
  c=[x.strip() for x in line.split(';')][1:-1]
  if len(c)>=10 and c[-2]=='audio_mixer':out[c[-2]]=dict(aluts=c[1],registers=c[2],memory_bits=c[3],dsp=c[4],path=c[-3])
 return out
def totals(p):
 s=p.read_text(errors='replace').split('; Analysis & Synthesis Resource Usage Summary',1)[1].split('; Analysis & Synthesis Resource Utilization by Entity',1)[0]
 keys={'estimated_alms':'Estimate of Logic utilization (ALMs needed)','logic_aluts':'Combinational ALUT usage for logic','memory_aluts':'Memory ALUT usage','registers':'Dedicated logic registers','mlab_bits':'Total MLAB memory bits','block_memory_bits':'Total block memory bits','dsps':'Total DSP Blocks'}
 return {k:int(re.search(r';\s*'+re.escape(v)+r'\s*;\s*([\d,]+)',s).group(1).replace(',','')) for k,v in keys.items()}
ea,eb=entities(a),entities(b);assert ea.keys()==eb.keys()
delta={n:{k:int(eb[n][k].split()[0].replace(',',''))-int(ea[n][k].split()[0].replace(',','')) for k in ('aluts','registers','memory_bits','dsp')} for n in ea}
ta,tb=totals(a),totals(b)
assert ta['block_memory_bits']==tb['block_memory_bits'] and ta['dsps']==tb['dsps']
result=dict(passed_map=True,full_fit_attempted=False,changed_synthesis_sources=d['changed_sources'],candidate_cpu_sha256=d['component_cpu_sha256'],cpu_i_kib=64,cpu_d_kib=64,gpu_capacity_kib=8,effective_qsf_identical=True,seed=2,configured_mhz=100,added_frontend_cycles=1,peak_issue_width=1,cycle_equivalence_claim=False,baseline_entities=ea,candidate_entities=eb,entity_delta=delta,baseline_totals=ta,candidate_totals=tb,total_delta={k:tb[k]-ta[k] for k in ta},physical_m10ks=None,fitted_alms=None,fmax_mhz=None,production_modified=False,deployment_ready=False,limits=['Synthesis estimates do not establish actual placed ALM/LAB packing or whole-core timing.','This adds one frontend stage and changes branch/redirect latency; the native renderer measurements are separately bound, not claimed cycle-equivalent.','The CPU is the sole source difference from the previous cache64 timing composition. GPU8 and independently equivalent GPU/audio factors are retained.','Directed IRQ/trap/flush validation and physical qualification are separate gates; no fitter authorization follows from this resource report.'])
put(O/'result.json',result)
files=[pathlib.Path(__file__),pathlib.Path(m.__file__),O/'prepared.json',O/'candidate.patch',O/'recipe.py',O/'result.json',O/'native-map-gate.json',O/'map-core/prepared.json',O/'map-core/completion.json',O/'map-core/map.log',a,b,wd/'ap_core.qsf']+[O/'frozen'/x for x in d['source_sha256']]
put(O/'manifest.json',dict(passed_map=True,full_fit_attempted=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
print(json.dumps({'entity_delta':delta,'total_delta':result['total_delta']},indent=2));print('MANIFEST',sha(O/'manifest.json'))
