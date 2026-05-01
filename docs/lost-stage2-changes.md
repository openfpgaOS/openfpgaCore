# Lost Stage 2 changes — reconstruction guide

Lost on 2026-04-30 by an accidental `git checkout HEAD -- src/fpga/common/gpu_core.v src/fpga/test/tb_gpu.v`.
Stage 1 (slave `word_wr_done` pulse) survived because it lives in different files.

This document captures what was implemented and how to put it back. Each
stage is independent — they were applied incrementally and tested at each
step.

## Status when changes were lost

| Stage | Hardware result | Tests | Quartus slack |
|-------|-----------------|-------|---------------|
| Stage 1 (slave pulse) | – (built upon by 2a/2b) | 471 sdram pass | -0.942 ns |
| Stage 1 + 2a | **2x fps** (3.9 → 6.7) | 965 pass | -0.942 / TNS -31 |
| Stage 1 + 2a + 2b | **3x fps** (6.7 → 12) on early frames, then perceived freeze | 965 pass | -0.780 / TNS -49 |
| + DMA-drain-gate | Blank screen at game start (regressed) | 965 pass | -1.019 / TNS -284 |

User feedback: "Stage 2b is not a problem". So 2b correctness is OK in sim;
the blank-screen bisect points to either Stage 2a's multi-outstanding writes
amplifying a hardware-marginal slave bvalid path, or the DMA-drain-gate's
side-effect.

## Stage 2a — FBSS skip B-wait + multi-outstanding writes

**Goal:** stop the GPU pipeline from stalling for the full SDRAM round-trip
(~12-25 cycles) on every fb_acc cross-word flush. Instead, exit FBSS on
AW+W handshake (~2-3 cycles), let the B response drain asynchronously into
`m_wr_inflight`. CMD_FENCE/CMD_FLIP already gate on `m_wr_inflight==0` so
the drain happens at frame end.

**Files changed:** `src/fpga/common/gpu_core.v`

### Edit 1: cap `fp_pipe_stall` so `m_wr_inflight` (4-bit) can't overflow

Around the existing `fp_pipe_stall` definition (search for
`wire fp_pipe_stall =`):

```verilog
wire cmap_pipe_wait = p2b_valid && p2b_flags[SPAN_COLORMAP] && !cmap_resp_valid_b;
// Stage 2a: m_wr_inflight is 4 bits (max 15).  With B-wait removed in
// FBSS_FLUSH_W_RSP, we can pipe many flushes back-to-back; cap the
// pipeline at 14 outstanding writes so the counter cannot overflow if
// SDRAM is slow to drain Bs.  fp_pipe_stall blocks new fragments from
// reaching p3, which is where flushes are minted.
wire m_wr_inflight_near_full = (m_wr_inflight >= 4'd14);
wire fp_pipe_stall = (p1_valid && !tex_resp_valid)
                  || cmap_pipe_wait
                  || (fbss != FBSS_IDLE)
                  || m_wr_inflight_near_full;
```

### Edit 2: `FBSS_FLUSH_W_RSP` exits on AW+W handshake (not B)

Replace the `if (m_wr_bvalid)` branch with `if (!m_wr_awvalid && !m_wr_wvalid)`.
Body (re-arming `fb_acc` with the pending pixel) is unchanged; just the
gate condition changes:

```verilog
FBSS_FLUSH_W_RSP: begin
    // Drive AW/W until accepted
    if (m_wr_awvalid && m_wr_awready) m_wr_awvalid <= 0;
    if (m_wr_wvalid  && m_wr_wready ) m_wr_wvalid  <= 0;

    // Stage 2a — exit on AW+W handshake, NOT on B response.
    // The original code waited for m_wr_bvalid here, paying the full
    // SDRAM round-trip (~12-15 cycles row-hit, ~25+ on row miss) of
    // pipe stall per pixel-word flush.  Duke3D issues ~50k flushes/
    // frame, so the B-wait was dominating frame time.  Now we re-arm
    // fb_acc with the pending pixel as soon as both AW and W are
    // handshaked (typically 2-3 cycles via axi_register_slice).  The
    // outstanding B is tracked by m_wr_inflight; CMD_FENCE/CMD_FLIP
    // drain it before retiring.  Multi-master single-ID (m_wr_awid is
    // implicitly 0) AXI ordering preserves write semantics across
    // overlapping bursts — critical for overdraw correctness.
    if (!m_wr_awvalid && !m_wr_wvalid) begin
        if (fbss_pend_valid) begin : pend_apply
            // ... existing re-arm body unchanged ...
        end
        fbss <= FBSS_IDLE;
    end
end
```

