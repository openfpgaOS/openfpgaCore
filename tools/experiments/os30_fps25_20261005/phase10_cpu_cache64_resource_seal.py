#!/usr/bin/env python3
"""Seal cache-only generation and synthesis; no physical or speed claim."""
import hashlib,json,pathlib,re,sys
import phase10_cpu_cache64_resource as m
from phase10_lod5_queues_map import entities
R,P,O,BASE=m.R,m.P,m.O,m.BASE
sha=m.sha;put=m.put
prep=m.guard();q=json.loads((O/'qualification.json').read_text());regen=json.loads((O/'regeneration.json').read_text());assert regen['passed']
assert sha(O/'VexiiRiscv.v')==regen['actual_sha256']==q['candidate_cpu_sha256']
parentinputs=json.loads((P/'cpu-os30-regenerate-v5/prepared.json').read_text())['generator_inputs_sha256'];assert parentinputs.keys()==prep['generator_inputs_sha256'].keys()
assert [n for n in parentinputs if parentinputs[n]!=prep['generator_inputs_sha256'][n]]==['configs/os30.cfg']
for c in q['chain']:
 a=(m.OLD/'proposed'/m.REL/c['helper']).read_text();b=(O/'vendor'/c['helper']).read_text()
 norm=lambda s:re.sub(r"my \$(before|after)='[a-f0-9]{64}';",r"my $\1='HASH';",s)
 assert norm(a)==norm(b);assert sha(O/'vendor'/c['helper'])==c['helper_sha256']
def flags(path):return re.search(r'^\[info\] running \(fork\) vexiiriscv.Generate (.*)$',path.read_text(),re.M).group(1).split()
a=flags(P/'cpu-os30-regenerate-v5/generate.log');b=flags(O/'regenerate.log');assert len(a)==len(b)
changes=[dict(before=x,after=y) for x,y in zip(a,b) if x!=y];assert changes==[dict(before='--fetch-l1-sets=256',after='--fetch-l1-sets=512'),dict(before='--lsu-l1-sets=1024',after='--lsu-l1-sets=512')]
mapdir=O/'map-core';assert json.loads((mapdir/'completion.json').read_text())['passed']
mp=json.loads((mapdir/'prepared.json').read_text());assert m.inv(O/'frozen')==mp['source_sha256']
assert sorted(n for n in prep['baseline_frozen'] if prep['baseline_frozen'][n]!=mp['source_sha256'][n])==[m.CPU]
report=next((mapdir/'job').rglob('ap_core.map.rpt'));baseline=next((BASE/'fit-seed2/jobs').rglob('ap_core.map.rpt'))
wd=report.parent.parent;origwd=baseline.parent.parent;assert sha(wd/'ap_core.qsf')==sha(origwd/'ap_core.qsf')
assert not list((mapdir/'job').rglob('*.fit.rpt')),'A physical fit must not exist in this map-only job'
def numeric(v):return int(v.split()[0].replace(',',''))
a,b=entities(baseline),entities(report);delta={n:{k:numeric(b[n][k])-numeric(a[n][k]) for k in ('aluts','registers','memory_bits','dsp')} for n in a}
def totals(path):
 s=path.read_text().split('; Analysis & Synthesis Resource Usage Summary',1)[1].split('; Analysis & Synthesis Resource Utilization by Entity',1)[0]
 keys={'estimated_alms':'Estimate of Logic utilization (ALMs needed)','logic_aluts':'Combinational ALUT usage for logic','memory_aluts':'Memory ALUT usage','registers':'Dedicated logic registers','mlab_bits':'Total MLAB memory bits','block_memory_bits':'Total block memory bits','dsps':'Total DSP Blocks'}
 return {k:int(re.search(r';\s*'+re.escape(v)+r'\s*;\s*([\d,]+)',s).group(1).replace(',','')) for k,v in keys.items()}
ta,tb=totals(baseline),totals(report)
def rams(path):
 rows={}
 for l in path.read_text().splitlines():
  cells=[x.strip() for x in l.split(';')][1:-1]
  if len(cells)==9 and '|VexiiRiscv:cpu|altsyncram:' in cells[0] and cells[0].endswith('|ALTSYNCRAM'):
   name=cells[0].split('|VexiiRiscv:cpu|altsyncram:',1)[1].split('|',1)[0]
   rows[name]=dict(requested_type=cells[1],mode=cells[2],depth_a=cells[3],width_a=cells[4],depth_b=cells[5],width_b=cells[6],bits=int(cells[7]))
 return rows
ra,rb=rams(baseline),rams(report);assert ra.keys()==rb.keys();ramchanges={n:dict(before=ra[n],after=rb[n]) for n in ra if ra[n]!=rb[n]}
assert sum(v['after']['bits']-v['before']['bits'] for v in ramchanges.values())==delta['VexiiRiscv']['memory_bits']
model=json.loads((P/'renderer-pgo-cache64-v1/result.json').read_text());assert model['exact_buffers']==16
windows={w['window']:dict(baseline_ms=w['values']['baseline_i32_d128']['cpu_progression_ms'],candidate_ms=w['values']['candidate_i64_d64']['cpu_progression_ms'],cpu_time_increase_percent=-w['cpu_saving_percent'],presentation_saving_ms=w['presentation_saving_ms']) for w in model['windows']}
result=dict(passed_map=True,actual_regeneration_exact=True,candidate_cpu_sha256=regen['actual_sha256'],actual_generator_flag_changes=changes,helper_equations_unchanged=True,other_variants_unchanged=True,only_changed_synthesis_source=m.CPU,effective_qsf_identical_to_v7=True,seed=2,baseline_entities=a,candidate_entities=b,entity_delta=delta,baseline_totals=ta,candidate_totals=tb,total_delta={n:tb[n]-ta[n] for n in ta},changed_logical_rams=ramchanges,model_windows=windows,model_exact_buffers=16,physical_m10k_count=None,fitted_alms=None,fmax_mhz=None,full_fit_attempted=False,speed_improvement_claimed=False,production_modified=False,deployment_ready=False,limits=['This is a resource/placement-headroom experiment with a measured modeled CPU-time regression of about0.605% warm and0.293% cold.','The mapped total ALM estimate increases by9; reduced ALUTs do not establish fitted ALM savings.','The synthesis RAM-bit reduction does not establish exact physical M10K packing or timing.','Generated CPU cache capacities differ; cycle equivalence to the old cache profile is neither expected nor claimed.'])
put(O/'resource-result.json',result)
files=[p for p in O.iterdir() if p.is_file()]+[pathlib.Path(__file__),pathlib.Path(m.__file__),mapdir/'prepared.json',mapdir/'completion.json',mapdir/'map.log',report,wd/'ap_core.qsf',baseline,origwd/'ap_core.qsf',P/'renderer-pgo-cache64-v1/manifest.json',P/'renderer-pgo-cache64-v1/result.json',P/'cpu-os30-regenerate-v5/prepared.json',P/'cpu-os30-regenerate-v5/generate.log',m.OLD/'manifest.json']
files += [O/'vendor'/n for n in prep['generator_inputs_sha256']]
files += [O/'frozen'/n for n in mp['source_sha256']]
put(O/'manifest.json',dict(deployment_ready=False,full_fit_attempted=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}))
print(json.dumps({k:result[k] for k in ['candidate_cpu_sha256','actual_generator_flag_changes','entity_delta','total_delta','model_windows','physical_m10k_count']},indent=2));print('MANIFEST',sha(O/'manifest.json'))
