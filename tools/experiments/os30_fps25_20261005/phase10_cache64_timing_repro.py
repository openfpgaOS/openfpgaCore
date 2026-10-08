#!/usr/bin/env python3
"""Private exact I64/D64 generator chain plus the proved RVC bit19 factor."""
import difflib,hashlib,importlib.util,json,re,shutil,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
OLD=P/'cpu-os30-repro-v5';BASE=P/'cpu-cache64-resource-v1';O=P/'cpu-cache64-timing-repro-v1';C=P/'cpu-cache64-rvc19-v1';V=Path('src/fpga/vendor/vexriscv')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def prepare():
 assert sha(C/'VexiiRiscv.v')=='32cf8619ebd6beb9b7c62e593e300ba85486daf9c70c199ad0f978a23b80b3ea'
 for label in [OLD,BASE]:
  d=json.loads((label/'manifest.json').read_text())
  for n,h in d.get('files',{}).items():assert sha(label/n)==h,n
  for n,h in d.get('evidence_sha256',{}).items():assert sha(R/n)==h,n
 O.mkdir(exist_ok=False);shutil.copytree(OLD/'proposed',O/'proposed');shutil.copytree(OLD/'controls',O/'controls')
 for n in ['generate_vexii.sh','configs/os30.cfg','factor_os30_fp_rs3.pl','factor_os30_frontend.pl']:shutil.copy2(BASE/'vendor'/n,O/'proposed'/V/n)
 shutil.copy2(BASE/'raw-before-rs3.v',O/'controls/raw-emitted.v')
 spec=importlib.util.spec_from_file_location('bit19',R/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc19_cache64.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 parent=(BASE/'VexiiRiscv.v').read_text();first=next(l for l in parent.splitlines() if l.startswith('  assign AlignerPlugin_logic_feeder_lanes_0_isRvc ='))
 second='      decode_ctrls_0_up_Decode_INSTRUCTION_0[18] = rvc_instruction_bit18;'
 hunks=[('RVC19PLANE',first,m.EQ+'\n'+first),('RVC19OUTPUT',second,second+'\n      decode_ctrls_0_up_Decode_INSTRUCTION_0[19] = rvc_instruction_bit19;')]
 transformed=parent
 for tag,old,new in hunks:transformed=rep(transformed,old+'\n',new+'\n')
 assert transformed==(C/'VexiiRiscv.v').read_text()
 h=O/'proposed'/V/'factor_os30_frontend.pl';s=h.read_text();s=rep(s,"my $after='ab3205dff8e38211a223234e237a4ee083b55ecd0a02cb915c58850a34ce322e';",f"my $after='{sha(C/'VexiiRiscv.v')}';")
 insert='# Qualified direct RVC bit19 data plane; no state or cycle changes.\n'
 for name,old,new in hunks:
  insert+="{\n my $old=<<'"+name+"OLD';\n"+old+'\n'+name+"OLD\n my $new=<<'"+name+"NEW';\n"+new+'\n'+name+'''NEW
 my $count=()=$source=~/\Q$old\E/g;
 $count==1 or die "Expected one original '''+name+''' equation\n";
 $source=~s/\Q$old\E/$new/;
}
'''
 anchor='sha256_hex($source) eq $after or die "Unexpected transformed frontend hash\\n";';h.write_text(rep(s,anchor,insert+anchor))
 prep=json.loads((OLD/'prepared.json').read_text());prep.update(parent_proposal_manifest_sha256=sha(OLD/'manifest.json'),cache_component_manifest_sha256=sha(BASE/'manifest.json'),component_manifest_sha256=sha(C/'manifest.json'),candidate_cpu_sha256=sha(C/'VexiiRiscv.v'),added_checked_disjoint_hunks=[x[0] for x in hunks],cache_config_scope='os30 only sets512/512; os30_worker remains256/1024',actual_regeneration_pending=True,prepared_recipe_sha256=sha(Path(__file__)))
 put(O/'prepared.json',prep);shutil.copy2(__file__,O/'recipe.py')
 patch=''
 for q in sorted(p for p in (O/'proposed').rglob('*') if p.is_file()):
  n=q.relative_to(O/'proposed');a=(R/n).read_text() if (R/n).exists() else ''
  patch+=''.join(difflib.unified_diff(a.splitlines(True),q.read_text().splitlines(True),fromfile='a/'+str(n) if a else '/dev/null',tofile='b/'+str(n)))
 (O/'candidate.patch').write_text(patch)
 checks=(R/'tools/experiments/os30_fps25_20261005/phase10_cpu_final_repro_check.py').read_text()
 checks=rep(checks,"O=P/'cpu-os30-repro-v4';C=P/'cpu-final-paths-v1'","O=P/'cpu-cache64-timing-repro-v1';C=P/'cpu-cache64-rvc19-v1'")
 checks=rep(checks,"P/'cpu-fp-rs3-mux-v1/VexiiRiscv.v'","P/'cpu-cache64-resource-v1/rs3-actual.v'")
 checks=rep(checks,"flags=command.split()[2:];assert flags==json.loads((Q/'recipe.json').read_text())['flags']","flags=command.split()[2:];qualified=json.loads((Q/'recipe.json').read_text())['flags'];cachechanges={'--fetch-l1-sets=256':'--fetch-l1-sets=512','--lsu-l1-sets=1024':'--lsu-l1-sets=512'};assert flags==[cachechanges.get(x,x) for x in qualified]")
 checks=rep(checks,"assert [changes.get(x,x) for x in oldflags]==newflags","assert [cachechanges.get(changes.get(x,x),changes.get(x,x)) for x in oldflags]==newflags")
 checks=checks.replace('flags_equal_qualified_queue_recipe=True','flags_equal_qualified_queue_recipe_plus_os30_cache_change=True')
 dst=R/'tools/experiments/os30_fps25_20261005/phase10_cache64_timing_repro_check.py'
 with dst.open('x') as f:f.write(checks)
 print('PREPARED',sha(O/'prepared.json'))
def seal():
 assert json.loads((O/'checks/result.json').read_text())['passed'];d=json.loads((O/'prepared.json').read_text());assert sha(Path(__file__))==d['prepared_recipe_sha256']
 for n,h in d['source_hashes'].items():assert sha(R/n)==h,n
 files=[p for p in O.rglob('*') if p.is_file() and not any('/checks/'+x+'/' in str(p) for x in ['flow','original','freshness-root','publish-root'])]
 put(O/'manifest.json',dict(files={str(p.relative_to(O)):sha(p) for p in files},candidate_cpu_sha256=d['candidate_cpu_sha256'],no_actual_regeneration=True,no_map_or_fit=True,production_unchanged=True));print('MANIFEST',sha(O/'manifest.json'))
if __name__=='__main__':globals()[sys.argv[1]]()
