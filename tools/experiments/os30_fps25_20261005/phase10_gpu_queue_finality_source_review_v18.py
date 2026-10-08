#!/usr/bin/env python3
"""Independent exact finality rebase/source and retained-evidence review."""
from pathlib import Path
import hashlib,json,re,shutil
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
S=P/'gpu-queue-finality-v18';O=P/'gpu-queue-finality-source-review-v18';N=P/'gpu-queue-finality-review-v18';B=P/'gpu-control-owner-v1/gpu_core.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2,sort_keys=True);f.write('\n')
def manifest(d):
 m=json.loads((d/'manifest.json').read_text());assert m['passed'];n=0
 for k,h in m.get('files',{}).items():assert sha(d/k)==h,k;n+=1
 for k,h in m.get('evidence_sha256',{}).items():assert sha(R/k)==h,k;n+=1
 return n

def main():
 assert not O.exists();n=manifest(S)+manifest(N)
 b=B.read_text();c=(S/'gpu_core.v').read_text();assert sha(B)=='e75ac93db5483ee8525c44ef1aeed4e610f8deecf8fa42765997367873dd4f15'
 old="wire fbwq_head_chain_final = (fbwq_start_burst_words == 4'd8)\n                          || (fbwq_count > {1'b0, fbwq_start_burst_words});"
 a=c.index('// A chain is final at eight queued words, or at any broken link');z=c.index(';',a)+1;new=c[a:z]
 expected="// A chain is final at eight queued words, or at any broken link\n// between existing entries. Do not encode its length just to test finality.\n// The actual AWLEN/remaining encoder above is unchanged.\nwire fbwq_head_chain_final = (fbwq_count >= 5'd8)"
 for i in range(6):
  ptr='fbwq_rd_ptr'+('' if i==0 else '_'+str(i));expected+=f"\n    || ((fbwq_count >= 5'd{i+2}) && !fbwq_link_next[{ptr}])"
 expected+=';';assert new==expected
 assert b.count(old)==1 and b.replace(old,new,1)==c and c.replace(new,old,1)==b
 assert (P/'gpu-queue-finality-v15/gpu_core.v').read_text().count(new)==1
 decl=lambda s:re.findall(r'^\s*(?:input|output|inout|reg|parameter|localparam)\s+.*$',s,re.M)
 assert decl(b)==decl(c);assert [l for l in b.splitlines() if '<=' in l]==[l for l in c.splitlines() if '<=' in l]
 result=json.loads((S/'result.json').read_text());assert result['passed'] and result['literal']['all_combinations']==33554432 and result['native_pairs']==16 and result['abort_pairs']==4
 assert result['live_finality_checks']==30829320
 native=json.loads((N/'result.json').read_text());abort=json.loads((N/'abort/result.json').read_text())
 for x in native['rows']+abort['rows']:assert x['bytes_equal'] and x['cycles_equal']
 O.mkdir();shutil.copy2(__file__,O/'recipe.py')
 put(O/'review.json',dict(passed=True,blocking_findings=[],candidate_sha256=sha(S/'gpu_core.v'),parent_sha256=sha(B),evidence_rehashed=n,exact_prior_v15_predicate=True,single_reversible_hunk=True,all_sequential_lines_and_declarations_exact=True,
 algebra='For count>=8, either the encoded prefix reaches8 or it is shorter than occupancy; both forms are final. For count<8, final means an existing link breaks before the last queued entry. Six count-qualified links cover counts2..7. Empty/singleton remain false. No AWLEN, queue ownership, pointer arithmetic or register timing changes.',
 proof_scope='Author freshly exhausted all32 counts x16 pointers x65536 link patterns, including every legal occupancy0..16. Sixteen native/boundary and four real-MMIO reset pairs retain exact output/busy/total cycles;30,829,320 live finality comparisons pass. Independent review repeats no simulation.',
 limits=['Selected GPU_WRITE_GATHER=0; no separate gather-enabled render qualification.','Reset fixtures precede first pixels, not arbitrary active-AXI abort.','Native component gate only; no area/routed-timing claim.']))
 files=[Path(__file__),B,S/'manifest.json',S/'result.json',S/'prepared.json',S/'gpu_core.v',N/'manifest.json',P/'gpu-queue-finality-v15/gpu_core.v',O/'recipe.py',O/'review.json']
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p) for p in files}));print(sha(O/'manifest.json'))
if __name__=='__main__':main()
