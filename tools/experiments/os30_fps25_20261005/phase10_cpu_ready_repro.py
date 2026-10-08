#!/usr/bin/env python3
"""Private checked os30 generator proposal, adding qualified ready factoring.

This stage never elaborates Scala. It extends the existing final frontend
postprocessor; all configuration, other variant arguments and helper ordering
remain as previously qualified. Further CPU composition must bind a new target.
"""
import difflib, hashlib, importlib.util, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=P/'cpu-os30-repro-v2';O=P/'cpu-os30-repro-v3'
V=Path('src/fpga/vendor/vexriscv');CPU=P/'cpu-ready-factor-v1/VexiiRiscv.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def prepare():
 O.mkdir(exist_ok=False);shutil.copytree(OLD/'proposed',O/'proposed');shutil.copytree(OLD/'controls',O/'controls')
 spec=importlib.util.spec_from_file_location('ready',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_ready_factor.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 p=O/'proposed'/V/'factor_os30_frontend.pl';s=p.read_text();s=rep(s,"my $after='b5397043e842bb2ed88005e6f24321668f2ddbffe392db94f08be3b44b36576d';",f"my $after='{sha(CPU)}';")
 guards=["  assign DispatchPlugin_logic_scheduler_eusFree_0 = 1'b1;","  assign DispatchPlugin_logic_scheduler_hartFree_0 = 1'b1;","  assign DispatchPlugin_logic_scheduler_arbiters_0_candHazard = 1'b0;",'  assign DispatchPlugin_logic_candidates_0_ctx_valid = ((decode_ctrls_2_up_isValid && decode_ctrls_2_up_LANE_SEL_0) && (! DispatchPlugin_logic_feeds_0_sent));']
 insert='''# Exact single-candidate topology is part of the ready-factor proof.
'''
 for i,g in enumerate(guards):
  insert+="{\n my $guard=<<'GUARD"+str(i)+"';\n"+g+'\nGUARD'+str(i)+'''\n chomp $guard;
 my $count=()=$source=~/\Q$guard\E/g;
 $count==1 or die "Unqualified single-candidate dispatch topology\\n";
}
'''
 insert+="{\n my $old=<<'READYOLD';\n"+m.OLD+'\nREADYOLD\n'+" my $new=<<'READYNEW';\n"+m.NEW+'''\nREADYNEW
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one original dispatch ready equation\\n";
 $source=~s/\Q$old\E/$new/;
}
'''
 anchor='sha256_hex($source) eq $after or die "Unexpected transformed frontend hash\\n";'
 s=rep(s,anchor,insert+anchor);p.write_text(s)
 files=sorted(p for p in (O/'proposed').rglob('*') if p.is_file());patch=''
 for p in files:
  n=p.relative_to(O/'proposed');before=(ROOT/n).read_text() if (ROOT/n).exists() else ''
  patch+=''.join(difflib.unified_diff(before.splitlines(True),p.read_text().splitlines(True),fromfile='a/'+str(n) if before else '/dev/null',tofile='b/'+str(n)))
 (O/'candidate.patch').write_text(patch)
 inherited=json.loads((OLD/'prepared.json').read_text());inherited.update(parent_proposal_manifest_sha256=sha(OLD/'manifest.json'),component_manifest_sha256=sha(P/'cpu-ready-factor-v1/manifest.json'),candidate_cpu_sha256=sha(CPU),ready_topology_guards=guards,production_modified=False,Scala_executed=False,actual_regeneration_pending_final_CPU_composition=True)
 (O/'prepared.json').write_text(json.dumps(inherited,indent=2)+'\n');shutil.copy2(__file__,O/'recipe.py')
 checks=(ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_frontend_repro_check.py').read_text();checks=rep(checks,"O=P/'cpu-os30-repro-v2';C=P/'cpu-rvc-control-v3'","O=P/'cpu-os30-repro-v3';C=P/'cpu-ready-factor-v1'")
 # Keep the check helper in the normal tools directory so ROOT resolves exactly.
 dst=ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_ready_repro_check.py';assert not dst.exists();dst.write_text(checks)
def seal():
 assert json.loads((O/'checks/result.json').read_text())['passed']
 prep=json.loads((O/'prepared.json').read_text())
 for n,h in prep['source_hashes'].items():assert sha(ROOT/n)==h,n
 files=[p for p in O.rglob('*') if p.is_file() and '/checks/flow/' not in str(p) and '/checks/original/' not in str(p) and '/checks/freshness-root/' not in str(p) and '/checks/publish-root/' not in str(p)]
 (O/'manifest.json').write_text(json.dumps({'files':{str(p.relative_to(O)):sha(p) for p in files},'component_manifest_sha256':sha(P/'cpu-ready-factor-v1/manifest.json'),'no_actual_regeneration':True,'no_map_or_fit':True,'production_unchanged':True},indent=2)+'\n')
if __name__=='__main__':globals()[sys.argv[1]]()