### Edit 3: `S_FB_FLUSH_WAIT` same change

Same gate swap (`if (m_wr_bvalid)` → `if (!m_wr_awvalid && !m_wr_wvalid)`)
in the end-of-primitive flush wait state. The bodies for `tri_active` /
`sp_count > 0` / else branches stay the same.

## Stage 2b — multi-beat AW bursts (4-deep fb_acc)

**Goal:** amortise the slave's per-AW handshake cost. Coalesce up to 4
consecutive same-row word writes into one AW burst. Frag pipe still
stalls per-burst, but bursts are 4x rarer, so cycles in fbss != IDLE
drop ~4x.

**Files changed:** `src/fpga/common/gpu_core.v`

### Edit 1: replace single-word fb_acc with 4-deep buffer

Find the existing `fb_acc_*` reg block (search `// FB write accumulator`):

```verilog
// FB write accumulator — Stage 2b: 4-deep buffer for multi-beat AW bursts.
// Each slot holds one word of FB pixel data + per-byte mask.  Slots are
// stored CONSECUTIVELY in address space starting from fb_acc_base_addr,
// i.e. slot i covers byte address fb_acc_base_addr + i*4.  When the
// buffer flushes, we emit one AW with awlen=count-1 and stream count
// W beats from slot 0 → slot count-1; the slave's S_WR_BURST path
// already handles the streaming.  Coalescing across span/triangle
// pixels reduces flush count by ~4x in the typical scan order.
reg [31:0] fb_acc_data [0:3];
reg [3:0]  fb_acc_mask [0:3];   // Per-slot byte enables
reg [31:0] fb_acc_base_addr;     // Word-aligned address of slot 0
reg [2:0]  fb_acc_count;         // 0..4 — number of valid slots
// `fb_acc_valid` legacy alias kept for BLEND read-bypass logic so the
// existing pattern (`fb_acc_valid && fb_acc_addr == word`) continues
// to work for the slot-0 fast path.  BLEND forces a flush before entry
// (fbss=BLEND_REQ), so by the time BLEND_R_WAIT runs, only slot 0
// matters for bypass.
wire        fb_acc_valid = (fb_acc_count != 3'd0);
wire [31:0] fb_acc_addr  = fb_acc_base_addr;  // historical name; head slot
wire [31:0] fb_acc_head_addr = fb_acc_base_addr + ({29'b0, fb_acc_count - 3'd1} << 2);
wire [31:0] fb_acc_next_addr = fb_acc_base_addr + ({29'b0, fb_acc_count} << 2);

// Flush streaming index — driven during FBSS_FLUSH_W_RSP / S_FB_FLUSH_WAIT
// to advance through fb_acc_data[0..flush_total-1] on each W handshake.
reg [2:0]  flush_idx;            // 0..3 — current beat index being driven
reg [2:0]  flush_total;          // 1..4 — number of beats in the burst
```

### Edit 2: reset blocks (main reset + soft_reset)

Replace `fb_acc_valid <= 0; fb_acc_mask <= 0;` with:

```verilog
fb_acc_count <= 0;
fb_acc_mask[0] <= 0;
fb_acc_mask[1] <= 0;
fb_acc_mask[2] <= 0;
fb_acc_mask[3] <= 0;
flush_idx   <= 0;
flush_total <= 0;
```

Both in main reset block (~line 1909) and soft_reset block (~line 2032).

### Edit 3: add `fbss_pend_blend` flag

Near `fbss_pend_valid` declaration (~line 1348):

```verilog
// Stage 2b: when BLEND fires while fb_acc has data, we flush the
// buffer first and then enter BLEND_REQ (rather than IDLE).  This
// flag steers FBSS_FLUSH_W_RSP's exit.  Mutually exclusive with
// fbss_pend_valid.
reg        fbss_pend_blend;
```

