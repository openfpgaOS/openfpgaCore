#!/usr/bin/env python3
"""Independent exact emitted audit for narrower two-anchor register reuse."""
from pathlib import Path
import hashlib,json,re,subprocess
ROOT=Path(__file__).resolve().parents[3];ART=ROOT/'docs/measurements/os30-dual-20261004'
BASE=ART/'texture-offer-v1/gpu_core.v';SRC=ART/'gpu-anchor-stage-v1';OUT=ART/'gpu-anchor-stage-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    old=BASE.read_text();new=(SRC/'gpu_core.v').read_text()
    assert sha(BASE)=='2d4870c5b1c687cb51a3e60aef0fa0d31372bcea4c4dd521a0c825dd8ba24ec7'
    assert sha(SRC/'gpu_core.v')=='ecb667f27547251c7921623c3438233b0ce49dce971ff1b26991fed775cb4f73'
    anchor='                DRV_PROD_L0: begin\n'
    added='''                    // Reuse the not-yet-live anchor registers as coordinate staging.
                    // Sorting is complete; raw vertices/order hold until DRV_DELTA.
                    dd_x0 <= dvx[dv_ord[0]];
                    dd_y0 <= dvy[dv_ord[0]];
'''
    before='''                    sx0 = dvx[dv_ord[0]]; sx1 = dvx[dv_ord[1]]; sx2 = dvx[dv_ord[2]];
                    sy0 = dvy[dv_ord[0]]; sy1 = dvy[dv_ord[1]]; sy2 = dvy[dv_ord[2]];'''
    after='''                    sx0 = dd_x0; sx1 = dvx[dv_ord[1]]; sx2 = dvx[dv_ord[2]];
                    sy0 = dd_y0; sy1 = dvy[dv_ord[1]]; sy2 = dvy[dv_ord[2]];'''
    assert old.count(anchor)==old.count(before)==1
    assert old.replace(anchor,anchor+added).replace(before,after)==new
    assert new.replace(anchor+added,anchor).replace(after,before)==old
    captures=dict(re.findall(r'(dd_[xy]0) <= ([^;]+);',added))
    oldread=dict(re.findall(r'(s[xy][012]) = ([^;]+);',before))
    newread=dict(re.findall(r'(s[xy][012]) = ([^;]+);',after))
    assert len(captures)==2
    for k,rhs in newread.items():assert captures.get(rhs,rhs)==oldread[k]
    # Reuse the already independent source-bound phase review, whose source
    # and every state/entry/reset/order/raw-coordinate statement remain exact.
    phase=ART/'gpu-coordinate-stage-emitted-audit-v1/manifest.json';p=json.loads(phase.read_text())
    assert p['reference_sha256']==sha(BASE) and p['passed']
    assert p['all_original_dds_consumers_closed'] and p['order_and_coordinates_stable_between_capture_and_delta']
    assert p['all_three_entries_restart_sort'] and p['reset_and_abort_invalidate_scratch_before_later_observation']
    # Four edge-value registers retain all of their original write sites.
    for name in ['dd1x','dd2x','dd1y','dd2y']:
        assert re.findall(r'\b'+name+r'\s*<=\s*[^;]+;',old)==re.findall(r'\b'+name+r'\s*<=\s*[^;]+;',new)
    def blocks(s):
        body=s[s.index('            case (dstate)'):s.index('            end // INCLUDE_VERT_TRI')]
        matches=list(re.finditer(r'^                (DRV_\w+|default):',body,re.M))
        return {m[1]:body[m.start():matches[i+1].start() if i+1<len(matches) else body.rindex('            endcase')].rstrip() for i,m in enumerate(matches)}
    proof=(SRC/'proof.sv').read_text()
    for label,s in [('original',old),('candidate',new)]:
        mod=re.search(r'module '+label+r'\(.*?endmodule',proof,re.S)[0];bb=blocks(s)
        for key in ['DRV_PROD_L0','DRV_DELTA','DRV_PLANE_NUM','DRV_PLANE_ORG']:
            actual=bb[key].replace('DRV_PLANE_NUM:',"6'd63, DRV_PLANE_NUM:") if key=='DRV_PLANE_NUM' else bb[key]
            assert actual in mod,key
    OUT.mkdir(exist_ok=False)
    with (OUT/'proof-rerun.log').open('w') as log:subprocess.run([str(SRC/'obj/Vcoordinate_proof')],stdout=log,stderr=subprocess.STDOUT,check=True)
    result=dict(passed=True,candidate_sha256=sha(SRC/'gpu_core.v'),exact_complete_source_reconstruction_and_inverse=True,
        only_two_existing_anchor_captures_and_two_operand_reads_changed=True,
        four_edge_register_writers_and_four_slot1_slot2_reads_exact=True,
        all_six_operands_identical_by_two16bit_capture_substitutions=True,
        source_bound_independent_phase_review=str(phase),phase_review_sha256=sha(phase),
        state_entry_reset_order_and_coordinate_stability_logic_unchanged=True,
        actual_extracted_blocks_verified=True,focused_binary_rerun_pass=True,
        whole_gpu_runtime='Separate; not implied by this source/phase audit.',new_registers=0,new_cycles=0)
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
