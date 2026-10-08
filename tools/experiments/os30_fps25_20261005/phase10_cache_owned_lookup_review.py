#!/usr/bin/env python3
"""Independent source/result review; no new RTL, builds, maps or fits."""
import json, shutil
from pathlib import Path
from phase10_experiment import CAMP, ROOT, sha, write
P=CAMP/'phase10';C=P/'cache-owned-lookup-v3';OUT=P/'cache-owned-lookup-review-v3'
def main():
 OUT.mkdir(exist_ok=False)
 prepared=json.loads((C/'prepared.json').read_text());r=json.loads((C/'result.json').read_text());protocol=json.loads((C/'protocol/result.json').read_text())
 old=(C/'baseline.sv').read_text();new=(C/'gpu_color_depth_cache.sv').read_text()
 assert sha(C/'baseline.sv')==prepared['parent_sha256']==sha(P/'cache-write-speculate-v2/gpu_color_depth_cache.sv')
 assert sha(C/'gpu_color_depth_cache.sv')==prepared['source_sha256']==r['source_sha256']
 before='        R_ACCEPT: data_ra={hot_slot,word_index};'
 after='''        // R_ACCEPT owns addr. Do not route the live IDLE address mux
        // through this RAM input: hot_slot is equivalent in this state,
        // but includes an otherwise unowned upstream AW tag comparison.
        R_ACCEPT: data_ra={(hot0_valid && hot0_line==addr[ADDR_W-1:LINE_BITS])
                           ? hot0_slot : hot1_slot,word_index};'''
 assert old.count(before)==1 and old.replace(before,after)==new
 for statement in ('wire [ADDR_W-1:0] lookup_addr=state==IDLE\n    ? (REGISTER_READ_LOOKUP ? s_awaddr : (s_awvalid ? s_awaddr : s_araddr)) : addr;',
                   'wire hot0_hit=hot0_valid && hot0_line==lookup_addr[ADDR_W-1:LINE_BITS];',
                   'wire [INDEX_BITS-1:0] hot_slot=hot0_hit ? hot0_slot : hot1_slot;',
                   'wire [WORD_BITS-1:0] word_index=addr[2+:WORD_BITS];'):
  assert statement in old and statement in new
 assert r['passed'] and protocol['passed'] and r['component_runs']==18
 for a,b in [('baseline','default'),('old-enabled','enabled'),('old-ranged','ranged')]:
  assert protocol['runs'][a]==protocol['runs'][b]
  assert [x['seed']for x in protocol['runs'][a]]==[1,2,3]
 for name in protocol['runs']:
  cmd=json.loads((C/'protocol'/name/'command.json').read_text())
  assert '-GADDR_W=26'in cmd and '-GSET_BITS=6'in cmd and '-GWAYS=2'in cmd and '-GWORD_BITS=4'in cmd
  assert '-GREGISTER_READ_LOOKUP=1'in cmd and '-GREGISTER_FILL_HAZARD=1'in cmd
  for seed in (1,2,3):
   log=(C/'protocol'/name/f'seed{seed}.log').read_text();assert'PASS cycles='in log and'FAIL'not in log
 candidate_test=(C/'protocol/gpu_color_depth_cache.sv').read_text()
 assert 'assert(lookup_addr==addr);'in candidate_test and 'assert(data_ra=={hot_slot,word_index});'in candidate_test
 text='Independent review: cache-owned-lookup-v3\n\nPASS. The complete source difference is one combinational data_ra expression in R_ACCEPT plus its comment. No declaration, state, sequential assignment, write enable, metadata, RAM size or external handshake changes.\n\nExact identity: R_ACCEPT is26 and IDLE is1. In the changed case, lookup_addr is therefore addr, independent of live AW/AR and REGISTER_READ_LOOKUP. Substituting that equality into hot0_hit and hot_slot produces the new expression literally. Both branches preserve INDEX_BITS bits, the comparison uses the identical ADDR_W-LINE_BITS slice, and word_index is unchanged. This holds for every bit pattern of tags/valid/slot/address, even if neither hot entry hits or both do; hot1 validity is not an added assumption. It holds for every otherwise-legal parameter combination with the original nonempty slices. REGISTER_READ_LOOKUP=0 normally does not enter R_ACCEPT, and the identity still holds if it did. The exact-case selection also avoids any new unknown-state assumption.\n\nSynthesis intent: remove the IDLE live-AW address mux from the registered read data-RAM lookup cone. The RAM read remains on the same edge with the same owned address. Synthesis may share or refactor logic, so no area or routed timing improvement is inferred.\n\nExisting protocol proof is sufficient for this substitution:18 runs, selected26-bit address/64-set/two-way/16-word-line parameters, read/fill staging enabled, default/enabled/ranged cache pairs at seeds1..3. Each run passes the independent memory scoreboard and hostile RAM-collision/address-poison/reset/first-W cases. Each old/new configuration pair has exact cycle and memory-transaction counts. The candidate includes a live R_ACCEPT assertion of both lookup_addr==addr and new data_ra==old {hot_slot,word_index}. No new tests, maps, fitting or production edits were performed by this review.\n'
 (OUT/'REPORT.txt').write_text(text);shutil.copy2(Path(__file__),OUT/'recipe.py')
 write(OUT/'review.json',dict(passed=True,source_sha256=sha(C/'gpu_color_depth_cache.sv'),parent_sha256=sha(C/'baseline.sv'),literal_single_expression_reconstruction=True,all_legal_widths_same_bitvector_identity=True,no_new_state_register_or_cycle=True,selected_protocol_runs=18,no_new_tests=True,area_timing_unmeasured=True,production_changed=False))
 evidence=[p for p in C.rglob('*')if p.is_file()and'obj'not in p.parts]
 evidence+=[C/'protocol'/n/'obj/Vgpu_color_depth_cache'for n in protocol['runs']]
 evidence += [Path(__file__),Path(__file__).with_name('phase10_cache_owned_lookup.py'),Path(__file__).with_name('phase10_cache_full_write.py'),Path(__file__).with_name('phase10_cache_write_speculate.py')]
 evidence += list(OUT.iterdir())
 write(OUT/'manifest.json',dict(passed=True,source_sha256=sha(C/'gpu_color_depth_cache.sv'),deployment_ready=False,evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in sorted(set(evidence))}))
 print(sha(OUT/'manifest.json'))
if __name__=='__main__':main()