Reset to 0 in the main reset block (`fbss_pend_blend <= 0;`).

### Edit 4: rewrite p3 fast path in FBSS_IDLE

Replace the existing `else if (p3_valid && !p3_discard) begin : fb_acc_blk`
body:

```verilog
else if (p3_valid && !p3_discard) begin : fb_acc_blk
    reg        match_head;
    reg        match_next;
    p3_word_addr = p3_fb_addr & 32'hFFFFFFFC;
    p3_byte_lane = p3_fb_addr[1:0];
    p3_word_match = 1'b0;  // unused; kept assigned to satisfy outer block
    match_head = fb_acc_valid && (fb_acc_head_addr == p3_word_addr);
    match_next = fb_acc_valid && (fb_acc_count < 3'd4)
                              && (fb_acc_next_addr == p3_word_addr);

    if (!fb_acc_valid) begin
        // Empty buffer: occupy slot 0
        fb_acc_data[0] <= 32'b0;
        fb_acc_mask[0] <= 4'b0;
        case (p3_byte_lane)
            2'd0: begin fb_acc_data[0][7:0]   <= p3_color; fb_acc_mask[0][0] <= 1; end
            2'd1: begin fb_acc_data[0][15:8]  <= p3_color; fb_acc_mask[0][1] <= 1; end
            2'd2: begin fb_acc_data[0][23:16] <= p3_color; fb_acc_mask[0][2] <= 1; end
            2'd3: begin fb_acc_data[0][31:24] <= p3_color; fb_acc_mask[0][3] <= 1; end
        endcase
        fb_acc_base_addr <= p3_word_addr;
        fb_acc_count     <= 3'd1;
    end else if (match_head) begin
        // Same word as HEAD — merge byte into existing slot.
        // Index by (count-1).  Case fans 4×4 = 16 combinations.
        case ({fb_acc_count - 3'd1, p3_byte_lane})
            {3'd0,2'd0}: begin fb_acc_data[0][7:0]   <= p3_color; fb_acc_mask[0][0] <= 1; end
            {3'd0,2'd1}: begin fb_acc_data[0][15:8]  <= p3_color; fb_acc_mask[0][1] <= 1; end
            {3'd0,2'd2}: begin fb_acc_data[0][23:16] <= p3_color; fb_acc_mask[0][2] <= 1; end
            {3'd0,2'd3}: begin fb_acc_data[0][31:24] <= p3_color; fb_acc_mask[0][3] <= 1; end
            {3'd1,2'd0}: begin fb_acc_data[1][7:0]   <= p3_color; fb_acc_mask[1][0] <= 1; end
            {3'd1,2'd1}: begin fb_acc_data[1][15:8]  <= p3_color; fb_acc_mask[1][1] <= 1; end
            {3'd1,2'd2}: begin fb_acc_data[1][23:16] <= p3_color; fb_acc_mask[1][2] <= 1; end
            {3'd1,2'd3}: begin fb_acc_data[1][31:24] <= p3_color; fb_acc_mask[1][3] <= 1; end
            {3'd2,2'd0}: begin fb_acc_data[2][7:0]   <= p3_color; fb_acc_mask[2][0] <= 1; end
            {3'd2,2'd1}: begin fb_acc_data[2][15:8]  <= p3_color; fb_acc_mask[2][1] <= 1; end
            {3'd2,2'd2}: begin fb_acc_data[2][23:16] <= p3_color; fb_acc_mask[2][2] <= 1; end
            {3'd2,2'd3}: begin fb_acc_data[2][31:24] <= p3_color; fb_acc_mask[2][3] <= 1; end
            {3'd3,2'd0}: begin fb_acc_data[3][7:0]   <= p3_color; fb_acc_mask[3][0] <= 1; end
            {3'd3,2'd1}: begin fb_acc_data[3][15:8]  <= p3_color; fb_acc_mask[3][1] <= 1; end
            {3'd3,2'd2}: begin fb_acc_data[3][23:16] <= p3_color; fb_acc_mask[3][2] <= 1; end
            {3'd3,2'd3}: begin fb_acc_data[3][31:24] <= p3_color; fb_acc_mask[3][3] <= 1; end
            default: ;
        endcase
    end else if (match_next) begin
        // Adjacent next word, room available — start new slot.
        // Initialise slot[count] to zero THEN set the byte; non-blocking
        // last-wins gives byte_lane=p3_color, others=0.
        case ({fb_acc_count, p3_byte_lane})
            {3'd1,2'd0}: begin fb_acc_data[1] <= 32'b0; fb_acc_mask[1] <= 4'b0; fb_acc_data[1][7:0]   <= p3_color; fb_acc_mask[1][0] <= 1; end
            {3'd1,2'd1}: begin fb_acc_data[1] <= 32'b0; fb_acc_mask[1] <= 4'b0; fb_acc_data[1][15:8]  <= p3_color; fb_acc_mask[1][1] <= 1; end
            {3'd1,2'd2}: begin fb_acc_data[1] <= 32'b0; fb_acc_mask[1] <= 4'b0; fb_acc_data[1][23:16] <= p3_color; fb_acc_mask[1][2] <= 1; end
            {3'd1,2'd3}: begin fb_acc_data[1] <= 32'b0; fb_acc_mask[1] <= 4'b0; fb_acc_data[1][31:24] <= p3_color; fb_acc_mask[1][3] <= 1; end
            {3'd2,2'd0}: begin fb_acc_data[2] <= 32'b0; fb_acc_mask[2] <= 4'b0; fb_acc_data[2][7:0]   <= p3_color; fb_acc_mask[2][0] <= 1; end
            {3'd2,2'd1}: begin fb_acc_data[2] <= 32'b0; fb_acc_mask[2] <= 4'b0; fb_acc_data[2][15:8]  <= p3_color; fb_acc_mask[2][1] <= 1; end
            {3'd2,2'd2}: begin fb_acc_data[2] <= 32'b0; fb_acc_mask[2] <= 4'b0; fb_acc_data[2][23:16] <= p3_color; fb_acc_mask[2][2] <= 1; end
            {3'd2,2'd3}: begin fb_acc_data[2] <= 32'b0; fb_acc_mask[2] <= 4'b0; fb_acc_data[2][31:24] <= p3_color; fb_acc_mask[2][3] <= 1; end
            {3'd3,2'd0}: begin fb_acc_data[3] <= 32'b0; fb_acc_mask[3] <= 4'b0; fb_acc_data[3][7:0]   <= p3_color; fb_acc_mask[3][0] <= 1; end
            {3'd3,2'd1}: begin fb_acc_data[3] <= 32'b0; fb_acc_mask[3] <= 4'b0; fb_acc_data[3][15:8]  <= p3_color; fb_acc_mask[3][1] <= 1; end
            {3'd3,2'd2}: begin fb_acc_data[3] <= 32'b0; fb_acc_mask[3] <= 4'b0; fb_acc_data[3][23:16] <= p3_color; fb_acc_mask[3][2] <= 1; end
            {3'd3,2'd3}: begin fb_acc_data[3] <= 32'b0; fb_acc_mask[3] <= 4'b0; fb_acc_data[3][31:24] <= p3_color; fb_acc_mask[3][3] <= 1; end
            default: ;
        endcase
        fb_acc_count <= fb_acc_count + 3'd1;
    end else begin
        // Non-adjacent OR full buffer: flush ALL slots as one burst,
        // queue p3 for re-application post-flush.
        m_wr_awvalid <= 1;
        m_wr_awaddr  <= fb_acc_base_addr;
        m_wr_awlen   <= {5'b0, fb_acc_count - 3'd1};
        m_wr_wvalid  <= 1;
        m_wr_wdata   <= fb_acc_data[0];
        m_wr_wstrb   <= fb_acc_mask[0];
        m_wr_wlast   <= (fb_acc_count == 3'd1);
        flush_idx    <= 3'd0;
        flush_total  <= fb_acc_count;

        // Queue p3's pixel for re-application post-flush
        fbss_pend_valid <= 1;
        fbss_pend_color <= p3_color;
        fbss_pend_addr  <= p3_fb_addr;

        // CRITICAL: do NOT clear fb_acc_mask[1..3] here — the W beat
        // streamer in FBSS_FLUSH_W_RSP reads them on subsequent
        // cycles, and the accumulation paths re-initialise each
        // slot's mask before occupying it.  Bug fix from Stage 2b
        // dev: clearing on flush trigger caused 31/404 GPU tests to
        // fail (pixels lost mid-burst).
        fb_acc_count <= 0;

        fbss <= FBSS_FLUSH_W_RSP;
    end

    if (fp_pipe_stall) p3_valid <= 0;
end
```

