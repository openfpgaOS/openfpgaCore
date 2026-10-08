#!/usr/bin/env python3
"""Private span-v5 area screen: resume only the changing part of a span."""
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
SOURCE = P / 'span-lookahead-v3/gpu_core.v'
OUT = P / 'span-lookahead-v5'
PARENT_SHA = 'ac840c18f263714257eebbb219486d9d345657c4db52bbbc69dd2ec6b4032a5d'

TASK = '''
`ifdef INCLUDE_SPAN_LOOKAHEAD
// Resume a non-direct span of the SAME triangle. The initial EMIT already
// installed material, masks, modes, plane steps and clamp constants. They
// remain live for older fragments, and do not need another write-enable/mux
// cone here. Only per-span accumulators and perspective scheduling restart.
// Call exclusively through the qualified lookahead handoff below.
task spanprod_resume_generated_span;
    begin
        sp_count <= spanprod_cur_count;
        sp_fb_addr <= spanprod_fb_addr_r;
        sp_light_q <= spanprod_light_start_r;
        sp_R_q <= spanprod_R_start_r;
        sp_B_q <= spanprod_B_start_r;
        sp_Dr_q <= spanprod_Dr_start_r;
        sp_Dg_q <= spanprod_Dg_start_r;
        sp_Db_q <= spanprod_Db_start_r;
        // Constant-Z perspective setup clears PERSP in the live flags. Each
        // next span must restore it even though the command flags are stable.
        sp_flags <= spanprod_attr_persp
                  ? (spanprod_flags | (4'b0001 << SPAN_PERSP))
                  : (spanprod_flags & ~(4'b0001 << SPAN_PERSP));
        sp_z_addr <= spanprod_z_addr_r;
        sp_zc_la <= spanprod_rgb_mode_w ? spanprod_depth_start_r
                                       : spanprod_attr2_start_r;
        g_zwarm <= (INCLUDE_DIRECT_COLOR != 0) ? 2'd2 : 2'd0;
        sp_z_value <= (EFF_Q29 && spanprod_use_q29
                           && (spanprod_z_write || spanprod_z_test))
                        ? q29_restore_z_saturating(spanprod_attr2_start_r,
                                                   spanprod_q29_attr_shift)
                        : spanprod_attr2_start_r;
        if (GPU_ENABLE_PERSP && spanprod_attr_persp) begin
            sp_s <= 32'sd0;
            sp_t <= 32'sd0;
            sp_sstep <= 32'sd0;
            sp_tstep <= 32'sd0;
            sp_scc <= 32'sd0;
            sp_tcc <= 32'sd0;
            sp_sZ <= spanprod_attr0_start_r;
            sp_tZ <= spanprod_attr1_start_r;
            sp_zinv <= spanprod_attr2_start_r;
            persp_active <= GPU_ENABLE_PERSP;
            persp_first_done <= 1'b0;
            persp_prev_valid <= 1'b0;
            persp_prev_anchor_s <= 32'sd0;
            persp_prev_anchor_t <= 32'sd0;
            persp_pend_scc <= 32'sd0;
            persp_pend_tcc <= 32'sd0;
            persp_seg_a_ready <= 1'b0;
            persp_seg_b_ready <= 1'b0;
            persp_swap_pending <= 1'b0;
            persp_pss <= PSS_IDLE;
            persp_pass <= PSS_PASS_ANCHOR;
            sp_seg_left <= 4'd0;
        end else begin
            sp_s <= spanprod_attr0_start_r;
            sp_t <= spanprod_attr1_start_r;
            sp_sstep <= spanprod_attr0_step_pre;
            sp_tstep <= spanprod_attr1_step_pre;
            sp_sZ <= 32'sd0;
            sp_tZ <= 32'sd0;
            sp_zinv <= 32'sd0;
            persp_active <= 1'b0;
            persp_first_done <= 1'b0;
            persp_swap_pending <= 1'b0;
            persp_pss <= PSS_IDLE;
            persp_pass <= PSS_PASS_ANCHOR;
            sp_seg_left <= 4'd0;
        end
        src_done <= 1'b0;
    end
endtask
`endif

'''


def main():
    original = SOURCE.read_bytes()
    assert hashlib.sha256(original).hexdigest() == PARENT_SHA
    text = original.decode()
    insertion = '// N64-style monotonic floating-point depth code'
    assert text.count(insertion) == 1
    text = text.replace(insertion, TASK + insertion)
    call = '                spanprod_load_generated_span;\n                if(span_la_chunk)begin'
    assert text.count(call) == 1
    text = text.replace(call, call.replace('spanprod_load_generated_span',
                                          'spanprod_resume_generated_span'))
    OUT.mkdir(exist_ok=False)
    (OUT / 'gpu_core.v').write_text(text)
    (OUT / 'recipe.py').write_bytes(Path(__file__).read_bytes())
    (OUT / 'candidate.patch').write_text(''.join(difflib.unified_diff(
        original.decode().splitlines(True), text.splitlines(True),
        fromfile='v3/gpu_core.v', tofile='v4/gpu_core.v')))
    (OUT / 'prepared.json').write_text(json.dumps(dict(
        private_only=True, parent_sha256=PARENT_SHA,
        source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        recipe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        functional_proof=False, fitted=False, deployed=False,
        purpose='Reduce repeated material/plane write logic at same-triangle span handoff; unchanged schedule.'
    ), indent=2) + '\n')
    print(OUT)


if __name__ == '__main__':
    main()
