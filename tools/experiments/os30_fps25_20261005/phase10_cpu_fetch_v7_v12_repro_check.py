#!/usr/bin/env python3
"""Bounded generator replay with captured raw RTL; does not execute Scala/sbt."""
import hashlib,json,os,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-os30-repro-v10';C=P/'cpu-fetch-v7-v12';Q=P/'cpu-queues-v1';V=Path('src/fpga/vendor/vexriscv')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
D=O/'checks';D.mkdir(exist_ok=False)
helper=O/'proposed'/V/'factor_os30_frontend.pl';results={}
def run(cmd,**kw):
 r=subprocess.run(cmd,capture_output=True,text=True,**kw);return r
def transform(name,source,variant='os30',expect=True):
 p=D/(name+'.v');p.write_bytes(source);before=sha(p)
 r=run(['perl',str(helper),variant,str(p)])
 (D/(name+'.log')).write_text(r.stdout+r.stderr)
 assert (r.returncode==0)==expect,(name,r.returncode,r.stderr)
 if expect:assert sha(p)==sha(C/'VexiiRiscv.v')
 else:assert sha(p)==before
 results[name]=dict(passed=True,returncode=r.returncode,sha256=sha(p))
parent=(P/'cpu-fp-rs3-mux-v1/VexiiRiscv.v').read_bytes();candidate=(C/'VexiiRiscv.v').read_bytes()
transform('parent',parent);transform('idempotent',candidate)
body=parent.split(b'\n\n',1)[1]
transform('archive-header-rejected-before-rs3-normalization',b'// Generator : SpinalHDL dev    git head : ???\n// Component : VexiiRiscv\n\n'+body,expect=False)
transform('changed-body',parent.replace(b'`timescale 1ns/1ps',b'`timescale 1ns/1ns'),expect=False)
transform('wrong-variant',parent,'os25',False)
transform('partial-transform',parent.replace(b'execute_ctrl1_down_float_RS3_lane0 =',b'execute_ctrl1_down_float_RS3_lane0 = /* incomplete */',1),expect=False)

def vendor_at(path,script):
 path.mkdir(parents=True);shutil.copy2(script,path/'generate_vexii.sh')
 shutil.copytree(O/'controls/configs',path/'configs');(path/'VexiiRiscv').mkdir()
 for h in (ROOT/V).glob('*.pl'):shutil.copy2(h,path/h.name)
 shutil.copy2(helper,path/helper.name)
 shutil.copy2(O/'proposed'/V/'factor_os30_fp_rs3.pl',path/'factor_os30_fp_rs3.pl')
 return path
def stub_at(path,emit):
 path.mkdir();f=path/'sbt'
 f.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$REPLAY_ARGS"\n'+('cp "$REPLAY_RAW" VexiiRiscv.v\n' if emit else 'exit 77\n'))
 f.chmod(0o755);return path
proposed=vendor_at(D/'flow',O/'proposed'/V/'generate_vexii.sh')
shutil.copy2(O/'proposed'/V/'configs/os30.cfg',proposed/'configs/os30.cfg')
stub=stub_at(D/'emit-stub',True)
env=dict(os.environ,PATH=str(stub)+os.pathsep+os.environ['PATH'],REPLAY_ARGS=str(D/'flow.args'),REPLAY_RAW=str(O/'controls/raw-emitted.v'))
out=proposed/'VexiiRiscv/VexiiRiscv_os30.v';out.write_text('previous CPU\n')
r=run(['bash',str(proposed/'generate_vexii.sh'),'os30'],env=env);(D/'flow.log').write_text(r.stdout+r.stderr)
assert r.returncode==0,(r.returncode,r.stderr)
assert sha(out)==sha(C/'VexiiRiscv.v'),(sha(out),sha(C/'VexiiRiscv.v'))
args=(D/'flow.args').read_text().splitlines();command=next(x for x in args if x.startswith('Test/runMain '))
flags=command.split()[2:];assert flags==json.loads((Q/'recipe.json').read_text())['flags']
results['captured_raw_full_flow']=dict(passed=True,output_sha256=sha(out),flags_equal_qualified_queue_recipe=True,Scala_executed=False)
# Final-hook failure must preserve the previously published artifact or absence.
bad=D/'bad-raw.v';bad.write_bytes((O/'controls/raw-emitted.v').read_bytes()+b'\n// unqualified body change\n')
env['REPLAY_RAW']=str(bad)
for present in (True,False):
 if present:out.write_text('previous CPU\n')
 else:out.unlink()
 r=run(['bash',str(proposed/'generate_vexii.sh'),'os30'],env=env)
 (D/('failure-present.log' if present else 'failure-absent.log')).write_text(r.stdout+r.stderr)
 assert r.returncode!=0
 assert out.read_text()=='previous CPU\n' if present else not out.exists()
