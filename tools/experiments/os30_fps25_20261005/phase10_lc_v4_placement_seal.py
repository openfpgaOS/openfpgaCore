#!/usr/bin/env python3
"""Seal bounded placement evidence for the rejected LC v4 fit."""
import collections,hashlib,json,pathlib,re,shlex
R=pathlib.Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'lc-v4-placement-audit-v1';S=P/'lc-v7-timing-placement-v4';F=S/'fit-seed2';W=F/'job/src/fpga/targets/pocket'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 if p.exists():
  assert json.loads(p.read_text())==x;return
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def census(f):
 d={}
 for line in f.read_text().splitlines():
  _,name,loc=line.split('\t');assert name not in d;d[name]=loc
 return d
b=census(P/'eco-v7-local-placement-v1/baseline-nodes.tsv');a=census(O/'nodes.tsv')
loc={}
for line in (W/'ap_core.qsf').read_text().splitlines():
 if line.startswith('set_location_assignment '):
  t=shlex.split(line);assert t[3] not in loc;loc[t[3]]=t[1]
common=b.keys()&a.keys();same={n for n in common if b[n]==a[n]};moved=common-same
present=loc.keys()&a.keys();match={n for n in present if loc[n]==a[n]};assert present==match
fitlog=(F/'fit.log').read_text();warnings=collections.Counter(re.findall(r'Warning \((\d+)\)',fitlog))
absent=set(re.findall(r'Warning \(15706\): Node "(.*?)" is assigned to location or region, but does not exist in design',fitlog));assert absent==loc.keys()-a.keys()
put(O/'placement-differences.json',dict(moved=[dict(name=n,before=b[n],after=a[n],constrained=n in loc) for n in sorted(moved)],absent_original_names=sorted(b.keys()-a.keys()),new_names=sorted(a.keys()-b.keys()),absent_constraint_targets=sorted(absent)))
target='core_top:ic|cpu_system:cpu|VexiiRiscv:cpu|WideOr109~0';assert a[target]==loc[target]=='LABCELL_X44_Y34_N6'
critical={n:dict(before=b.get(n),after=a[n],requested=loc.get(n)) for n in sorted(a) if n==target or n.endswith('execute_ctrl3_up_LANE_SEL_lane0') or 'AlignerPlugin_logic_buffer_mask[0]' in n or 'AlignerPlugin_logic_buffer_last[0]' in n}
cones={}
for temp in (0,85):
 text=(O/f'slow_{temp}_cone.rpt').read_text();slack=float(re.search(r'Worst case slack is ([-\d.]+)',text).group(1))
 first=text.split('Path #1:',1)[1].split('Path #2:',1)[0]
 path={}
 for key in ('From Node','To Node','Slack','Data Delay','Number of Logic Levels'):
  m=re.search(r';\s*'+re.escape(key)+r'\s*;\s*([^;]+)',first);path[key]=m.group(1).strip() if m else None
 edge=next(l for l in first.splitlines() if 'WideOr109~0|datae' in l);parts=[x.strip() for x in edge.split(';')];route=float(parts[2])
 cones[str(temp)]=dict(worst_setup_ns=slack,period_equivalent_mhz=1000/(10-slack),path=path,launch_to_wideor109_route_ns=route)
# Bind all declared RTL/configuration inputs to their exact prepared values.
prepared=json.loads((S/'prepared.json').read_text())['prepared_job_sha256'];source={}
for name,h in prepared.items():
 p=F/'job'/name
 if p.suffix.lower() in {'.v','.sv','.vh','.vhd','.qsf','.qpf','.qip','.sdc','.mif','.hex'}:
  assert sha(p)==h,name;assert sha(O/'job'/name)==h,name;source[name]=h
complete=json.loads((F/'complete.json').read_text());assert len(complete['timing'])==20
rows=(W/'timing/summary.tsv').read_text().splitlines();assert len(rows)==21
assert set((r['model'],r['temperature'],r['check']) for r in complete['timing'])==set((m,t,c) for m in ('slow','fast') for t in ('0','85') for c in ('setup','hold','recovery','removal','pulse_width'))
cornerlog=(F/'corners.log').read_text();errors=[l for l in cornerlog.splitlines() if re.search(r'^Error|^Fatal',l)]
assert errors==[f'Error (23031): Evaluation of Tcl script {F}/job/tools/report_core_timing.tcl unsuccessful','Error: Quartus Prime Timing Analyzer was unsuccessful. 1 error, 1 warning'],errors
assert '\nWhole-design timing failed; see timing\n' in cornerlog and len(re.findall(r'^RESULT ',cornerlog,re.M))==20
report=json.loads((S/'fit-result.json').read_text());assert not report['comparative_physical_gate_pass']
# Record only existing reports; this audit never ran another map, fit, or assembly.
newcritical={}
for temp in (0,85):
 text=(W/f'timing/slow_{temp}_setup.rpt').read_text();first=text.split('Path #1:',1)[1].split('Path #2:',1)[0]
 newcritical[str(temp)]={key:re.search(r';\s*'+re.escape(key)+r'\s*;\s*([^;]+)',first).group(1).strip() for key in ('From Node','To Node','Slack','Data Delay')}
result=dict(status='rejected_timing',baseline_nodes=len(b),after_nodes=len(a),common_nodes=len(common),same_location=len(same),changed_location=len(moved),absent_original_names=len(b.keys()-a.keys()),new_names=len(a.keys()-b.keys()),requested_constraints=len(loc),surviving_constraints=len(present),surviving_constraints_exact=len(match),absent_constraints=len(absent),absent_constraints_match_warning_15706=True,critical_locations=critical,original_lane_aligner_cones=cones,current_worst_paths=newcritical,main_clock_slow_fmax_mhz=report['main_clock_slow_fmax_mhz'],worst_timing=report['worst_timing'],resources=report['resources'],warning_counts=dict(warnings),source_inputs_checked=len(source),fresh_corner_rows=20,expected_corner_error=errors,configured_mhz=100,seed=2,no_new_map_fit_assembly=True,logical_equivalence_claimed=False,production_modified=False,deployment_ready=False,interpretation=['The requested LUT move is honored and repairs the original sub-90-MHz cone.','The ordinary fitter regenerates some names and changes unconstrained placements/routes; exact preserved placement or netlist is not claimed.','Other CPU load-result forwarding, SDRAM port decode and GPU Z-accumulator paths now fail the comparative timing target.','No further placement trial or normal-build integration is selected.'])
put(O/'result.json',result);put(O/'source-inputs.json',source)
files=[p for p in O.iterdir() if p.is_file()]+[pathlib.Path(__file__),S/'prepared.json',S/'fit-result.json',S/'fit-manifest.json',F/'complete.json',F/'fit.log',F/'corners.log',W/'ap_core.qsf',W/'output_files/ap_core.fit.rpt',W/'output_files/ap_core.sta.rpt',W/'timing/summary.tsv',P/'eco-v7-local-placement-v1/baseline-nodes.tsv']+[W/f'timing/slow_{t}_setup.rpt' for t in (0,85)]
put(O/'manifest.json',dict(status='rejected_timing',evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}))
print(json.dumps(result,indent=2));print('MANIFEST',sha(O/'manifest.json'))