### Edit 5: BLEND entry forces flush if buffer non-empty

Replace existing BLEND entry block (search `if (p3_valid && !p3_discard && p3_flags[SPAN_TRANSLUC])`):

```verilog
if (p3_valid && !p3_discard && p3_flags[SPAN_TRANSLUC]) begin
    blend_src_color <= p3_color;
    blend_word_addr <= p3_fb_addr & 32'hFFFFFFFC;
    blend_byte_lane <= p3_fb_addr[1:0];
    blend_p3_flags  <= p3_flags;
    // Stage 2b: BLEND requires a clean fb_acc (no multi-slot bypass
    // implemented).  If the burst buffer has any data, flush it first;
    // pend_blend tells FBSS_FLUSH_W_RSP's exit to go to BLEND_REQ
    // instead of IDLE.  BLEND_REQ also waits for m_wr_inflight==0 so
    // the read sees committed data.
    if (fb_acc_count > 3'd0) begin
        m_wr_awvalid <= 1;
        m_wr_awaddr  <= fb_acc_base_addr;
        m_wr_awlen   <= {5'b0, fb_acc_count - 3'd1};
        m_wr_wvalid  <= 1;
        m_wr_wdata   <= fb_acc_data[0];
        m_wr_wstrb   <= fb_acc_mask[0];
        m_wr_wlast   <= (fb_acc_count == 3'd1);
        flush_idx    <= 3'd0;
        flush_total  <= fb_acc_count;
        fb_acc_count <= 0;
        fbss_pend_blend <= 1;
        fbss <= FBSS_FLUSH_W_RSP;
    end else begin
        fbss <= FBSS_BLEND_REQ;
    end
    if (fp_pipe_stall) p3_valid <= 0;
end
```

