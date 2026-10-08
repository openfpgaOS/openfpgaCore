#!/usr/bin/env python3
"""Final private os30 checked postprocessor composition; no generator variant changes."""
import difflib,hashlib,importlib.util,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OLD=P/'cpu-os30-repro-v3';O=P/'cpu-os30-repro-v4';C=P/'cpu-final-paths-v1';V=Path('src/fpga/vendor/vexriscv')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def prepare():
 assert (C/'manifest.json').exists();O.mkdir(exist_ok=False);shutil.copytree(OLD/'proposed',O/'proposed');shutil.copytree(OLD/'controls',O/'controls')
 spec=importlib.util.spec_from_file_location('integer',ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_int_wb_factor.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
 p=O/'proposed'/V/'factor_os30_frontend.pl';s=p.read_text();s=rep(s,"my $after='726cb18a845d60aca7f74f374f9c3f459d381db5ca7a6810c032296736445c6c';",f"my $after='{sha(C/'VexiiRiscv.v')}';")
 f=P/'cpu-fp-rs2-mux-v1';hunks=[('INTWB',m.OLD,m.NEW),('RS2',(f/'original-assignment.txt').read_text().rstrip('\n'),(f/'candidate-assignment.txt').read_text().rstrip('\n'))]
 insert='# Qualified disjoint zero-cycle data rewrites after ready factoring.\n'
 for name,old,new in hunks:
  insert+="{\n my $old=<<'"+name+"OLD';\n"+old+'\n'+name+"OLD\n my $new=<<'"+name+"NEW';\n"+new+'\n'+name+'''NEW
 my $count=()=$source=~/\\Q$old\\E/g;
 $count==1 or die "Expected one original '''+name+''' data equation\\n";
 $source=~s/\\Q$old\\E/$new/;
}
'''
 anchor='sha256_hex($source) eq $after or die "Unexpected transformed frontend hash\\n";';s=rep(s,anchor,insert+anchor);p.write_text(s)
 patch=''
 for q in sorted(p for p in (O/'proposed').rglob('*') if p.is_file()):
  n=q.relative_to(O/'proposed');before=(ROOT/n).read_text() if (ROOT/n).exists() else '';patch+=''.join(difflib.unified_diff(before.splitlines(True),q.read_text().splitlines(True),fromfile='a/'+str(n) if before else '/dev/null',tofile='b/'+str(n)))
 (O/'candidate.patch').write_text(patch)
 prep=json.loads((OLD/'prepared.json').read_text());prep.update(parent_proposal_manifest_sha256=sha(OLD/'manifest.json'),component_manifest_sha256=sha(C/'manifest.json'),candidate_cpu_sha256=sha(C/'VexiiRiscv.v'),added_checked_disjoint_hunks=['INTWB','RS2'],actual_regeneration_pending=True);(O/'prepared.json').write_text(json.dumps(prep,indent=2)+'\n');shutil.copy2(__file__,O/'recipe.py')
 checks=(ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_ready_repro_check.py').read_text();checks=rep(checks,"O=P/'cpu-os30-repro-v3';C=P/'cpu-ready-factor-v1'","O=P/'cpu-os30-repro-v4';C=P/'cpu-final-paths-v1'");dst=ROOT/'tools/experiments/os30_fps25_20261005/phase10_cpu_final_repro_check.py';assert not dst.exists();dst.write_text(checks)
def seal():
 assert json.loads((O/'checks/result.json').read_text())['passed'];prep=json.loads((O/'prepared.json').read_text())
 for n,h in prep['source_hashes'].items():assert sha(ROOT/n)==h,n
 files=[p for p in O.rglob('*') if p.is_file() and not any('/checks/'+x+'/' in str(p) for x in ['flow','original','freshness-root','publish-root'])]
 (O/'manifest.json').write_text(json.dumps(dict(files={str(p.relative_to(O)):sha(p) for p in files},component_manifest_sha256=sha(C/'manifest.json'),no_actual_regeneration=True,no_map_or_fit=True,production_unchanged=True),indent=2)+'\n');print(sha(O/'manifest.json'))
if __name__=='__main__':globals()[sys.argv[1]]()
