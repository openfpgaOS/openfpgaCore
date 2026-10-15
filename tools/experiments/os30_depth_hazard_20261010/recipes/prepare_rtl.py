#!/usr/bin/env python3
"""Create one isolated cache-specific depth-read hazard prototype."""
from pathlib import Path
import difflib
import hashlib
import json

HERE = Path(__file__).resolve().parent
P = HERE.parents[1]
BASE = P / 'cached-cpu-v1/native-sdram-v2'
REL = 'frozen/src/fpga/common/gpu_core.v'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
bound = json.loads((BASE / 'prepared.json').read_text())
assert sha(BASE / REL) == bound['source_sha256'][REL]
before = (BASE / REL).read_text()
text = before


def replace(old, new):
    global text
    assert text.count(old) == 1, old
    text = text.replace(old, new)


replace('// Barrier for the z / blend-dst window fills.', '''// Cache-specific depth-only selective wait. The upstream render cache owns
// an accepted AW through its final W/data commit, and accepts AR only in IDLE
// with no AW presented. Therefore popped FIFO writes are already ordered
// ahead of reads by that cache; its own posted-writeback scoreboard protects
// fills that could otherwise pass dirty SDRAM writes. Only not-yet-popped
// FIFO entries need a GPU scoreboard. Do not use this with an uncached port.
`ifdef INCLUDE_GPU_DEPTH_SELECTIVE_WAIT
`ifdef INCLUDE_GPU_RENDER_CACHE
`define GPU_DEPTH_SELECTIVE_ACTIVE
`endif
`endif
`ifdef GPU_DEPTH_SELECTIVE_ACTIVE
// DEPTH_HAZARD_QUEUE_BEGIN: source-bound focused tests extract this block.
localparam DH_TAG_W = 6;
function [DH_TAG_W-1:0] depth_hazard_tag(input [GPU_ADDR_W-1:0] a);
    integer k;
    begin
        depth_hazard_tag = {DH_TAG_W{1'b0}};
        for (k = 6; k < GPU_ADDR_W; k = k + 1)
            depth_hazard_tag[(k-6) % DH_TAG_W] =
                depth_hazard_tag[(k-6) % DH_TAG_W] ^ a[k];
    end
endfunction
reg [DH_TAG_W-1:0] depth_read_tag;
reg [DH_TAG_W-1:0] depth_q_tag [0:FBWQ_DEPTH-1];
reg [FBWQ_DEPTH-1:0] depth_q_valid;
reg depth_pending_valid;
reg [3:0] depth_pending_index;
reg [DH_TAG_W-1:0] depth_pending_tag;
wire depth_q_push = fbwq_swap_now || fbwq_stage_drain_now;
// The pending tag avoids extending the late enqueue/swap decision through
// an array write enable. It is included in every read hazard decision.
always @(posedge clk) begin
    if (!reset_n || soft_reset) begin
        depth_q_valid <= {FBWQ_DEPTH{1'b0}};
        depth_pending_valid <= 1'b0;
    end else begin
        depth_pending_valid <= depth_q_push;
        depth_pending_index <= fbwq_wr_ptr;
        depth_pending_tag <= fbwq_swap_now
                           ? depth_hazard_tag(fbwq_req_addr)
                           : depth_hazard_tag(fbwq_stage_addr);
        if (depth_pending_valid) begin
            depth_q_valid[depth_pending_index] <= 1'b1;
            depth_q_tag[depth_pending_index] <= depth_pending_tag;
        end
        // Clear wins if a just-committed pending entry is popped this edge.
        if (fbwq_pop_now) depth_q_valid[fbwq_rd_ptr] <= 1'b0;
    end
end
reg depth_queue_conflict;
integer depth_q_i;
always @* begin
    depth_queue_conflict = depth_pending_valid && (depth_pending_tag == depth_read_tag);
    for (depth_q_i = 0; depth_q_i < FBWQ_DEPTH; depth_q_i = depth_q_i + 1)
        if (depth_q_valid[depth_q_i] && depth_q_tag[depth_q_i] == depth_read_tag)
            depth_queue_conflict = 1'b1;
end
// Keep local holding registers conservative; every write kind (color,
// depth, clear, partial mask) is tagged identically. A 64-byte tag covers
// any supported aligned depth-window burst (up to sixteen words). XOR
// collisions only add waits; an equal address can never miss a conflict.
wire depth_read_safe = !z_flush_valid && !z_src_pending_valid && !wc_busy
                       && !fbwq_req_valid && !fbwq_stage_valid && !depth_queue_conflict;
// DEPTH_HAZARD_QUEUE_END
`else
wire depth_read_safe = fb_read_barrier_clear;
`endif

// Barrier for the z / blend-dst window fills.''')

