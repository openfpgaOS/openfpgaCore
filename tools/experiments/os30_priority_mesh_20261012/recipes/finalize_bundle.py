#!/usr/bin/env python3
"""Seal current measured decision plus portable source-test receipt; no build/deploy."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess
p=argparse.ArgumentParser();p.add_argument('--bundle',type=Path,required=True);p.add_argument('--tests',type=Path,required=True);a=p.parse_args();out=a.bundle.resolve();tests=a.tests.resolve();here=Path(__file__).resolve().parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
subprocess.run(['python3',str(out/'verify.py'),'--patches','--evidence'],check=True)
m=json.loads((out/'manifest.json').read_text());assert len(m['strict_receipts'])==5 and len(m['fixtures'])==2
result=json.loads((tests/'result.json').read_text());assert result=={'status':'PASS','cases':['wrapper','combined']}
cases={}
for case in result['cases']:
 actual=json.loads((tests/case/'run.log').read_text());expected=json.loads((out/'receipts/tests'/f'{case}.json').read_text())['result'];assert actual==expected,(case,actual,expected);cases[case]=actual
selected={name:h for name,h in m['files'].items()if name.startswith(('rtl/','tests/wrapper/','tests/combined/'))or name in ['run_tests.py','recipes/wrapper-sources.json']}
for name,h in selected.items():assert sha(out/name)==h
proof={'status':'PASS_PORTABLE_ACTUAL_RTL_FROM_FINAL_BUNDLE','command':['python3',str(out/'run_tests.py'),'--output',str(tests)],'cases':cases,'checks':sum(v['checks']for v in cases.values()),'bundle_source_sha256':selected,'sanitizers':['address','undefined'],'leak_sanitizer':'disabled by inherited portable runner; address/undefined checks active','external_logs_sha256':{str(p):sha(p)for case in cases for p in [tests/case/'build.log',tests/case/'run.log']}}
proof_file=tests/'proof.json';assert not proof_file.exists();proof_file.write_text(json.dumps(proof,indent=2)+'\n')
def put(source,target):
 source=Path(source).resolve();dest=out/target;assert not dest.exists();dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest);m['copied_inputs'][target]={'source':str(source),'sha256':sha(source)};m['external_evidence_sha256'][str(source)]=sha(source)
put(proof_file,'receipts/tests/portable-bundle.json');put(Path(__file__),'recipes/finalize_bundle.py');put(here/'final-command-v1.json','recipes/final-command.json')
for name,h in proof['external_logs_sha256'].items():m['external_evidence_sha256'][name]=h
conclusion=json.loads((out/'reviews/conclusion.json').read_text());assert conclusion['status']=='CORRECT_CPU_OUTPUT_WAIT_REMOVED_BUT_NO_NET_SPEEDUP'
m['status']=conclusion['status'];m['decision']=conclusion['decision'];m['portable_tests']={'receipt':'receipts/tests/portable-bundle.json','checks':proof['checks']};m['result_report']='REPORT.txt';m['conclusion']='reviews/conclusion.json'
f=conclusion['final_same_F24_comparison'];h=conclusion['same_F23_hardware_comparison']
readme=f'''AJ02 priority mesh: CPU waiting removed, no net speedup

Keep the existing deployed core. Final F24 is {f['warm_cycle_change_percent']:.3f}% slower in
the warm interval and {f['combined_cycle_change_percent']:.3f}% slower across both intervals than its
identical-ELF qualified control. No physical fit, seed sweep or deployment ran.

The identical-F23 hardware comparison proves the intended CPU unblock: warm
raw-lease waiting fell from {h['warm_lease_wait_before']:,} to {h['warm_lease_wait_after']:,} cycles
({h['warm_lease_wait_reduction_percent']:.2f}%), reducing that complete warm interval by
{h['warm_whole_interval_reduction_percent']:.3f}% against the previous priority experiment. This is not a speedup
against the qualified control. REPORT.txt and reviews/conclusion.json contain
the exact measured table, actual overlap proof, decision and limitations.

Selected sources: native-v2 and F24 PRI2 app. The existing survivor M10K stores
converted packet data so raw/FMA can be reused while old output drains. Physical
area/timing remain unqualified. Five strict native receipts, two fresh fixtures,
F23 source history, all181 selected native source hashes, six exact source patch
round trips and the failed pre-binding observer attempt are recorded explicitly.
Game assets, binaries and Quartus databases are not included.

Verify locally:
  python3 verify.py --patches --evidence
  python3 run_tests.py --output /tmp/aj02-portable-tests

The portable actual-RTL wrapper/combined suites passed {proof['checks']:,} checks from
this bundle. Full native replay also needs separately held game inputs/tooling;
these are hash-bound, not distributed. Workspace recipes preserve original paths
for provenance. core.patch targets the qualified physical frozen tree;
recipes/prepare_physical.py records source/QSF integration but never compiles.
Historical priority-only RTL and F23 app patches are retained for review.

These are two bounded native intervals in one scene, not sustained Pocket FPS,
all-level coverage, or a Quake2 result. No script automatically fits or deploys.
'''
(out/'README.txt').write_text(readme)
m['files']={str(p.relative_to(out)):sha(p)for p in sorted(out.rglob('*'))if p.is_file()and str(p.relative_to(out))not in m['excluded_inventory']}
(out/'manifest.json').write_text(json.dumps(m,indent=2)+'\n')
subprocess.run(['python3',str(out/'verify.py'),'--patches','--evidence'],check=True)
print(json.dumps({'status':m['status'],'manifest_sha256':sha(out/'manifest.json'),'files':len(m['files']),'portable_checks':proof['checks']},indent=2))
