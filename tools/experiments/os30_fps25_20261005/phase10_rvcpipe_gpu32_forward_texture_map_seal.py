#!/usr/bin/env python3
"""Seal final GPU32/CPU-forward/owned-texture composition resources; no physical timing claim."""
import json,pathlib,re
import phase10_rvcpipe_gpu32_forward_texture_map as m
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
  if len(c)>=10 and c[-2] in ('audio_mixer','gpu_color_depth_cache','gpu_tex_cache'):
   out[c[-2]]=dict(aluts=c[1],registers=c[2],memory_bits=c[3],dsp=c[4],path=c[-3])
 return out
def totals(p):
 s=p.read_text(errors='replace').split('; Analysis & Synthesis Resource Usage Summary',1)[1].split('; Analysis & Synthesis Resource Utilization by Entity',1)[0]
 keys={'estimated_alms':'Estimate of Logic utilization (ALMs needed)','logic_aluts':'Combinational ALUT usage for logic','memory_aluts':'Memory ALUT usage','registers':'Dedicated logic registers','mlab_bits':'Total MLAB memory bits','block_memory_bits':'Total block memory bits','dsps':'Total DSP Blocks'}
 return {k:int(re.search(r';\s*'+re.escape(v)+r'\s*;\s*([\d,]+)',s).group(1).replace(',','')) for k,v in keys.items()}
def cache_rams(p):
 text=p.read_text(errors='replace');rows=[]
 for line in text.split('; Analysis & Synthesis RAM Summary',1)[1].split('; State Machines',1)[0].splitlines():
  c=[x.strip() for x in line.split(';')][1:-1]
  if len(c)==9 and 'gpu_color_depth_cache:gpu_render_cache|' in c[0] and c[2]=='Simple Dual Port':
   rows.append(dict(name=c[0],type=c[1],mode=c[2],depth_a=int(c[3]),width_a=int(c[4]),depth_b=int(c[5]),width_b=int(c[6]),bits=int(c[7])))
 assert rows;return rows
ea,eb=entities(a),entities(b);assert ea.keys()==eb.keys()
delta={n:{k:int(eb[n][k].split()[0].replace(',',''))-int(ea[n][k].split()[0].replace(',','')) for k in ('aluts','registers','memory_bits','dsp')} for n in ea}
ta,tb=totals(a),totals(b);rams=cache_rams(b)
data=[r for r in rams if 'data_banked.bank[' in r['name']]
victim=[r for r in rams if '|altsyncram:victim_rtl_' in r['name']]
old32=next((P/'cache64-gpu32-map-v1/map-core/job').rglob('ap_core.map.rpt'))
old32cache=entities(old32)['gpu_color_depth_cache']
result=dict(passed_map=True,full_fit_attempted=False,changed_synthesis_sources=d['changed_sources'],candidate_cpu_sha256=d['component_cpu_sha256'],candidate_render_cache_sha256=d['component_render_cache_sha256'],candidate_texture_sha256=d['component_texture_sha256'],cpu_i_kib=64,cpu_d_kib=64,gpu_capacity_kib=32,effective_qsf_identical=True,seed=2,configured_mhz=100,storage_cycle_equivalence_passed=True,cpu_forwarding_native_cycle_change=0,texture_native_cycle_change=0,baseline_entities=ea,candidate_entities=eb,entity_delta=delta,baseline_totals=ta,candidate_totals=tb,total_delta={k:tb[k]-ta[k] for k in ta},baseline_render_cache_rams=cache_rams(a),candidate_render_cache_rams=rams,data_bank_count=len(data),data_banks_inferred_2048x40=(len(data)==4 and all(r['depth_a']==r['depth_b']==2048 and r['width_a']==r['width_b']==40 for r in data)),data_bank_reported_types=[r['type'] for r in data],victim_ram_count=len(victim),historical_monolithic_gpu32_cache_entity=old32cache,physical_m10ks=None,fitted_alms=None,fmax_mhz=None,production_modified=False,deployment_ready=False,limits=['Synthesis RAM shapes and ALM estimates do not establish fitted M10K/LAB count, ALM packing, or timing.','Compared to the latest CPU-forward/GPU32-storage-v3 map, only the reused texture-owned-read address factor changes. Current native byte and cycle equivalence is independently bound.','Historical monolithic GPU32 entity is from a different whole-CPU placement context; use as an explicitly historical per-entity resource comparison, not an isolated full-core delta.','No fitter authorization follows from this resource report.'])
put(O/'result.json',result)
files=[pathlib.Path(__file__),pathlib.Path(m.__file__),O/'prepared.json',O/'candidate.patch',O/'recipe.py',O/'result.json',O/'native-map-gate.json',O/'map-core/prepared.json',O/'map-core/completion.json',O/'map-core/map.log',a,b,old32,wd/'ap_core.qsf']+[O/'frozen'/x for x in d['source_sha256']]
put(O/'manifest.json',dict(passed_map=True,full_fit_attempted=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}))
print(json.dumps({'entity_delta':delta,'total_delta':result['total_delta'],'data_banks_inferred_2048x40':result['data_banks_inferred_2048x40'],'victim_ram_count':len(victim),'rams':rams},indent=2));print('MANIFEST',sha(O/'manifest.json'))