replace('''        p3_z_test <= 0; p3_z_write <= 0; p3_z_addr <= 0; p3_z_value <= 0;
`ifdef INCLUDE_GPU_SELECTIVE_READ_WAIT''', '''        p3_z_test <= 0; p3_z_write <= 0; p3_z_addr <= 0; p3_z_value <= 0;
`ifdef GPU_DEPTH_SELECTIVE_ACTIVE
        depth_read_tag <= 0;
`endif
`ifdef INCLUDE_GPU_SELECTIVE_READ_WAIT''')

replace('''                p3_fb_addr   <= p2b_fb_addr;
`ifdef INCLUDE_GPU_SELECTIVE_READ_WAIT''', '''                p3_fb_addr   <= p2b_fb_addr;
`ifdef GPU_DEPTH_SELECTIVE_ACTIVE
                depth_read_tag <= depth_hazard_tag(p2b_z_addr);
`endif
`ifdef INCLUDE_GPU_SELECTIVE_READ_WAIT''')

replace('''                        end else if (!zw_fill_busy && !tex_axi_arvalid && !tex_m0_in_flight
                                  && fb_read_barrier_clear) begin
                            // Z-window fill: burst read of the whole aligned
                            // depth line.  The drain barrier is the
                            // conservative fb_write_drain_complete: the whole
                            // fb write queue drains before the z line is read,
                            // so no z-read overlaps any pending write.''', '''                        end else if (!zw_fill_busy && !tex_axi_arvalid && !tex_m0_in_flight
                                  && depth_read_safe) begin
                            // Z-window fill: depth_read_safe defaults to the
                            // original barrier. The cache-specific experiment
                            // permits unrelated queued writes while preserving
                            // all prior writes to this read's 64-byte line.''')

replace('endmodule', '''endmodule
`ifdef GPU_DEPTH_SELECTIVE_ACTIVE
`undef GPU_DEPTH_SELECTIVE_ACTIVE
`endif''')

target = HERE / 'gpu_core.v'
assert not target.exists()
target.write_text(text)
patch = HERE / 'depth-selective.patch'
patch.write_text(''.join(difflib.unified_diff(before.splitlines(keepends=True),
    text.splitlines(keepends=True), fromfile='a/src/fpga/common/gpu_core.v',
    tofile='b/src/fpga/common/gpu_core.v')))
notes = dict(status='RTL_READY_FOR_LINT_AND_DIRECTED_TESTS',
    baseline_model=str(BASE), baseline_prepared_sha256=sha(BASE / 'prepared.json'),
    baseline_source_sha256=sha(BASE / REL), candidate_source=str(target),
    candidate_source_sha256=sha(target), patch_sha256=sha(patch),
    recipe_sha256=sha(Path(__file__)), defines=['INCLUDE_GPU_DEPTH_SELECTIVE_WAIT'],
    required_existing_define='INCLUDE_GPU_RENDER_CACHE',
    changed_read_path='Only external depth-window launch uses depth_read_safe.',
    untouched=['Blend and color-window read barriers', 'Fence/flip/clear full drain',
               'Write enqueue/drain/control', 'All cache RTL and ports'],
    queue_tag_state_bits=129,
    state_breakdown={'fifo_tags':96, 'fifo_valid':16, 'pending_valid_index_tag':11, 'read_tag':6},
    resource_claim='Static register count only; no inferred-area, RAM, fit or timing claim.',
    ordering_contract=[
      'Unpopped FIFO entries are covered by tags plus pending entry, including push/pop overlap.',
      'All local req/stage/zflush/pending/combiner state retains a global block.',
      'Popped writes own AW/W or have been accepted by the serialized render cache; cache AW has priority over AR.',
      'The render cache completes upstream writes into cache data before accepting AR, or tracks posted SDRAM bypass/writeback hazards until downstream B.',
      'Soft reset clears FIFO and matching tags together; ring reset does not clear either. No new B lifetime is tracked.',
      'All address aliases use the same hash without a color/depth type distinction; partial masks conservatively conflict.'
    ])
(HERE / 'rtl-notes.json').write_text(json.dumps(notes, indent=2) + '\n')
print(json.dumps(notes, indent=2))
