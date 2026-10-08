#!/usr/bin/env python3
"""Private checked os30 generation extension for the two measured RVC paths."""
import difflib, hashlib, importlib.util, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=P/'cpu-os30-repro-v4'; O=P/'cpu-os30-repro-v5'; C=P/'cpu-rvc-plane-v7'
V=Path('src/fpga/vendor/vexriscv')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a))
 return s.replace(a,b)
def prepare():
 assert (C/'manifest.json').exists()
 O.mkdir(exist_ok=False)
 shutil.copytree(OLD/'proposed',O/'proposed'); shutil.copytree(OLD/'controls',O/'controls')
 spec=importlib.util.spec_from_file_location('plane',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_plane_v7.py')
 m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
 p=O/'proposed'/V/'factor_os30_frontend.pl'; s=p.read_text()
 s=rep(s,"my $after='022f46fbbb0834bba1499df65ecdda79ecf13447fb33eff85effcb3bbda1e73d';",f"my $after='{sha(C/'VexiiRiscv.v')}';")
 override='      decode_ctrls_0_up_Decode_INSTRUCTION_0[16] = rvc_instruction_bit16;'
 hunks=[('RVCPLANES',m.OLD,m.NEW),('RVC18OUTPUT',override,override+'\n      decode_ctrls_0_up_Decode_INSTRUCTION_0[18] = rvc_instruction_bit18;')]
 insert='# Qualified bit16/18 direct decoder planes; all architectural controls unchanged.\n'
 for name,old,new in hunks:
  insert+="{\n my $old=<<'"+name+"OLD';\n"+old+'\n'+name+"OLD\n my $new=<<'"+name+"NEW';\n"+new+'\n'+name+'''NEW
 my $count=()=$source=~/\\Q$old\\E/g;
 $count==1 or die "Expected one original '''+name+''' equation\\n";
 $source=~s/\\Q$old\\E/$new/;
}
'''
 anchor='sha256_hex($source) eq $after or die "Unexpected transformed frontend hash\\n";'
 p.write_text(rep(s,anchor,insert+anchor))
 patch=''
 for q in sorted(p for p in (O/'proposed').rglob('*') if p.is_file()):
  n=q.relative_to(O/'proposed'); before=(ROOT/n).read_text() if (ROOT/n).exists() else ''
  patch+=''.join(difflib.unified_diff(before.splitlines(True),q.read_text().splitlines(True),fromfile='a/'+str(n) if before else '/dev/null',tofile='b/'+str(n)))
 (O/'candidate.patch').write_text(patch)
 prep=json.loads((OLD/'prepared.json').read_text())
 prep.update(parent_proposal_manifest_sha256=sha(OLD/'manifest.json'),component_manifest_sha256=sha(C/'manifest.json'),candidate_cpu_sha256=sha(C/'VexiiRiscv.v'),added_checked_disjoint_hunks=['RVCPLANES','RVC18OUTPUT'],actual_regeneration_pending=True)
 (O/'prepared.json').write_text(json.dumps(prep,indent=2)+'\n'); shutil.copy2(__file__,O/'recipe.py')
 checks=(ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_final_repro_check.py').read_text()
 checks=rep(checks,"O=P/'cpu-os30-repro-v4';C=P/'cpu-final-paths-v1'","O=P/'cpu-os30-repro-v5';C=P/'cpu-rvc-plane-v7'")
 dst=ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_rvc_plane_repro_check.py'
 assert not dst.exists(); dst.write_text(checks)
def seal():
 assert json.loads((O/'checks/result.json').read_text())['passed']
 prep=json.loads((O/'prepared.json').read_text())
 for n,h in prep['source_hashes'].items(): assert sha(ROOT/n)==h,n
 files=[p for p in O.rglob('*') if p.is_file() and not any('/checks/'+x+'/' in str(p) for x in ['flow','original','freshness-root','publish-root'])]
 with (O/'manifest.json').open('x') as f:
  json.dump(dict(files={str(p.relative_to(O)):sha(p) for p in files},component_manifest_sha256=sha(C/'manifest.json'),no_actual_regeneration=True,no_map_or_fit=True,production_unchanged=True),f,indent=2);f.write('\n')
 print(sha(O/'manifest.json'))
if __name__=='__main__':globals()[sys.argv[1]]()