results['publication_failure']=dict(passed=True,old_output_preserved=True,absent_output_remains_absent=True)
# Compare the exact argument lists without executing any emitter/helper.
capture=stub_at(D/'capture-stub',False);variants={}
base=vendor_at(D/'original',O/'controls/generate_vexii.sh')
for cfg in sorted((base/'configs').glob('*.cfg')):
 variant=cfg.stem;pair=[]
 for tag,v in [('old',base),('new',proposed)]:
  a=D/(variant+'-'+tag+'.args');ee=dict(os.environ,PATH=str(capture)+os.pathsep+os.environ['PATH'],REPLAY_ARGS=str(a))
  rr=run(['bash',str(v/'generate_vexii.sh'),variant],env=ee);assert rr.returncode==77,(variant,tag,rr.stderr)
  pair.append(a.read_text())
 if variant=='os30':
  oldflags=next(x for x in pair[0].splitlines() if x.startswith('Test/runMain ')).split()[2:]
  newflags=next(x for x in pair[1].splitlines() if x.startswith('Test/runMain ')).split()[2:]
  changes=json.loads((Q/'recipe.json').read_text())['changes'];assert [changes.get(x,x) for x in oldflags]==newflags
 else:assert pair[0]==pair[1],variant
 variants[variant]=dict(flags_unchanged=pair[0]==pair[1])
results['variant_arguments']=variants
# Execute the ordinary freshness test against the proposed private file tree.
testroot=D/'freshness-root';target=testroot/'src/fpga/targets/pocket';target.mkdir(parents=True)
shutil.copy2(O/'proposed/src/fpga/targets/pocket/Makefile',target/'Makefile')
shutil.copytree(ROOT/'src/fpga/targets/pocket/variants',target/'variants')
(testroot/'tools/tests').mkdir(parents=True)
test=testroot/'tools/tests/test_pocket_cpu_freshness.py';shutil.copy2(O/'proposed/tools/tests/test_pocket_cpu_freshness.py',test)
r=run([sys.executable,str(test)]);(D/'freshness.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
results['freshness']=dict(passed=True,os30_helper_invalidation=True)
# Additional direct make query: helper timestamps must not invalidate os25.
vr=testroot/V
for n in ['generate_vexii.sh','retime_fetch_reads.pl','retime_aligner_pc.pl','factor_fetch_ready.pl','retime_dcache_writes.pl','retime_fpu_matches.pl','retime_fetch_pipeline.pl','optimize_os20_timing.pl','configs/os25.cfg','configs/os30.cfg','VexiiRiscv/build.sbt','VexiiRiscv/ext/SpinalHDL/build.sbt','factor_os30_fp_rs3.pl','factor_os30_frontend.pl']:
 p=vr/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('fixture\n');os.utime(p,(10,10))
for variant in ('os25','os30'):
 p=vr/f'VexiiRiscv/VexiiRiscv_{variant}.v';p.write_text('fixture netlist\n');os.utime(p,(20,20))
os.utime(vr/'factor_os30_frontend.pl',(30,30))
for variant,expected in [('os25',0),('os30',1)]:
 r=run(['make','-s','-q','VARIANT='+variant,'../../vendor/vexriscv/VexiiRiscv/VexiiRiscv_'+variant+'.v'],cwd=target)
 assert r.returncode==expected,(variant,r.returncode,r.stderr)
results['freshness']['os25_unaffected']=True
# Extend the normal publication regression with the new final os30 helper.
publishroot=D/'publish-root';(publishroot/V).mkdir(parents=True);(publishroot/'tools/tests').mkdir(parents=True)
for h in (ROOT/V).glob('*.pl'):shutil.copy2(h,publishroot/V/h.name)
for name in ('generate_vexii.sh','factor_os30_fp_rs3.pl','factor_os30_frontend.pl'):shutil.copy2(O/'proposed'/V/name,publishroot/V/name)
test=publishroot/'tools/tests/test_vexii_publish.py';shutil.copy2(O/'proposed/tools/tests/test_vexii_publish.py',test)
r=run([sys.executable,str(test)]);(D/'publish-test.log').write_text(r.stdout+r.stderr);assert r.returncode==0,r.stderr
results['ordinary_publish_test']=dict(passed=True,new_final_helper_failure_covered=True)
(D/'result.json').write_text(json.dumps(dict(passed=True,checks=results,no_regeneration=True,no_fit=True),indent=2)+'\n')
shutil.copy2(__file__,D/'recipe.py');print(json.dumps(results,indent=2))
