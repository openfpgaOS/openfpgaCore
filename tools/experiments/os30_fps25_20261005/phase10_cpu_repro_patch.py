#!/usr/bin/env python3
"""Prepare private os30-only exact-output generator hooks; never run sbt."""
import difflib,hashlib,json,os,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
O=P/'cpu-os30-repro-v1';C=P/'cpu-fp-rs3-mux-v1';Q=P/'cpu-queues-v1'
V=Path('src/fpga/vendor/vexriscv');M=Path('src/fpga/targets/pocket/Makefile')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
 O.mkdir(exist_ok=False);f=O/'proposed';(f/V/'configs').mkdir(parents=True)
 files=[V/'generate_vexii.sh',V/'configs/os30.cfg',M,Path('tools/tests/test_pocket_cpu_freshness.py'),Path('tools/tests/test_vexii_publish.py')]
 for n in files:
  (f/n).parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/n,f/n)
 p=f/V/'generate_vexii.sh';s=p.read_text()
 s=rep(s,'LSU_L1_WAYS=2            # D$ associativity (sets x ways x 64 B line)', '''FETCH_REFILL_COUNT=2    # I$ outstanding refill slots (os30 qualifies one).
LSU_REFILL_COUNT=2      # D$ outstanding refill slots.
LSU_WRITEBACK_COUNT=2   # D$ outstanding writeback slots.
OS30_FP_RS3_GROUPED=0   # Exact checked os30 FP RS3 selection factoring.
LSU_L1_WAYS=2            # D$ associativity (sets x ways x 64 B line)''')
 s=rep(s,'--fetch-l1-refill-count=2','--fetch-l1-refill-count=$FETCH_REFILL_COUNT')
 s=rep(s,'--lsu-l1-refill-count=2 --lsu-l1-writeback-count=2','--lsu-l1-refill-count=$LSU_REFILL_COUNT --lsu-l1-writeback-count=$LSU_WRITEBACK_COUNT')
 s=rep(s,'\nmv -f "$OUTPUT" "$FINAL_OUTPUT"','''
# Keep this final: its hash guard includes every preceding qualified helper.
# Failure leaves the previous published CPU intact, as with the other passes.
if [ "$OS30_FP_RS3_GROUPED" = 1 ]; then
    perl "$SCRIPT_DIR/factor_os30_fp_rs3.pl" "$VARIANT" "$OUTPUT"
fi

mv -f "$OUTPUT" "$FINAL_OUTPUT"''');p.write_text(s)
 p=f/V/'configs/os30.cfg';p.write_text(p.read_text()+'''
# Qualified queue-only profile: cache capacities, ISA and arithmetic unchanged.
# os30_worker sources this file too; keep that diagnostic profile unchanged.
if [ "$VARIANT" = os30 ]; then
FETCH_REFILL_COUNT=1
LSU_REFILL_COUNT=1
LSU_WRITEBACK_COUNT=1
LSU_SB_SLOTS=1
LSU_SB_OPS=4
# Applies only after the existing os30 post-generation transformations.
OS30_FP_RS3_GROUPED=1
fi
''')
 p=f/M;s=p.read_text();s=rep(s,'              $(VEXII_DIR)/../optimize_os20_timing.pl \\\n','              $(VEXII_DIR)/../optimize_os20_timing.pl \\\n              $(if $(filter os30,$(VARIANT)),$(VEXII_DIR)/../factor_os30_fp_rs3.pl) \\\n');p.write_text(s)
 p=f/'tools/tests/test_pocket_cpu_freshness.py';s=p.read_text();s=rep(s,"              'optimize_os20_timing.pl',","              'optimize_os20_timing.pl', 'factor_os30_fp_rs3.pl',");p.write_text(s)
 p=f/'tools/tests/test_vexii_publish.py';s=p.read_text();s=rep(s,"'retime_fetch_pipeline.pl', 'optimize_os20_timing.pl']:","'retime_fetch_pipeline.pl', 'optimize_os20_timing.pl', 'factor_os30_fp_rs3.pl']:");s=rep(s,"['FPU_MATCH_REG', 'FETCH_PIPELINE_HOLD', 'OS20_TIMING']:","['FPU_MATCH_REG', 'FETCH_PIPELINE_HOLD', 'OS20_TIMING', 'OS30_FP_RS3_GROUPED']:");p.write_text(s)
 header=(C/'baseline.v').read_text().split('\n\n',1)[0]
 perl='''#!/usr/bin/env perl
# SPDX-License-Identifier: Apache-2.0
# Exact os30-only FP RS3 factoring. Run last, on the unpublished staging RTL.
# Hashes bind the qualified queue profile and every prior helper. A changed
# emitter/body must be qualified explicitly; do not weaken this check to regex.
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);
use File::Temp qw(tempfile);
my ($variant,$path)=@ARGV;
@ARGV==2 && $variant eq 'os30' or die "Usage: factor_os30_fp_rs3.pl os30 <staging-netlist>\\n";
open my $in, '<:raw', $path or die "$path: $!\\n";
my $source=do {local $/; <$in>}; close $in;
my $before='''+repr(sha(C/'baseline.v'))+''';
my $after='''+repr(sha(C/'VexiiRiscv.v'))+''';
exit 0 if sha256_hex($source) eq $after;
# Source archives may omit Git metadata. Normalize only the leading emitter
# comments, after checking their exact grammar; the complete body remains bound.
my ($header,$body)=split(/\\n\\n/,$source,2);
defined($body) && $header =~ /\\A\\/\\/ Generator : [^\\n]+\\n\\/\\/ Component : VexiiRiscv(?:\\n\\/\\/ Git hash  : [^\\n]+)?\\z/
    or die "Unexpected generator provenance header\\n";
my $qualified_header=<<'HEADER';
'''+header+'''
HEADER
chomp $qualified_header;
$source=$qualified_header."\\n\\n".$body;
my $hash=sha256_hex($source);
$hash eq $before || $hash eq $after
    or die "Unqualified os30 CPU body ($hash); retaining prior output\\n";
if($hash eq $before){
 my $old=<<'OLD';
'''+(C/'original-assignment.txt').read_text()+'''OLD
 my $new=<<'NEW';
'''+(C/'candidate-assignment.txt').read_text()+'''NEW
 chomp $old; chomp $new;
 my $count=()=$source=~/\\Q$old\\E/g;
 $count==1 or die "Expected one qualified FP RS3 assignment\\n";
 $source=~s/\\Q$old\\E/$new/;
}
sha256_hex($source) eq $after or die "Unexpected FP RS3 transformed hash\\n";
my ($out,$temporary)=tempfile($path.'.XXXXXX',UNLINK=>0);
binmode $out; print {$out} $source or die "$temporary: $!\\n";
close $out or die "$temporary: $!\\n";
chmod((stat($path))[2]&07777,$temporary) or die "$temporary chmod: $!\\n";
rename($temporary,$path) or die "$path rename: $!\\n";
print "os30 FP RS3 exact output $after\\n";
'''
 (f/V/'factor_os30_fp_rs3.pl').write_text(perl)
 # Keep every other variant's config and checked helper as an immutable test input.
 shutil.copytree(ROOT/V/'configs',O/'controls/configs')
 shutil.copytree(Q/'helpers',O/'controls/helpers')
 shutil.copy2(Q/'source/VexiiRiscv.v',O/'controls/raw-emitted.v')
 shutil.copy2(ROOT/V/'generate_vexii.sh',O/'controls/generate_vexii.sh')
 save(O/'prepared.json',dict(parent_cpu_sha256=sha(C/'baseline.v'),candidate_cpu_sha256=sha(C/'VexiiRiscv.v'),source_hashes={str(n):sha(ROOT/n) for n in files},raw_emitter_sha256=sha(O/'controls/raw-emitted.v'),queue_recipe_sha256=sha(Q/'recipe.json'),regenerated=False,production_modified=False))
 patch=''
 for n in [*files,V/'factor_os30_fp_rs3.pl']:
  old=(ROOT/n).read_text() if (ROOT/n).exists() else ''
  patch+=''.join(difflib.unified_diff(old.splitlines(True),(f/n).read_text().splitlines(True),fromfile='a/'+str(n) if old else '/dev/null',tofile='b/'+str(n)))
 (O/'candidate.patch').write_text(patch);shutil.copy2(__file__,O/'recipe.py')
 print('prepared',O)
if __name__=='__main__':globals()[sys.argv[1]]()