### Edit 6: rewrite FBSS_FLUSH_W_RSP for multi-beat W stream

Replace the body (Stage 2a's exit logic gets multi-beat treatment):

```verilog
FBSS_FLUSH_W_RSP: begin
    // Drive AW until accepted (single AW per burst)
    if (m_wr_awvalid && m_wr_awready) m_wr_awvalid <= 0;

    // Stream W beats: on each handshake, advance to next slot.
    // flush_idx tracks the beat CURRENTLY on the bus (0..flush_total-1).
    // Last beat: drop wvalid.
    if (m_wr_wvalid && m_wr_wready) begin
        if (flush_idx + 3'd1 < flush_total) begin
            // More beats — drive slot[flush_idx+1]
            case (flush_idx + 3'd1)
                3'd1: begin m_wr_wdata <= fb_acc_data[1]; m_wr_wstrb <= fb_acc_mask[1]; end
                3'd2: begin m_wr_wdata <= fb_acc_data[2]; m_wr_wstrb <= fb_acc_mask[2]; end
                3'd3: begin m_wr_wdata <= fb_acc_data[3]; m_wr_wstrb <= fb_acc_mask[3]; end
                default: ;
            endcase
            m_wr_wlast <= (flush_idx + 3'd2 == flush_total);
            flush_idx  <= flush_idx + 3'd1;
        end else begin
            // Last beat handshaked — burst done from W side.
            m_wr_wvalid <= 0;
            m_wr_wlast  <= 0;
        end
    end

    // Stage 2 — exit on AW + (last W) handshake, NOT on B response.
    if (!m_wr_awvalid && !m_wr_wvalid) begin
        if (fbss_pend_valid) begin : pend_apply
            reg [31:0] pw_addr;
            reg [1:0]  pw_lane;
            pw_addr = fbss_pend_addr & 32'hFFFFFFFC;
            pw_lane = fbss_pend_addr[1:0];

            // Re-arm slot 0 with the pending pixel.  Burst buffer is
            // empty after flush, restart with count=1.
            fb_acc_data[0] <= 32'b0;
            fb_acc_mask[0] <= 4'b0;
            case (pw_lane)
                2'd0: begin fb_acc_data[0][7:0]   <= fbss_pend_color; fb_acc_mask[0][0] <= 1; end
                2'd1: begin fb_acc_data[0][15:8]  <= fbss_pend_color; fb_acc_mask[0][1] <= 1; end
                2'd2: begin fb_acc_data[0][23:16] <= fbss_pend_color; fb_acc_mask[0][2] <= 1; end
                2'd3: begin fb_acc_data[0][31:24] <= fbss_pend_color; fb_acc_mask[0][3] <= 1; end
            endcase
            fb_acc_base_addr <= pw_addr;
            fb_acc_count     <= 3'd1;

            fbss_pend_valid <= 0;
        end

        if (fbss_pend_blend) begin
            // Pre-blend buffer flush done — proceed to BLEND.
            fbss_pend_blend <= 0;
            fbss <= FBSS_BLEND_REQ;
        end else begin
            fbss <= FBSS_IDLE;
        end
    end
end
```

### Edit 7: BLEND_REQ also waits for m_wr_inflight==0

```verilog
FBSS_BLEND_REQ: begin
    // Wait for the texture cache to be fully drained from M0
    // (no pending AR, no in-flight read) AND for outstanding GPU
    // writes to commit before issuing the BLEND read.  The drain
    // wait is required because Stage 2a allows multi-outstanding
    // writes — without it, BLEND would read SDRAM before the
    // just-flushed write committed, returning stale data on
    // overdraw within the same word.
    if (!tex_axi_arvalid && !tex_m0_in_flight
        && m_wr_inflight == 4'd0) begin
        blend_arvalid <= 1;
        blend_araddr  <= blend_word_addr;
        fbss          <= FBSS_BLEND_AR_WAIT;
    end
end
```

### Edit 8: simplify BLEND_R_WAIT (no bypass needed)

Since BLEND now always enters with empty `fb_acc`, the bypass logic can
be removed. Replace with straight-from-rdata:

```verilog
FBSS_BLEND_R_WAIT: begin
    if (blend_rvalid) begin : blend_r_capture
        // Stage 2b: BLEND entry forces a flush + drain wait, so
        // fb_acc is guaranteed empty here.  No bypass needed —
        // read straight from SDRAM rdata.
        reg [7:0] rdata_lane;
        reg [7:0] fb_byte;
        case (blend_byte_lane)
            2'd0: rdata_lane = blend_rdata[7:0];
            2'd1: rdata_lane = blend_rdata[15:8];
            2'd2: rdata_lane = blend_rdata[23:16];
            2'd3: rdata_lane = blend_rdata[31:24];
        endcase
        fb_byte = rdata_lane;
        blend_fb_word <= blend_rdata;
        transluc_rd_addr <= { blend_src_color[7:1], fb_byte };
        fbss <= FBSS_BLEND_LUT_WAIT;
    end
end
```

### Edit 9: rewrite BLEND_APPLY to write slot 0 (count=1)

```verilog
FBSS_BLEND_APPLY: begin : fbss_blend_apply_blk
    // Stage 2b: BLEND entry forced a flush+drain; fb_acc is empty
    // here.  Write the blended byte to slot 0 and exit to IDLE —
    // count=1 is a normal post-blend state, the next p3 will either
    // merge into slot 0 (same word) or extend / flush as usual.
    fb_acc_data[0] <= 32'b0;
    fb_acc_mask[0] <= 4'b0;
    case (blend_byte_lane)
        2'd0: begin fb_acc_data[0][7:0]   <= transluc_rd_data; fb_acc_mask[0][0] <= 1; end
        2'd1: begin fb_acc_data[0][15:8]  <= transluc_rd_data; fb_acc_mask[0][1] <= 1; end
        2'd2: begin fb_acc_data[0][23:16] <= transluc_rd_data; fb_acc_mask[0][2] <= 1; end
        2'd3: begin fb_acc_data[0][31:24] <= transluc_rd_data; fb_acc_mask[0][3] <= 1; end
    endcase
    fb_acc_base_addr <= blend_word_addr;
    fb_acc_count     <= 3'd1;
    fbss <= FBSS_IDLE;
end
```

### Edit 10: rewrite S_FB_FLUSH for multi-beat emit

```verilog
S_FB_FLUSH: begin
    if (fb_acc_valid) begin
        // Stage 2b: emit awlen=count-1 burst, stream slot 0 first;
        // FBSS_FLUSH_W_RSP-style W-beat advance happens in S_FB_FLUSH_WAIT.
        m_wr_awvalid <= 1;
        m_wr_awaddr  <= fb_acc_base_addr;
        m_wr_awlen   <= {5'b0, fb_acc_count - 3'd1};
        m_wr_wvalid  <= 1;
        m_wr_wdata   <= fb_acc_data[0];
        m_wr_wstrb   <= fb_acc_mask[0];
        m_wr_wlast   <= (fb_acc_count == 3'd1);
        flush_idx    <= 3'd0;
        flush_total  <= fb_acc_count;
        state        <= S_FB_FLUSH_WAIT;
    end else begin
        fb_acc_count <= 0;
        fb_acc_mask[0] <= 0;
        fb_acc_mask[1] <= 0;
        fb_acc_mask[2] <= 0;
        fb_acc_mask[3] <= 0;
        if (tri_active) tri_active <= 0;
        if (cmd_is_draw_spans_batch && pay_remaining > 24'd0) begin
            ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
            state      <= S_PAY_DATA;
        end else
            state <= S_IDLE;
    end
end
```

### Edit 11: rewrite S_FB_FLUSH_WAIT for multi-beat W stream + slot-0 re-arm

```verilog
S_FB_FLUSH_WAIT: begin
    if (m_wr_awvalid && m_wr_awready)
        m_wr_awvalid <= 0;
    // Stream W beats — same advance as FBSS_FLUSH_W_RSP.
    if (m_wr_wvalid && m_wr_wready) begin
        if (flush_idx + 3'd1 < flush_total) begin
            case (flush_idx + 3'd1)
                3'd1: begin m_wr_wdata <= fb_acc_data[1]; m_wr_wstrb <= fb_acc_mask[1]; end
                3'd2: begin m_wr_wdata <= fb_acc_data[2]; m_wr_wstrb <= fb_acc_mask[2]; end
                3'd3: begin m_wr_wdata <= fb_acc_data[3]; m_wr_wstrb <= fb_acc_mask[3]; end
                default: ;
            endcase
            m_wr_wlast <= (flush_idx + 3'd2 == flush_total);
            flush_idx  <= flush_idx + 3'd1;
        end else begin
            m_wr_wvalid <= 0;
            m_wr_wlast  <= 0;
        end
    end
    if (!m_wr_awvalid && !m_wr_wvalid) begin
        if (tri_active) begin
            // Re-accumulate pending pixel into slot 0.  Clear masks
            // 0..3 so previous burst's masks don't bleed into the
            // new burst.
            begin : reaccum_tri
                reg [1:0] bl;
                bl = sp_fb_addr[1:0];
                fb_acc_data[0] <= 32'b0;
                fb_acc_mask[0] <= 4'b0;
                fb_acc_mask[1] <= 4'b0;
                fb_acc_mask[2] <= 4'b0;
                fb_acc_mask[3] <= 4'b0;
                case (bl)
                    2'd0: begin fb_acc_data[0][7:0]   <= frag_color[7:0]; fb_acc_mask[0][0] <= 1; end
                    2'd1: begin fb_acc_data[0][15:8]  <= frag_color[7:0]; fb_acc_mask[0][1] <= 1; end
                    2'd2: begin fb_acc_data[0][23:16] <= frag_color[7:0]; fb_acc_mask[0][2] <= 1; end
                    2'd3: begin fb_acc_data[0][31:24] <= frag_color[7:0]; fb_acc_mask[0][3] <= 1; end
                endcase
                fb_acc_base_addr <= sp_fb_addr & 32'hFFFFFFFC;
                fb_acc_count     <= 3'd1;
            end
            state <= S_TRI_PIX;
        end else if (sp_count > 0) begin
            // Same as tri_active branch but for span continuation.
            // (Body identical except final state <= S_SPAN_STEP.)
            // ... see tri_active body ...
            state <= S_SPAN_STEP;
        end else begin
            fb_acc_count <= 0;
            fb_acc_mask[0] <= 0;
            fb_acc_mask[1] <= 0;
            fb_acc_mask[2] <= 0;
            fb_acc_mask[3] <= 0;
            if (cmd_is_draw_spans_batch && pay_remaining > 24'd0) begin
                ring_rdptr <= (ring_rdptr + 16'd4) & ring_mask;
                state      <= S_PAY_DATA;
            end else
                state <= S_IDLE;
        end
    end
end
```

## DMA-drain-gate

**Goal:** restore the implicit serialisation that pre-CMD_FLIP CPU-blocking
provided. Without it, the CPU is free to keep emitting commands /
triggering DMA while GPU is still draining `m_wr_inflight` for CMD_FLIP.
This *should* be safe (separate AXI channels), but the user's hypothesis
is that the concurrency exposes a latent race.

**Files changed:** `src/fpga/common/gpu_core.v`

Find the existing `dma_bus_idle` definition (search `wire dma_bus_idle`):

```verilog
// Don't kick DMA into S_AR while a CMD_FLIP or CMD_FENCE drain is in
// progress.  Pre-CMD_FLIP era, the CPU spun on FB_SWAP_CTRL waiting
// for vsync — that spin happened to serialise CPU activity behind
// the drain.  With GPU-triggered CMD_FLIP, the SDK kernel call is
// non-blocking, so the CPU starts the next frame immediately and
// may kick a fresh DMA while m_wr_inflight is still draining.  The
// DMA's AR claim on M0 plus its R-burst-induced SDRAM bus contention
// can delay m_wr's pending B responses and exposes a latent path
// where drain stalls indefinitely.  Gating dma_bus_idle on the drain
// phase reproduces the old CPU-block invariant in RTL.
wire dma_drain_block = (state == S_EXECUTE) && (cmd_is_flip || cmd_is_fence);
wire dma_bus_idle = !blend_owns_m0 && !tex_m0_in_flight && !dma_drain_block;
```

**Caveat:** `dma_starve_count` (1024-cycle threshold) overrides this gate.
So if a drain takes >1024 cycles (~10 µs at 100 MHz), DMA fires anyway.
The gate only delays DMA during normal-length drains. If drain is *stuck*
(m_wr_inflight never reaches 0), the gate doesn't help.

This addition was the LAST change before the blank-screen regression. May
or may not be implicated.

## tb_gpu.v debug pin additions

`tb_gpu.v` referenced `slave_swap_pending`, `arb_state_dbg`, `cpu_pending_dbg`,
`dbg_bus` on the gpu_core instantiation. These were ports added during the
original CMD_FLIP RTL work (commit 1b3d7a9). When `gpu_core.v` was reverted
to HEAD, those ports came back automatically — but `tb_gpu.v` was modified
post-1b3d7a9 to wire additional debug pins. Those wiring changes are what
got lost.

To re-add: instantiate the gpu_core with current ports, and add any new
debug pins as needed when reconstructing.

## Verification checklist when reconstructing

- [ ] All 471 tb_sdram tests pass (Stage 1 regression test included)
- [ ] All 404 tb_gpu tests pass (especially `tri_px_*`, `tri_b2b_*`,
      `batch2_tri*`, `batchdj_tri*` — these caught the mask-clearing bug
      in Stage 2b development)
- [ ] tb_gpu_gpudemo: 32/32 frames pass
- [ ] Quartus closes timing similar to the references in the table at top
- [ ] Verify `m_wr_inflight` doesn't get stuck >0 in hardware via
      `GPU_DBG_MWR_REG` (`0x4a000034`) reads

## Open questions when resuming

1. Does Stage 1's slave `word_wr_done` pulse miss in hardware due to
   timing margin? Current `axi_sdram_slave.v` exit is pulse-only; an
   earlier "belt-and-suspenders" attempt added `(wr_busy_seen && !sdram_busy)`
   as fallback but blew TNS to -187. A timer-based timeout is an
   alternative we didn't try.
2. Does the DMA-drain-gate actually help, or is it a red herring? The
   user's intuition was solid but the deploy regressed.
3. Is there a `tex_cache pipe_valid_a` desync triggered by the increased
   M0 traffic from Stage 2a's multi-outstanding writes? (Trace showed
   `p1_valid=1, fp_pipe_stall=1, tex_dbg_state=0` — pipeline stuck waiting
   for tex response while cache is idle.)
