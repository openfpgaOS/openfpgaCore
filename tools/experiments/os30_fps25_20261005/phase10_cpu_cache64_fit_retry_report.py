#!/usr/bin/env python3
"""Seal exact cache fit; tolerate vendor degree symbols only in report decoding."""
import json,pathlib,re
import phase10_cpu_cache64_fit_retry as m
R,P,O,F,sha,put=m.R,m.P,m.O,m.F,m.sha,m.put
m.checked();c=json.loads((F/'complete.json').read_text());assert c['state']=='complete';wd=pathlib.Path(c['directory'])
def report(path):return path.read_text(errors='replace')
s=report(wd/'output_files/ap_core.fit.rpt');resources={}
for k,p in {'needed_alms':r';\s*ALMs needed \[=A-B\+C\]\s*;\s*([\d,]+)','placed_alms':r';\s*\[A\] ALMs used in final placement \[=a\+b\+c\+d\]\s*;\s*([\d,]+)','labs':r';\s*Total LABs:\s*partially or completely used\s*;\s*([\d,]+)','m10ks':r';\s*M10K blocks\s*;\s*([\d,]+)','dsps':r';\s*Total DSP Blocks\s*;\s*([\d,]+)'}.items():
 match=re.search(p,s);assert match;resources[k]=int(match.group(1).replace(',',''))
freq={};s=report(wd/'output_files/ap_core.sta.rpt')
for t in (0,85):
 section=s.split(f'; Slow 1100mV {t}C Model Fmax Summary',1)[1].split('Model Setup Summary',1)[0];match=re.search(r';\s*([\d.]+) MHz\s*;[^\n]+ic\|mp_ram\|[^\n]+general\[0\]',section);assert match;freq[str(t)]=float(match.group(1))
rows=c['timing'];assert len(rows)==20 and all(re.fullmatch(r'-?\d+(?:\.\d+)?',r['slack']) for r in rows)
assert set((r['model'],r['temperature'],r['check']) for r in rows)==set((model,t,check) for model in ('slow','fast') for t in ('0','85') for check in ('setup','hold','recovery','removal','pulse_width'))
log=(F/'corners.log').read_text();errors=[l for l in log.splitlines() if re.search(r'^Error|^Fatal',l)]
assert c['returncodes']==dict(fit=0,asm=0,sta=0,corners=3)
assert errors==[f'Error (23031): Evaluation of Tcl script {F}/job/tools/report_core_timing.tcl unsuccessful','Error: Quartus Prime Timing Analyzer was unsuccessful. 1 error, 1 warning']
assert '\nWhole-design timing failed; see timing\n' in log and len(re.findall(r'^RESULT ',log,re.M))==20
worst=c['worst'];passed=min(freq.values())>90 and all(worst[k]>0 for k in ('hold','recovery','removal','pulse_width'))
d=json.loads((O/'prepared.json').read_text())
for n,h in d['source_input_sha256'].items():assert sha(F/'job'/n)==h,n
output=dict(comparative_physical_gate_pass=passed,configured_mhz=100,all_configured_timing_checks_pass=c['all_checks_pass'],slow_main_clock_fmax_mhz=freq,resources=resources,worst_timing=worst,phase_returncodes=c['returncodes'],seconds=c['seconds'],seed=2,actual_quartus_fits=1,prior_permission_attempt_quartus_fits=0,seed_sweep=False,production_modified=False,deployment_ready=False,raw_reports_hashed_without_normalization=True,report_decoding='UTF-8 replacement for vendor non-UTF8 report glyphs; numeric ASCII fields unchanged',earlier_analytic_model_cpu_time_cost_percent=dict(warm=0.6049778987613497,cold=0.29280837110863267),native_cpu_comparison='Separate newly reported native cold-call comparison is being independently sealed; no warm/full-frame inference here.')
put(O/'result.json',output)
files=[pathlib.Path(__file__),pathlib.Path(m.__file__),O/'prepared.json',O/'independent-review.json',O/'retry-link.json',O/'result.json',F/'complete.json',F/'prepared.json',m.C/'manifest.json',wd/'ap_core.qsf',P/'cache64-v7-fit-v1/manifest.json',P/'cache64-v7-fit-v1/failure-classification.json']
for directory in [F,wd/'output_files',wd/'timing']:
 files += [p for p in directory.iterdir() if p.is_file() and p.suffix in {'.json','.log','.rpt','.tsv','.rbf','.sof'}]
put(O/'manifest.json',dict(deployment_ready=False,evidence_sha256={str(p.relative_to(R)):sha(p) for p in sorted(set(files))}));print(json.dumps(output,indent=2));print('MANIFEST',sha(O/'manifest.json'))
