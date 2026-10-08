#!/usr/bin/env python3
"""Independent read-only count ownership/terminal predicate review."""
import json,re
from pathlib import Path
from phase10_compact_v7_review import ROOT,P,sha,bind,verify,evidence
C=P/'gpu-span-control-v17'
R=P/'gpu-span-control-review-v17'
O=P/'gpu-span-control-source-review-v17'
BASE=P/'compact-lod5-core-v12/frozen/src/fpga/common/gpu_core.v'

def main():
    for p in (C/'manifest.json',R/'manifest.json'): assert verify(p)['passed']
    prep=bind(C/'prepared.json');result=bind(C/'result.json')
    old=bind(BASE);new=bind(C/'gpu_core.v')
    assert prep['parent_sha256']==sha(BASE)=='32de956cdfc6a85e627aed6772c805494c4475a92d9193ece5673d7650aff177'
    assert prep['candidate_sha256']==sha(C/'gpu_core.v')=='7076c8e7c9d41361aa01bda2612089dcff924cc38885d958e2956a39ca0037a2'
    assert result['passed'] and result['exact_cycle_pixel_pairs']>=16
    # Reconstruct every data/valid paired setter from its own payload, then
    # require all remaining source text exact after the two stated changes.
    expected=old
    pattern=r'(store_count\(([^,;]+),\s*([^;]+)\);(?:[^\n]*?|\s*)spanprod_cnt_valid\[([^\n]+?)\]\s*<=\s*)1\'b1;'
    sites=[]
    def replace(m):
        slot,payload,maskslot=(m.group(i).strip() for i in (2,3,4))
        assert slot==maskslot
        sites.append((slot,payload))
        return m.group(1)+'('+payload+" != 16'd0);"
    expected,n=re.subn(pattern,replace,expected);assert n==10
    expected=expected.replace("//   cnt_valid[L]  — lane L's count word written since the last clear site\n//                   (compact w0 / long-form w29 / prepare_next_record_chunk).\n//                   Clear = the old count[L]<=0; SELECT reads 0 when clear.","//   cnt_valid[L]  — lane L's count word was written AND is nonzero since\n//                   the last clear (compact w0 / long w29 / next chunk).\n//                   Zero writes clear it too. SELECT's count remains exact,\n//                   and its nonzero flag no longer depends on the count RAM.")
    expected=expected.replace("assign spanprod_cur_nonzero = (spanprod_cur_count != 16'd0);",'assign spanprod_cur_nonzero = spanprod_cur_valid && spanprod_cur_count_valid;')
    terminal=new[new.index('// This is the Boolean terminal condition'):new.index('// Launch dispatch')]
    expected=expected.replace('// Launch dispatch',terminal+'// Launch dispatch',1)
    expected,n=re.subn(r'spanprod_next_calc\(spanprod_calc_step\)\s*==\s*SPANPROD_STEP_NONE','spanprod_calc_terminal',expected)
    assert n==2 and expected==new
    assert len(re.findall(r'spanprod_cnt_valid\s*<=\s*4\x27b0000;',old))==4
    # There are no other count-memory writes, mask setters or consumers.
    for s in (old,new):
        assert s.count('spanprod_count[spanprod_count_wa] <= spanprod_count_wd;')==1
        assert len(re.findall(r'\bstore_count\(',s))==10
        assert s.count('spanprod_cur_count_valid <= spanprod_cnt_valid[spanprod_read_idx];')==1
        assert s.count('spanprod_cur_count_raw <= spanprod_count[spanprod_read_idx];')==1
    literal=bind(C/'literal/result.json')
    assert literal['passed'] and literal['terminal']==16384 and literal['counts']==262144
    assert literal['transitions']==4194304 and literal['negative']>0
    live=bind(R/'span-control-result.json')
    assert live['passed'] and live['pairs']>=16 and all(v>0 for v in live['totals'].values())
    instr=bind(ROOT/'tools/experiments/os30_fps25_20261005/phase10_gpu_span_control_acceptance.py')
    for token in ['assert n==14','proof_cnt_valid_old <= 4\'b0000','proof_cur_count_valid_old <= proof_cnt_valid_old[spanprod_read_idx]',
      'count RAM read/write ownership collision','original masked span count differs','original span nonzero differs','original encoded terminal differs']:
        assert token in instr,token
    for row in live['runs']:
        log=bind(R/row['log']); assert 'FATAL' not in log and 'SPAN_CONTROL_AUDIT' in log
    review=dict(passed=True,source_review_only=True,no_duplicate_simulation=True,
      candidate_sha256=sha(C/'gpu_core.v'),parent_sha256=sha(BASE),count_write_sites=sites,
      architectural_flip_flops_added=0,states_added=0,cycles_added=0,
      exact_cycle_pixel_pairs=result['exact_cycle_pixel_pairs'],literal=literal,live=live['totals'],
      findings=[
        'Four compact setters, four long-form setters and two walker/refill setters now record payload!=0 beside the same owned count write. All four reset/chunk mask clears retain exact locations and priority. No count RAM/data/write-enable or address change occurs.',
        'The inductive invariant is new_mask[lane]=old_mask[lane] AND (RAM_count[lane]!=0). Clear establishes it, every write preserves it including zero overwrite, and untouched lanes preserve it. SELECT captures RAM and mask together; valid-gated count and nonzero are exactly equal. The actual native shadow mirrors original mask writes at all14 sites and checks every live cycle, including clear/reuse/zero records; it also rejects simultaneous SELECT/write ownership collision.',
        'For all16 calc-step codes:0,1,5,8,9,11,12 are nonterminal;2/3 depend on perspective/colormap/truecolor as written;4 on any RGB/indexed/clip command;10 on combine;6,7,13,14,15 are terminal. This exactly expands next_calc==NONE, including default encodings. Both serial and lookahead consumers retain their current-cycle state transition and next-step assignment.',
        'All other source bytes, clocks, reset, register declarations, DSP operands, queue/fence/MMIO ownership and command scheduling are unchanged. Kept terminal logic and earlier count metadata target the measured combinational dependencies; no timing or mapped-area improvement is inferred.',
        'Literal/transition proof and actual replay results are rebound rather than rerun. The proof is binary logic plus bounded native traffic, not a universal X-propagation or whole-game/native CPU guarantee.'
      ],deployment_ready=False,map_or_fit_invoked=False)
    O.mkdir(exist_ok=False)
    (O/'review.json').write_text(json.dumps(review,indent=2)+'\n')
    (O/'REPORT.txt').write_text('\n\n'.join(review['findings'])+'\n')
    (O/'recipe.py').write_bytes(Path(__file__).read_bytes())
    bind(Path(__file__))
    manifest=dict(files={p.name:sha(p) for p in O.iterdir() if p.is_file()},evidence_sha256=evidence)
    (O/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(sha(O/'manifest.json'))
if __name__=='__main__':main()
