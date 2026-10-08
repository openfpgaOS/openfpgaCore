#!/usr/bin/env python3
"""Extend sealed os30-only generation proposal with exact qualified frontend."""
import difflib, hashlib, json, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=P/'cpu-os30-repro-v1';O=P/'cpu-os30-repro-v2';V=Path('src/fpga/vendor/vexriscv')
BASE=P/'cpu-fp-rs3-mux-v1/VexiiRiscv.v';CPU=P/'cpu-rvc-control-v3/VexiiRiscv.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def prepare():
    O.mkdir(exist_ok=False);f=O/'proposed';shutil.copytree(OLD/'proposed',f);shutil.copytree(OLD/'controls',O/'controls')
    p=f/V/'generate_vexii.sh';s=p.read_text()
    s=rep(s,'OS30_FP_RS3_GROUPED=0   # Exact checked os30 FP RS3 selection factoring.',
          'OS30_FP_RS3_GROUPED=0   # Exact checked os30 FP RS3 selection factoring.\nOS30_FRONTEND_FLAT=0    # Exact checked os30 frontend Boolean factoring.')
    s=rep(s,'\nmv -f "$OUTPUT" "$FINAL_OUTPUT"','''
# This pass binds the exact output of the qualified RS3/helper chain.
if [ "$OS30_FRONTEND_FLAT" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_frontend.pl" "$VARIANT" "$OUTPUT"
fi

mv -f "$OUTPUT" "$FINAL_OUTPUT"''');p.write_text(s)
    p=f/V/'configs/os30.cfg';p.write_text(rep(p.read_text(),'OS30_FP_RS3_GROUPED=1\n','OS30_FP_RS3_GROUPED=1\nOS30_FRONTEND_FLAT=1\n'))
    p=f/'src/fpga/targets/pocket/Makefile';p.write_text(rep(p.read_text(),'              $(if $(filter os30,$(VARIANT)),$(VEXII_DIR)/../factor_os30_fp_rs3.pl) \\\n','              $(if $(filter os30,$(VARIANT)),$(VEXII_DIR)/../factor_os30_fp_rs3.pl $(VEXII_DIR)/../factor_os30_frontend.pl) \\\n'))
    p=f/'tools/tests/test_pocket_cpu_freshness.py';p.write_text(rep(p.read_text(),"'factor_os30_fp_rs3.pl',","'factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl',"))
    p=f/'tools/tests/test_vexii_publish.py';s=p.read_text();s=rep(s,"'factor_os30_fp_rs3.pl']:","'factor_os30_fp_rs3.pl', 'factor_os30_frontend.pl']:");s=rep(s,"'OS30_FP_RS3_GROUPED']:","'OS30_FP_RS3_GROUPED', 'OS30_FRONTEND_FLAT']:");p.write_text(s)
    a=BASE.read_text().splitlines(True);b=CPU.read_text().splitlines(True);sm=difflib.SequenceMatcher(None,a,b,autojunk=False)
    replacements=[]
    for g in sm.get_grouped_opcodes(3):
        replacements.append((''.join(a[g[0][1]:g[-1][2]]),''.join(b[g[0][3]:g[-1][4]])))
    s='''#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# os30-only zero-cycle frontend factoring. Apply AFTER factor_os30_fp_rs3.pl.
# Hash-locked to the qualified output; unknown generator changes fail closed.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_frontend.pl os30 <staging-netlist>\\n";
open my $in,'<:raw',$path or die "$path: $!\\n";
my $source=do {local $/; <$in>}; close $in;
my $before='''+repr(sha(BASE))+''';
my $after='''+repr(sha(CPU))+''';
exit 0 if sha256_hex($source) eq $after;
sha256_hex($source) eq $before or die "Unqualified os30 frontend input; retaining prior output\\n";
'''
    for i,(old,new) in enumerate(replacements):
        s+='''{
 my $old=<<'OLD'''+str(i)+"';\n"+old+'OLD'+str(i)+'''\n my $new=<<'NEW'''+str(i)+"';\n"+new+'NEW'+str(i)+'''
 my $count=()=$source=~/\\Q$old\\E/g;
 $count==1 or die "Expected one qualified frontend block\\n";
 $source=~s/\\Q$old\\E/$new/;
}
'''
    s+='''sha256_hex($source) eq $after or die "Unexpected transformed frontend hash\\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\\n";
close $out or die "$temporary: $!\\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\\n";
rename($temporary,$path) or die "$path rename: $!\\n";
print "os30 frontend exact output $after\\n";
'''
    (f/V/'factor_os30_frontend.pl').write_text(s)
    files=sorted(p for p in f.rglob('*') if p.is_file());patch=''
    for p in files:
        n=p.relative_to(f);old=(ROOT/n).read_text() if (ROOT/n).exists() else ''
        patch+=''.join(difflib.unified_diff(old.splitlines(True),p.read_text().splitlines(True),fromfile='a/'+str(n) if old else '/dev/null',tofile='b/'+str(n)))
    (O/'candidate.patch').write_text(patch)
    (O/'prepared.json').write_text(json.dumps(dict(parent_proposal_manifest_sha256=sha(OLD/'manifest.json'),component_manifest_sha256=sha(P/'cpu-rvc-control-v3/manifest.json'),parent_cpu_sha256=sha(BASE),candidate_cpu_sha256=sha(CPU),replacement_blocks=len(replacements),source_hashes={str(p.relative_to(f)):sha(ROOT/p.relative_to(f)) for p in files if (ROOT/p.relative_to(f)).exists()},production_modified=False),indent=2)+'\n')
    shutil.copy2(__file__,O/'recipe.py');print(O)
if __name__=='__main__':globals()[sys.argv[1]]()
