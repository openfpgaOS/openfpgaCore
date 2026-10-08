#!/usr/bin/env python3
"""Seal complete v7 timing census, allowing only proved empty sub90 reports."""
import collections,csv,json,re,shutil
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
from phase10_timing_paths_parse import parse
P=CAMP/'phase10';OUT=P/'compact-timing-audit-v7';CORE=P/'compact-lod5-core-v7'

def family(row):
 assert row['from_node'].endswith('|execute_ctrl3_up_LANE_SEL_lane0'),row
 assert '|VexiiRiscv:cpu|AlignerPlugin_logic_buffer_'in row['to_node'],row
 assert re.search(r'buffer_(?:mask|last)\[0\](?:~DUPLICATE)?$',row['to_node']),row
 return 'CPU_dispatch_ready_to_aligner_buffer_control'

def main():
 census=list(csv.DictReader((OUT/'reports/census.tsv').open(),delimiter='\t'))
 assert len(census)==2 and all(r['complete']=='1'and float(r['last_slack'])>=10-1000/90 for r in census)
 counts={'slow_'+r['temperature']:int(r['below_90'])for r in census}
 assert counts=={'slow_85':3,'slow_0':0}
 data={p.stem:parse(p)for p in sorted((OUT/'reports').glob('*.rpt'))}
 assert data
 empty=[]
 for name,rows in data.items():
  if not rows:
   assert name.endswith('_below90')and counts[name.removesuffix('_below90')]==0,name
   empty.append(name)
 assert empty==['slow_0_below90']
 # The generic parser's path semantics are unchanged; only its CLI's blanket
 # nonempty condition is narrowed using the complete endpoint census.
 write(OUT/'paths.json',data)
 groups={};modules={}
 for corner in ('slow_85','slow_0'):
  buckets=collections.defaultdict(list)
  for row in data[corner+'_below90']:
   assert row['relationship_ns']==10 and row['period_bound_mhz']<90
   buckets[family(row)].append(row)
  groups[corner]=dict(buckets);assert sum(map(len,buckets.values()))==counts[corner]
  modules[corner]={}
  for module in ('cpu','gpu_internal','geometry','render_cache','sdram','dispatch','rvc','aligner_pc'):
   rows=[r for r in data[corner+'_'+module]if r['relationship_ns']==10]
   assert rows,(corner,module);modules[corner][module]=min(rows,key=lambda r:r['slack_ns'])
 endpoints=list(csv.DictReader((OUT/'reports/endpoints.tsv').open(),delimiter='\t'))
 assert len(endpoints)==3
 assert {(r['from'],r['to'],float(r['slack']))for r in endpoints}=={(r['from_node'],r['to_node'],r['slack_ns'])for r in data['slow_85_below90']}
 fit=json.loads((CORE/'result.json').read_text());assert fit['whole_core_fit_successful']and not fit['exceeds_90mhz']and not fit['all_configured_timing_checks_pass']
 invocation=json.loads((OUT/'invocation.json').read_text());assert not invocation['fit_invoked']
 assert invocation['completed_parent_sha256']==sha(CORE/'fit-seed2/complete.json')and invocation['tcl_sha256']==sha(OUT/'audit.tcl')
 source=CORE/'fit-seed2/jobs/pocket-os30-s2'
 for n,h in invocation['inputs_sha256'].items():assert sha(source/n)==h,n
 worst={c:data[c+'_global'][0]['period_bound_mhz']for c in counts}
 for c,value in worst.items():assert abs(value-fit['main_clock_slow_fmax_mhz'][c.removeprefix('slow_')])<.01
 path=data['slow_85_global'][0];assert path['slack_ns']==-1.154
 gap_ps=(path['effective_period_ns']-1000/90)*1000
 report='''Compact-v7 routed timing audit

The complete endpoint census finds three endpoints below 90 MHz at slow 85 C
and none at slow 0 C, compared with 13/2 in v6. All three are the same CPU
dispatch-ready/aligner-buffer control family: execute_ctrl3_up_LANE_SEL_lane0
to AlignerPlugin_logic_buffer_mask[0], its duplicate, and buffer_last[0].
They are not compressed-instruction decoder output endpoints.

Worst path bounds are 89.654 MHz at 85 C and 91.199 MHz at 0 C (the main-clock
summary rounds these to 89.65/91.20). At 85 C the limiting paths have seven
logic levels, about 10.880 ns data delay, 6.820 ns routing (63%), and -0.154 ns
clock skew. The effective period is 11.154 ns, about 42.889 ps beyond the
11.111 ns period for 90 MHz. A neighboring RS3-dependency path reaches the same
aligner endpoint at 90.050 MHz, so the remaining ready/control cone is narrow
but does not have substantial margin.

The routed 85 C module endpoint bounds are CPU 89.654, GPU 92.336, SDRAM
91.600, render cache 97.399, and LOAD 100.867 MHz. RVC decode endpoints are
96.479 MHz and the exported aligner-to-PC family is 115.088 MHz. The complete
sub90 census contains no GPU, SDRAM, cache or texture-arbiter endpoint.
Do not compose the separately prepared fallback changes for those families
based on v6 paths; v7 no longer supplies a timing need for them.

V7 fits 17,956 needed ALMs, 17,997 placed ALMs, 1,842/1,848 LABs,
307/308 M10Ks and 45 DSPs. At configured 100 MHz, worst setup is -1.154 ns;
hold is +0.032 ns, recovery +1.647 ns, removal +0.404 ns and pulse width
+0.500 ns. It therefore meets neither the strict historical greater-than-
90 MHz target across corners nor the configured 100 MHz setup requirement.
This is not a deployment recommendation or a Pocket FPS measurement.

Physical-strategy limit: the shared global clock already drives both launch
and capture. Changing its common latency cannot simply remove this same-clock
data dependency. Local useful skew or placement/logic duplication could alter
the 43 ps deficit, but that is an untested physical hypothesis; the 32 ps global
hold margin makes arbitrary clock-skew changes particularly unjustified.
The path includes a control node with fanout 89, so a narrowly proved CPU
control factoring/replication may help placement without changing cycles.
No clock, placement constraint, RTL, seed, or physical option was changed here.

The unchanged generic full-path parser retains clock relationship, skew/setup
effects in its effective-period bound. The summary adapter accepts an empty
slow_0_below90 report only because the independent complete census reports
zero; every other report remains mandatory. External SDRAM relationships are
retained and are not converted using the internal 100 MHz period formula.
Only an already-exported copied database was read; original input hashes were
checked again. This summary invoked no Quartus process or fitter.
'''
 write(OUT/'families.json',groups);write(OUT/'module-worst.json',modules)
 write(OUT/'result.json',dict(passed_readonly_export=True,timing_passed_90mhz=False,timing_passed_configured_100mhz=False,worst_mhz=worst,census=census,family_counts={c:{f:len(rows)for f,rows in fs.items()}for c,fs in groups.items()},empty_reports_accepted_only_with_complete_zero_census=empty,remaining_90mhz_deficit_ps=gap_ps,resources=fit['resources'],worst_timing=fit['worst_timing'],fitter_invoked=False,rtl_or_physical_options_changed=False,frozen_inputs_modified=False))
 (OUT/'REPORT.txt').write_text(report);shutil.copy2(Path(__file__),OUT/'summary-recipe.py')
 evidence=[CORE/'final-manifest.json',P/'compact-timing-audit-v6/manifest.json',Path(__file__),Path(__file__).with_name('phase10_timing_paths_parse.py')]
 write(OUT/'manifest.json',dict(files={str(p.relative_to(OUT)):sha(p)for p in sorted(OUT.rglob('*'))if p.is_file()and'job'not in p.relative_to(OUT).parts and p.name!='manifest.json'},evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in evidence}))
 print(json.dumps(json.loads((OUT/'result.json').read_text()),indent=2));print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
