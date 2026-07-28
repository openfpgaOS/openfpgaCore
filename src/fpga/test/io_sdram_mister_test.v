//
// io_sdram
//
// 2019-2022 Analogue
//

module io_sdram #(
    // Open-page row-tracking layout.
    //   1 (default) — per-bank tracking: four independently open rows
    //       (open_row[0:3] / row_open_v[3:0]), so cross-bank alternation
    //       between the CPU / GPU / scanout / audio regions hits an
    //       already-open row instead of paying PRECHG+tRP+ACT+tRCD on
    //       every switch.
    //   0 — single tracked {open_bank, open_row}: a hit requires the SAME
    //       bank AND the same row, and a miss precharges the tracked open
    //       bank (not the request's) before activating — the original
    //       single-bank behaviour.  Every track access degenerates to
    //       entry 0, so entries 1-3 and the bank-indexed muxes are swept;
    //       area-constrained variants trade row-hit rate for ALMs.
    // Both configs keep the registered row-hit decision (req_* registered
    // at ST_IDLE dispatch, consumed in ST_REQ_*) and the refresh
    // precharge-ALL (A10=1) path.
    parameter BANK_ROW_TRACK = 1
) (

input   wire            controller_clk,
input   wire            chip_clk,
input   wire            clk_90,
input   wire            reset_n,

output  reg             phy_cke,
output  wire            phy_clk,
output  wire            phy_cas,
output  wire            phy_ras,
output  wire            phy_we,
output  reg     [1:0]   phy_ba,
output  reg     [12:0]  phy_a,
input   wire    [15:0]  phy_dq_in,
output  wire    [15:0]  phy_dq_out_port,
output  wire            phy_dq_oe_port,
output  reg     [1:0]   phy_dqm,

input   wire            burst_rd, // must be synchronous to clk_ram
input   wire    [24:0]  burst_addr,
input   wire    [10:0]  burst_len,
input   wire            burst_32bit,
output  reg     [31:0]  burst_data,
output  reg             burst_data_valid,
output  reg             burst_data_done,

input   wire            burstwr,
input   wire    [24:0]  burstwr_addr,
output  reg             burstwr_ready,
input   wire            burstwr_strobe,
input   wire    [15:0]  burstwr_data,
input   wire            burstwr_done,

input   wire            word_rd, // can be from other clock domain. we synchronize these
input   wire            word_wr,
input   wire    [23:0]  word_addr,
input   wire    [31:0]  word_data,
input   wire    [3:0]   word_wstrb, // byte enables: [0]=byte0, [1]=byte1, [2]=byte2, [3]=byte3
input   wire    [31:0]  word_data_next, // pre-captured beat 1 for native 2-word block writes
input   wire    [3:0]   word_wstrb_next,
input   wire    [3:0]   word_burst_len, // 0=single word, N=N+1 words (for CPU cache line fills)
input   wire    [3:0]   word_burst_wr_len, // 0=single word, N=N+1 words (for burst writes)
output  reg     [31:0]  word_q,
output  reg             word_busy,
output  reg             word_q_valid, // Pulses high for one cycle when word_q data is valid
output  reg             word_wr_data_next, // Pulse: need next word data for burst write
output  reg             word_wr_done,      // Pulse: slave-issued word write completed (ST_WRITE_4→IDLE)

input   wire    [31:0]  burst_wr_direct_data, // Direct data bus from AXI slave (bypasses pulse adapter)
input   wire    [3:0]   burst_wr_direct_strb,  // Direct byte enables

// Diagnostic observability exported on controller_clk; core_top
// re-syncs into clk_cpu before splicing into GPU_DBG_BUS bits 31:24.
//   bits 5:0 = state[5:0] (see localparams above; ST_WRITE_0=20,
//              ST_BURSTWR_0=46, etc.)
//   bit  6   = refresh pending (one or more refreshes queued, awaiting ST_IDLE)
//   bit  7   = reserved
output  wire    [7:0]   dbg_io
);

    // tristate for DQ
    reg             phy_dq_oe;
    assign phy_dq_out_port = phy_dq_out;
assign phy_dq_oe_port = phy_dq_oe;
    reg     [15:0]  phy_dq_out;

    reg     [2:0]   cmd;
assign {phy_ras, phy_cas, phy_we} = cmd;

    localparam      CMD_NOP             = 3'b111;
    localparam      CMD_ACT             = 3'b011;
    localparam      CMD_READ            = 3'b101;
    localparam      CMD_WRITE           = 3'b100;
    localparam      CMD_PRECHG          = 3'b010;
    localparam      CMD_AUTOREF         = 3'b001;
    localparam      CMD_LMR             = 3'b000;

    localparam      CAS                 =   4'd3;   // timings are for 100MHz (10ns)
    localparam      TIMING_LMR          =   4'd2;   // tLMR = 2ck
    localparam      TIMING_AUTOREFRESH  =   4'd8;   // tRFC = 80ns @ 100MHz = 8 cycles (80ns)
    localparam      TIMING_PRECHARGE    =   4'd2;   // tRP = 15ns @ 100MHz = 2 cycles (20ns)
    // (tRC / ACT-to-ACT is satisfied implicitly by the FSM's per-row command
    // latency; no explicit ACT-ACT timer is needed.)
    localparam      TIMING_ACT_RW       =   4'd2;   // tRCD = 15ns @ 100MHz = 2 cycles (20ns)
    localparam      TIMING_WRITE        =   4'd2;   // tWR = 2ck

    reg     [5:0]   state;

    localparam      ST_RESET            = 'd0;
    localparam      ST_BOOT_0           = 'd1;
    localparam      ST_BOOT_1           = 'd2;
    localparam      ST_BOOT_2           = 'd3;
    localparam      ST_BOOT_3           = 'd4;
    localparam      ST_BOOT_4           = 'd5;
    localparam      ST_BOOT_5           = 'd6;
    localparam      ST_IDLE             = 'd7;

    // Open-page row-hit optimization states
    localparam      ST_PRECHG_WAIT      = 'd8;   // Wait tRP after precharge (then refresh or ACT)
    localparam      ST_WRITE_HIT        = 'd9;   // DQ setup for row-hit writes
    localparam      ST_REQ_READ         = 'd11;  // Registered row-hit decision -> READ path
    localparam      ST_REQ_WRITE        = 'd12;  // Registered row-hit decision -> WRITE path
    localparam      ST_REQ_BURST_READ   = 'd13;  // Registered row-hit decision -> burst READ path
    localparam      ST_REQ_BURST_WRITE  = 'd14;  // Registered open-row decision -> burst WRITE path

    localparam      ST_WRITE_0          = 'd20;
    localparam      ST_WRITE_1          = 'd21;
    localparam      ST_WRITE_2          = 'd22;
    localparam      ST_WRITE_3          = 'd23;
    localparam      ST_WRITE_4          = 'd24;
    // ('d25/'d26 were ST_WRITE_5/ST_WRITE_6, the 2-dead-cycle continuation
    //  bubble.  Retired: the rolling preload below streams continuation
    //  beats back-to-back on the WRITE_3->WRITE_2 edge.  'd27 was a leftover
    //  ST_WRITE_7 trampoline state, also retired.)
    localparam      ST_WRITE_4_NEWROW   = 'd28;
    localparam      ST_WRITE_4_NR_PRECHG = 'd29;
    localparam      ST_WRITE_4_NR_ACT   = 'd10;

    localparam      ST_READ_0           = 'd30;
    localparam      ST_READ_1           = 'd31;
    localparam      ST_READ_2           = 'd32;
    localparam      ST_READ_3           = 'd33;
    localparam      ST_READ_5           = 'd35;
    localparam      ST_READ_6           = 'd36;
    localparam      ST_READ_7           = 'd37;
    localparam      ST_READ_8           = 'd38;
    localparam      ST_READ_9           = 'd39;

    localparam      ST_BURSTWR_0        = 'd46;
    localparam      ST_BURSTWR_1        = 'd47;
    localparam      ST_BURSTWR_2        = 'd48;
    localparam      ST_BURSTWR_3        = 'd49;
    localparam      ST_BURSTWR_4        = 'd50;
    localparam      ST_BURSTWR_5        = 'd51;
    localparam      ST_BURSTWR_6        = 'd52;
    localparam      ST_BURSTWR_7        = 'd53;

    localparam      ST_REFRESH_0        = 'd60;
    localparam      ST_REFRESH_1        = 'd61;


    reg     [23:0]  delay_boot;
    // SDRAM command timing counter.  The long boot delay has its own counter;
    // this one only needs to cover tRFC=8 cycles, so keep it narrow to avoid
    // wide terminal-count compares on the 100MHz SDRAM command path.
    reg     [3:0]   dc;
    // Refresh every 7.36us at 100MHz (736 cycles).  8192 refreshes / 64ms
    // (8K-row part, A0-A12 row addressing) requires a <=7.8125us average;
    // 736 keeps >5% margin while issuing ~30% fewer refreshes than the old
    // 5.12us interval.  Unlike dc above, this terminal-count compare feeds
    // only the slow pending counter, not the SDRAM command path.
    localparam      REFRESH_INTERVAL = 10'd736;
    reg     [9:0]   refresh_count;
    // Pending-refresh counter (was a single flag).  A counter cannot drop a
    // refresh tick if a previous refresh is still being serviced when the next
    // tick arrives, so it stays correct even if a future longer op or clock
    // change pushes a single operation past the refresh interval.  Refresh has
    // top priority at every ST_IDLE entry, so pending only accumulates across
    // ONE op; the longest (an 800px 16bpp scanout line burst, ~830 cycles) is
    // under two intervals, so pending never exceeds 2 of the 3 this holds.
    reg     [1:0]   refresh_pending;

    wire reset_n_s;
synch_3 s1(reset_n, reset_n_s, controller_clk);

// Diagnostic tap (combinational; downstream re-syncs to clk_cpu).
assign dbg_io = {1'b0, (refresh_pending != 2'd0), state[5:0]};

    reg word_rd_queue;
    reg word_wr_queue;

    // Word interface - same clock domain as controller (no CDC needed)
    // word_rd/word_wr are 1-cycle pulses, use directly as triggers

    // Captured address and data registers
    // Sender must hold these stable until the operation completes (word_busy goes low)
    reg [23:0] word_addr_captured;
    reg [31:0] word_data_captured;
    reg [3:0]  word_wstrb_captured;
    reg [31:0] word_data_next_captured;
    reg [3:0]  word_wstrb_next_captured;
    reg [3:0]  word_burst_len_captured;
    reg [3:0]  word_burst_wr_len_captured;
    reg [3:0]  wr_burst_remaining;
    // Rolling-preload bookkeeping for native burst writes.  Every native
    // burst is issued with beat 0 in word_data_captured and beat 1 in
    // word_data_next_captured (the AXI slave preloads both before raising
    // the command).  ST_WRITE_3 then shifts preload->active each beat, and
    // ST_WRITE_2 pulls beat N+2 while writing beat N: pull asserted at the
    // WRITE_2 edge, slave registers the selected beat one cycle later, and
    // the next WRITE_2 edge (two cycles after the pull — one full register-
    // to-register cycle of slack) captures it into the freed preload slot.
    // pull_inflight marks a pull whose data has not been captured yet, so a
    // row-crossing pause (ST_WRITE_4_NEWROW) can complete the capture in
    // ST_WRITE_4_NR_ACT — the slave holds the selected beat stable until
    // the next pull, so late capture is always safe.
    reg        pull_inflight;

    reg burst_rd_queue;
    // Set when burst_rd arrives while a word op is already queued (or its
    // request pulse lands on the same edge): that word op was accepted first
    // and must be serviced before the burst.  One-shot — cleared on the next
    // ST_IDLE dispatch — so it delays a scanout fetch by at most one word op.
    reg burst_defer_word;
    reg burstwr_queue;

    reg             word_op;
    reg     [24:0]  addr;

    reg     [10:0]  length;
    reg             enable_dq_read, enable_dq_read_1, enable_dq_read_2, enable_dq_read_3, enable_dq_read_4;
    reg             enable_dq_read_toggle;

    reg             enable_data_done, enable_data_done_1, enable_data_done_2, enable_data_done_3, enable_data_done_4;

    reg             read_newrow;

    // Open-page tracking (layout selected by BANK_ROW_TRACK — see the
    // parameter comment).  Per-bank (1): SDR SDRAM keeps four
    // independently open rows (one per bank), so cross-bank alternation
    // between the CPU / GPU / scanout / audio regions hits an
    // already-open row instead of paying PRECHG+tRP+ACT+tRCD (~7 cycles)
    // on every switch; a precharge is only needed when the REQUEST's own
    // bank holds a different row.  Single (0): only entry 0 + open_bank
    // are live; a hit additionally requires the bank to match, and a
    // miss precharges the TRACKED open bank.  Refresh precharges ALL
    // banks (A10=1) and clears every valid bit in both configs.
    reg     [12:0]  open_row [0:3];     // Open row, per bank (entry 0 only when BANK_ROW_TRACK==0)
    reg     [3:0]   row_open_v;         // Per-bank open-row valid (bit 0 only when BANK_ROW_TRACK==0)
    reg     [1:0]   open_bank;          // BANK_ROW_TRACK==0: bank of the single tracked row (swept when 1)

    // Track-entry index: per-bank tracking indexes by the bank itself;
    // the single-row config degenerates every access to entry 0 (constant
    // fold), so entries 1-3 lose all writers/readers and the bank-indexed
    // muxes collapse.
    function [1:0] trk;
        input [1:0] b;
        trk = (BANK_ROW_TRACK != 0) ? b : 2'd0;
    endfunction
    // (No tRAS counter: the dead open_timer reg was removed — tRAS is met
    //  implicitly by the FSM's ACT->...->PRECHARGE command latency.)
    reg     [1:0]   prechg_return;      // After precharge: 0=READ_0, 1=WRITE_0, 2=BURSTWR_0, 3=REFRESH_0
    reg             req_row_hit;
    reg             req_need_prechg;
    reg     [1:0]   req_bank;
    reg     [1:0]   req_prechg_bank;
    reg     [1:0]   nr_prechg_bank;     // Bank to precharge on a burst-write row crossing

    // Open-page row-hit detection (combinational; the decision is still
    // REGISTERED into req_* at ST_IDLE dispatch — the bank-indexed mux
    // adds one LUT level there, not on the SDRAM command output path)
    wire    [24:0]  pending_addr      = word_addr_captured << 1;
    wire    [1:0]   pending_bank      = pending_addr[24:23];
    wire    [12:0]  pending_row       = pending_addr[22:10];
    // BANK_ROW_TRACK==1: bank term is constant 1 (per-bank entries imply
    // the bank), reducing to the pure row compare.  ==0: a hit requires
    // the same bank AND the same row of the single tracked entry.
    wire            pending_bank_ok   = (BANK_ROW_TRACK != 0) ||
                                        (open_bank == pending_bank);
    wire            pending_row_hit   = row_open_v[trk(pending_bank)] &&
                                        pending_bank_ok &&
                                        (open_row[trk(pending_bank)] == pending_row);
    wire            pending_need_prechg = row_open_v[trk(pending_bank)] &&
                                          !(pending_bank_ok &&
                                            (open_row[trk(pending_bank)] == pending_row));

    reg     [15:0]  phy_dq_latched;
always @(posedge controller_clk) begin
    phy_dq_latched <= phy_dq_in;
end


always @(*) begin
    burst_data_done <= enable_data_done_4;
end
initial begin
    state <= ST_RESET;
    phy_cke <= 0;
end
always @(posedge controller_clk) begin
    phy_dq_oe <= 0;
    cmd <= CMD_NOP;
    // Default DQM low (no mask) every cycle so phy_dqm is a clean load-only
    // output register (no clear+load conflict) → packs into the IOB output
    // register, fixing the dram_dqm output-setup path.  Write states override
    // it the same cycle the SDRAM samples DQM (behaviour identical; verified
    // byte-exact via the shared sdram-all suite on the pocket copy, and on
    // the Q17 flow both this and the pre-refactor fabric topology fit with
    // the wstrb→DQM paths intact — confirmed by STA path queries 2026-07-02).
    // NOTE (MiSTer modules): the dedicated DQML/DQMH pins are only half the
    // story — pin-saving module designs wire the chip's DQM inputs from
    // A12/A11 instead, so the write states mirror the mask onto phy_a[12:11]
    // as well (see ST_WRITE_2).  Driving only the dedicated pins makes every
    // sub-word write land full-width on such modules (SuperStation One,
    // HW-decoded 2026-07-02).
    phy_dqm <= 2'b00;
    dc <= dc + 1'b1;

    // (word_rd/word_wr are same clock domain - no edge detection needed)

    burst_data_valid <= 0;
    burstwr_ready <= 0;
    word_q_valid <= 0;  // Clear each cycle, set when read data is captured
    word_wr_data_next <= 0;
    word_wr_done <= 0;  // 1-cycle pulse, default low

    enable_dq_read_4 <= enable_dq_read_3;
    enable_dq_read_3 <= enable_dq_read_2;
    enable_dq_read_2 <= enable_dq_read_1;
    enable_dq_read_1 <= enable_dq_read;
    enable_dq_read <= 0;

    enable_data_done_4 <= enable_data_done_3;
    enable_data_done_3 <= enable_data_done_2;
    enable_data_done_2 <= enable_data_done_1;
    enable_data_done_1 <= enable_data_done;
    enable_data_done <= 0;


    // delayed by CAS latency for reads
    // CAS=3 means data appears 3 cycles after READ command
    // enable_dq_read_4 = CAS + 1 (for input register latency)
    if(enable_dq_read_4) begin
        enable_dq_read_toggle <= ~enable_dq_read_toggle;

        if(word_op) begin
            if(~enable_dq_read_toggle) begin
                // First read from low address: contains low 16 bits (little-endian)
                word_q[15:0] <= phy_dq_latched;
            end else begin
                // Second read from high address: contains high 16 bits, word complete
                word_q[31:16] <= phy_dq_latched;
                word_q_valid <= 1;  // Signal that word_q is valid
            end

        end else begin
            if(burst_32bit) begin
                // accumulate 32-bit word from BL=2 burst
                if(~enable_dq_read_toggle) begin
                    // First beat: low 16 bits
                    burst_data[15:0] <= phy_dq_latched;
                end else begin
                    // Second beat: high 16 bits
                    burst_data[31:16] <= phy_dq_latched;
                    burst_data_valid <= 1;
                end
            end else begin
                // 16-bit
                burst_data[15:0] <= phy_dq_latched;
                burst_data_valid <= 1;
            end
        end
    end


    case(state)
    ST_RESET: begin
        phy_cke <= 0;
        cmd <= CMD_NOP;
        delay_boot <= 0;
        refresh_pending <= 2'd0;
        phy_dqm <= 2'b00;

        state <= ST_BOOT_0;
    end
    ST_BOOT_0: begin
        delay_boot <= delay_boot + 1'b1;

        if(delay_boot == 30000-16) phy_cke <= 1;
        if(delay_boot == 30000) begin
            // >=200us power-up delay (30000 cycles @90MHz ~= 333us)
            dc <= 0;

            // precharge all
            cmd <= CMD_PRECHG;
            phy_a[10] = 1'b1;

            state <= ST_BOOT_1;
        end
    end
    ST_BOOT_1: begin
        if(dc == TIMING_PRECHARGE-1) begin
            dc <= 0;
            cmd <= CMD_AUTOREF;

            state <= ST_BOOT_2;
        end
    end
    ST_BOOT_2: begin
        if(dc == TIMING_AUTOREFRESH-1) begin
            dc <= 0;
            cmd <= CMD_AUTOREF;

            state <= ST_BOOT_3;
        end
    end
    ST_BOOT_3: begin
        if(dc == TIMING_AUTOREFRESH-1) begin
            dc <= 0;
            cmd <= CMD_LMR;
            phy_ba <= 'b00;
            phy_a <= 13'b000000_011_0_001; // CAS 3, burst length 2, sequential

            state <= ST_BOOT_4;
        end
    end
    ST_BOOT_4: begin
        if(dc == TIMING_LMR-1) begin
            dc <= 0;
            cmd <= CMD_LMR;
            phy_ba <= 'b10; // Extended mode register
            phy_a <= 13'b00000_010_00_000; // Self refresh coverage: All banks,
            // drive strength = 3'b010 (alliance, 50%)
            state <= ST_BOOT_5;
        end
    end
    ST_BOOT_5: begin
        if(dc == TIMING_LMR-1) begin
            phy_dqm <= 2'b00;

            state <= ST_IDLE;
        end
    end


    ST_IDLE: begin

        read_newrow <= 0;
        word_busy <= 0;
        word_op <= 0;

        if(refresh_pending != 2'd0) begin
            word_busy <= 1;
            if(row_open_v != 4'd0) begin
                // Precharge ALL banks before refresh: with per-bank
                // tracking any subset of the four banks may hold an open
                // row, and AUTOREFRESH requires every bank idle.  A10=1 is
                // the all-banks precharge (phy_ba is don't-care).
                dc <= 0;
                cmd <= CMD_PRECHG;
                phy_a[10] <= 1'b1;
                row_open_v <= 4'd0;
                prechg_return <= 2'd3;  // 3 = refresh
                state <= ST_PRECHG_WAIT;
            end else begin
                state <= ST_REFRESH_0;
            end
        end else
        // Video scanout fetch is hard-real-time: it must deliver one line of
        // pixels per scanline or the display shows a blank/stale line, and
        // under sustained GPU framebuffer writes a starved fetch corrupts the
        // whole frame (observed as Doom's blank / half-stale screen).  Serve it
        // right after refresh — ABOVE the CPU/GPU word read/write queues — so a
        // heavy GPU writer cannot starve it.  The fetch is a single bounded
        // line-burst per scanline, so CPU/GPU traffic only sees a small, bounded
        // added latency (their posted queues / cache absorb it).
        //
        // Exception (burst_defer_word): a word op that was already queued —
        // i.e. ACCEPTED — when the burst_rd arrived must not be preempted.
        // axi_sdram_slave treats acceptance as "committed soon" (S_WR_DON
        // waits for word_wr_done to release B); letting a later line fetch
        // jump ahead of an accepted write stalls that B response for the
        // whole line burst (~170 cycles).  The deference is one-shot: the
        // scanout fetch waits for at most ONE word op (~15 cycles for a
        // single word, ~40 for a 16-beat native burst write), which is
        // negligible against a scanline period, so it cannot be starved.
        if(burst_rd_queue && !(burst_defer_word && (word_rd_queue || word_wr_queue))) begin
            burst_rd_queue <= 0;
            burst_defer_word <= 0;
            addr <= burst_addr;
            phy_ba <= burst_addr[24:23];
            length <= burst_len;
            word_busy <= 1;
            req_row_hit <= row_open_v[trk(burst_addr[24:23])] &&
                           ((BANK_ROW_TRACK != 0) ||
                            (open_bank == burst_addr[24:23])) &&
                           (open_row[trk(burst_addr[24:23])] == burst_addr[22:10]);
            req_need_prechg <= row_open_v[trk(burst_addr[24:23])] &&
                               !(((BANK_ROW_TRACK != 0) ||
                                  (open_bank == burst_addr[24:23])) &&
                                 (open_row[trk(burst_addr[24:23])] == burst_addr[22:10]));
            req_bank <= burst_addr[24:23];
            // Per-bank: only the request's own bank ever needs precharging.
            // Single-row config: the precharge target is the TRACKED open
            // bank — the request's own bank is untracked and must not be
            // ACTivated while another bank's row is open.
            req_prechg_bank <= (BANK_ROW_TRACK != 0) ? burst_addr[24:23]
                                                     : open_bank;
            state <= ST_REQ_BURST_READ;
        end else
        if(word_rd_queue) begin
            word_rd_queue <= 0;
            burst_defer_word <= 0;
            word_op <= 1;
            addr <= pending_addr;
            phy_ba <= pending_bank;
            word_busy <= 1;
            length <= {7'd0, word_burst_len_captured} + 11'd1;

            req_row_hit <= pending_row_hit;
            req_need_prechg <= pending_need_prechg;
            req_bank <= pending_bank;
            req_prechg_bank <= (BANK_ROW_TRACK != 0) ? pending_bank
                                                     : open_bank;
            state <= ST_REQ_READ;
        end else
        if(word_wr_queue) begin
            word_wr_queue <= 0;
            burst_defer_word <= 0;
            word_op <= 1;
            addr <= pending_addr;
            phy_ba <= pending_bank;
            word_busy <= 1;
            wr_burst_remaining <= word_burst_wr_len_captured;

            req_row_hit <= pending_row_hit;
            req_need_prechg <= pending_need_prechg;
            req_bank <= pending_bank;
            req_prechg_bank <= (BANK_ROW_TRACK != 0) ? pending_bank
                                                     : open_bank;
            state <= ST_REQ_WRITE;
        end else
        if(burstwr_queue) begin
            burstwr_queue <= 0;
            addr <= burstwr_addr;
            phy_ba <= burstwr_addr[24:23];
            word_busy <= 1;
            req_need_prechg <= row_open_v[trk(burstwr_addr[24:23])];
            req_prechg_bank <= (BANK_ROW_TRACK != 0) ? burstwr_addr[24:23]
                                                     : open_bank;
            state <= ST_REQ_BURST_WRITE;
        end

    end

    // Registered request dispatch.  This keeps the per-bank open-row state out of
    // the SDRAM command output mux and gives the fitter one cycle between
    // row-hit comparison and PRECHG/READ/WRITE command issue.
    ST_REQ_READ: begin
        if(req_row_hit) begin
            // ROW HIT: skip ACT+tRCD, go directly to READ
            phy_ba <= req_bank;
            enable_dq_read_toggle <= 0;
            state <= ST_READ_2;
        end else if(req_need_prechg) begin
            // ROW MISS or DIFFERENT BANK: precharge, then ACT
            dc <= 0;
            cmd <= CMD_PRECHG;
            phy_ba <= req_prechg_bank;
            phy_a[10] <= 1'b0;
            row_open_v[trk(req_prechg_bank)] <= 1'b0;
            prechg_return <= 2'd0;
            state <= ST_PRECHG_WAIT;
        end else begin
            // NO ROW OPEN: normal ACT path
            state <= ST_READ_0;
        end
    end

    ST_REQ_WRITE: begin
        if(req_row_hit) begin
            // ROW HIT: 1-cycle DQ setup, then WRITE
            phy_ba <= req_bank;
            state <= ST_WRITE_HIT;
        end else if(req_need_prechg) begin
            // ROW MISS or DIFFERENT BANK: precharge, then ACT
            dc <= 0;
            cmd <= CMD_PRECHG;
            phy_ba <= req_prechg_bank;
            phy_a[10] <= 1'b0;
            row_open_v[trk(req_prechg_bank)] <= 1'b0;
            prechg_return <= 2'd1;
            state <= ST_PRECHG_WAIT;
        end else begin
            // NO ROW OPEN: normal ACT path
            state <= ST_WRITE_0;
        end
    end

    ST_REQ_BURST_READ: begin
        if(req_row_hit) begin
            // ROW HIT: skip precharge+ACT, go directly to READ
            phy_ba <= req_bank;
            enable_dq_read_toggle <= 0;
            state <= ST_READ_2;
        end else if(req_need_prechg) begin
            // ROW MISS: precharge open bank, then ACT
            dc <= 0;
            cmd <= CMD_PRECHG;
            phy_ba <= req_prechg_bank;
            phy_a[10] <= 1'b0;
            row_open_v[trk(req_prechg_bank)] <= 1'b0;
            prechg_return <= 2'd0;
            state <= ST_PRECHG_WAIT;
        end else begin
            // NO ROW OPEN: normal ACT path
            state <= ST_READ_0;
        end
    end

    ST_REQ_BURST_WRITE: begin
        if(req_need_prechg) begin
            // Precharge open bank before burst ACT
            dc <= 0;
            cmd <= CMD_PRECHG;
            phy_ba <= req_prechg_bank;
            phy_a[10] <= 1'b0;
            row_open_v[trk(req_prechg_bank)] <= 1'b0;
            prechg_return <= 2'd2;
            state <= ST_PRECHG_WAIT;
        end else begin
            state <= ST_BURSTWR_0;
        end
    end


    // Open-page: wait tRP after precharge, then dispatch
    ST_PRECHG_WAIT: begin
        if(dc == TIMING_PRECHARGE-1) begin
            case(prechg_return)
                2'd0: begin
                    phy_ba <= req_bank;
                    state <= ST_READ_0;
                end
                2'd1: begin
                    phy_ba <= req_bank;
                    state <= ST_WRITE_0;
                end
                2'd2: begin
                    phy_ba <= addr[24:23];
                    state <= ST_BURSTWR_0;
                end
                2'd3: state <= ST_REFRESH_0;
            endcase
        end
    end

    // Open-page: row-hit write DQ setup (1 cycle for tristate turn-on)
    ST_WRITE_HIT: begin
        phy_a[10] <= 1'b0;
        phy_dq_oe <= 1;
        state <= ST_WRITE_2;
    end


    ST_WRITE_0: begin
        dc <= 0;

        phy_a <= addr[22:10]; // A0-A12 row address
        cmd <= CMD_ACT;

        // Track open row (per-bank).  phy_ba is re-driven from addr so a
        // row-crossing resume that lands in a new bank (16MB boundary)
        // activates the correct bank.
        phy_ba <= addr[24:23];
        row_open_v[trk(addr[24:23])] <= 1'b1;
        open_row[trk(addr[24:23])] <= addr[22:10];
        open_bank <= addr[24:23];

        state <= ST_WRITE_1;
    end
    ST_WRITE_1: begin
        phy_a[10] <= 1'b0; // no auto precharge
        if(dc == TIMING_ACT_RW-1) begin
            dc <= 0;
            phy_dq_oe <= 1;
            state <= ST_WRITE_2;
        end
    end
    ST_WRITE_2: begin
        dc <= 0;

        // MiSTer module convention: the byte mask ALSO rides on A[12:11]
        // ({DQMH,DQML} = A[12:11], column don't-cares during CAS/data
        // cycles).  Pin-saving module designs — incl. the SuperStation
        // One's integrated 128MB SDRAM — wire the chip's DQM inputs from
        // A12/A11 instead of the dedicated DQML/DQMH lines, so a
        // controller that only drives the dedicated pins writes ALL byte
        // lanes on sub-word stores there (HW-decoded 2026-07-02).  Every
        // classic MiSTer core drives both; so do we.  A[10] stays 0 (no
        // auto-precharge).
        phy_a <= {~word_wstrb_captured[1:0], 1'b0, addr[9:0]};
        cmd <= CMD_WRITE;
        phy_dq_oe <= 1;
        phy_dq_out <= word_data_captured[15:0];
        phy_dqm <= ~word_wstrb_captured[1:0];

        // Rolling preload: capture the beat requested by the PREVIOUS
        // WRITE_2's pull into the preload slot (freed by last WRITE_3's
        // shift), and pull the beat after the one in the preload slot.
        // A pull is only needed while at least two beats remain after the
        // current one (the next beat is already preloaded).
        if (pull_inflight) begin
            word_data_next_captured <= burst_wr_direct_data;
            word_wstrb_next_captured <= burst_wr_direct_strb;
        end
        if (wr_burst_remaining >= 4'd2)
            word_wr_data_next <= 1;
        pull_inflight <= (wr_burst_remaining >= 4'd2);

        state <= ST_WRITE_3;
    end
    ST_WRITE_3: begin
        dc <= 0;

        phy_dq_oe <= 1;
        phy_dq_out <= word_data_captured[31:16];
        phy_dqm <= ~word_wstrb_captured[3:2];
        // Second BL=2 beat's mask on A[12:11] too (see ST_WRITE_2).
        phy_a[12:11] <= ~word_wstrb_captured[3:2];

        if (wr_burst_remaining > 0) begin
            wr_burst_remaining <= wr_burst_remaining - 4'd1;
            addr <= addr + 2'd2;
            // Shift preload->active: the continuation beat is already in
            // controller registers, so the next BL=2 write issues on the
            // very next cycle — no cross-module pull/capture bubble.
            word_data_captured <= word_data_next_captured;
            word_wstrb_captured <= word_wstrb_next_captured;
            if ((addr[9:0] + 10'd2) <= 10'd1) begin
                // Next write would cross SDRAM row boundary.
                // Must finish current write, precharge, activate
                // new row, then continue burst.  Stash the bank being
                // written NOW (pre-increment addr): if the crossing also
                // crosses a 16MB bank boundary, that is the bank the
                // precharge must target, not the new one.
                nr_prechg_bank <= addr[24:23];
                state <= ST_WRITE_4_NEWROW;
            end else begin
                state <= ST_WRITE_2;
            end
        end else begin
            state <= ST_WRITE_4;
        end
    end
    ST_WRITE_4: begin
        phy_dqm <= 2'b00;
        phy_a[12:11] <= 2'b00;   // drop the A-mirrored mask with DQM
        // tWR budget: last data beat lands on the bus one cycle into this
        // state; every precharge that can follow goes through ST_IDLE
        // dispatch (+1 cycle registered cmd) or ST_REQ_* first, so exiting
        // at TIMING_WRITE-1 still leaves >=1 cycle of tWR margin on the
        // earliest same-bank precharge (refresh precharge-all).  Only
        // ST_WRITE_4_NEWROW issues CMD_PRECHG directly from its own state
        // and keeps the extra cycle.
        if(dc == TIMING_WRITE-1) begin
            state <= ST_IDLE;
            word_wr_done <= 1;  // Slave-issued word write committed: pulse so axi_sdram_slave can release bvalid without polling !word_busy across unrelated io_sdram activity (scanout burst_rd, autorefresh, etc.)
        end
    end
    // Row-crossing for burst writes: finish tWR, precharge, activate new row, resume.
    ST_WRITE_4_NEWROW: begin
        phy_dqm <= 2'b00;
        phy_a[12:11] <= 2'b00;   // drop the A-mirrored mask with DQM
        if(dc == TIMING_WRITE-1+1) begin
            // Precharge the bank that was just written (stashed in
            // ST_WRITE_3 before addr advanced into the new row)
            cmd <= CMD_PRECHG;
            phy_a[10] <= 0;
            phy_ba <= nr_prechg_bank;
            row_open_v[trk(nr_prechg_bank)] <= 1'b0;
            dc <= 0;
            state <= ST_WRITE_4_NR_PRECHG;
        end
    end
    ST_WRITE_4_NR_PRECHG: begin
        if(dc == TIMING_PRECHARGE-1) begin
            // Activate new row (addr already points into it; re-drive
            // phy_ba in case the crossing entered a new bank)
            phy_a <= addr[22:10];
            phy_ba <= addr[24:23];
            cmd <= CMD_ACT;
            row_open_v[trk(addr[24:23])] <= 1'b1;
            open_row[trk(addr[24:23])] <= addr[22:10];
            open_bank <= addr[24:23];
            dc <= 0;
            state <= ST_WRITE_4_NR_ACT;
        end
    end
    ST_WRITE_4_NR_ACT: begin
        phy_a[10] <= 1'b0;
        if(dc == TIMING_ACT_RW-1) begin
            dc <= 0;
            // The resume beat was already shifted into word_data_captured
            // by ST_WRITE_3 before the crossing.  Complete any outstanding
            // pull here: the slave holds the selected beat stable on
            // burst_wr_direct_* until the next pull, so capturing it during
            // the precharge/activate pause is always safe.
            if (pull_inflight) begin
                word_data_next_captured <= burst_wr_direct_data;
                word_wstrb_next_captured <= burst_wr_direct_strb;
                pull_inflight <= 0;
            end
            phy_dq_oe <= 1;
            state <= ST_WRITE_2;
        end
    end


    ST_READ_0: begin
        dc <= 0;

        phy_a <= addr[22:10]; // A0-A12 row address
        cmd <= CMD_ACT;

        // Track open row (per-bank).  phy_ba is re-driven from addr so a
        // row-crossing resume that lands in a new bank (16MB boundary)
        // activates the correct bank.
        phy_ba <= addr[24:23];
        row_open_v[trk(addr[24:23])] <= 1'b1;
        open_row[trk(addr[24:23])] <= addr[22:10];
        open_bank <= addr[24:23];

        state <= ST_READ_1;
    end
    ST_READ_1: begin
        phy_a[10] <= 1'b0; // no auto precharge
        enable_dq_read_toggle <= 0;
        if(dc == TIMING_ACT_RW-1) begin
            dc <= 0;
            state <= ST_READ_2;
        end
    end
    ST_READ_2: begin
        phy_a <= addr[9:0]; // A0-A9 column address
        cmd <= CMD_READ;

        enable_dq_read <= 1;  // First BL=2 data beat

        length <= length - 1'b1;
        addr <= addr + 2'd2;  // BL=2: skip 2 half-word addresses per READ

        // Always go to ST_READ_3 for second BL=2 data beat
        state <= ST_READ_3;
    end
    ST_READ_3: begin
        // Second BL=2 data beat
        enable_dq_read <= 1;

        if(length == 0) begin
            // All READs issued, drain pipeline
            read_newrow <= 0;
            state <= ST_READ_5;
        end else if(addr[9:0] <= 10'd1) begin
            // Near end of row, need to activate next row
            read_newrow <= 1;
            state <= ST_READ_5;
        end else begin
            // More READs needed (burst mode)
            state <= ST_READ_2;
        end
    end
    ST_READ_5: begin
        state <= ST_READ_8;
    end
    ST_READ_8: begin
        state <= ST_READ_9;
    end
    ST_READ_9: begin
        state <= ST_READ_6;
    end
    ST_READ_6: begin
        if(!read_newrow && !word_op) enable_data_done <= 1;
        dc <= 0;

        if(!read_newrow) begin
            // No row crossing: leave row open, return to IDLE
            // (applies to both word and burst reads)
            state <= ST_IDLE;
        end else begin
            // Row-crossing: precharge and activate next row.  phy_ba still
            // holds the bank of the row being read (set at dispatch or the
            // last ST_READ_0 ACT), which is exactly the precharge target —
            // addr has already advanced and may point into a new bank.
            cmd <= CMD_PRECHG;
            phy_a[10] <= 0; // only precharge current bank
            row_open_v[trk(phy_ba)] <= 1'b0;
            state <= ST_READ_7;
        end
    end
    ST_READ_7: begin
        if(dc == TIMING_PRECHARGE-1) begin
            if(read_newrow)
                state <= ST_READ_0;
            else
                state <= ST_IDLE;
        end
    end

    ST_BURSTWR_0: begin
        phy_a <= addr[22:10]; // A0-A12 column address
        cmd <= CMD_ACT;
        state <= ST_BURSTWR_1;
    end
    ST_BURSTWR_1: begin
        cmd <= CMD_NOP;
        state <= ST_BURSTWR_2;
    end
    ST_BURSTWR_2: begin
        cmd <= CMD_NOP;
        state <= ST_BURSTWR_3;
    end
    ST_BURSTWR_3: begin
        burstwr_ready <= 1;

        if(burstwr_strobe) begin

            phy_a <= addr[9:0]; // A0-A9 row address
            cmd <= CMD_WRITE;
            phy_dq_oe <= 1;
            phy_dq_out <= burstwr_data;

            addr <= addr + 1'b1;
        end
        if(burstwr_strobe | burstwr_done) begin
            state <= ST_BURSTWR_4;
        end
    end
    ST_BURSTWR_4: begin
        cmd <= CMD_NOP;
        phy_dqm <= 2'b11;  // Mask unwanted second BL=2 data beat
        state <= ST_BURSTWR_5;
    end
    ST_BURSTWR_5: begin
        cmd <= CMD_PRECHG;
        phy_a[10] <= 0; // only precharge current bank
        phy_dqm <= 2'b00;  // Restore DQM for future operations
        row_open_v[trk(addr[24:23])] <= 1'b0;  // Track bank close (burstwr never crosses banks)
        state <= ST_BURSTWR_6;
    end
    ST_BURSTWR_6: begin
        cmd <= CMD_NOP;
        state <= ST_BURSTWR_7;
    end
    ST_BURSTWR_7: begin
        cmd <= CMD_NOP;
        state <= ST_IDLE;
    end


    ST_REFRESH_0: begin
        // autorefresh — refresh_pending is decremented by the generator below
        // (state==ST_REFRESH_0 this cycle).
        cmd <= CMD_AUTOREF;
        dc <= 0;
        state <= ST_REFRESH_1;
    end
    ST_REFRESH_1: begin
        if(dc == TIMING_AUTOREFRESH-1)  begin
            state <= ST_IDLE;
        end
    end

    endcase


    // catch incoming events if fsm is busy
    // Same clock domain - capture directly on pulse
    if(word_wr) begin
        word_wr_queue <= 1;
        word_addr_captured <= word_addr;
        word_data_captured <= word_data;
        word_wstrb_captured <= word_wstrb;
        word_data_next_captured <= word_data_next;
        word_wstrb_next_captured <= word_wstrb_next;
        word_burst_wr_len_captured <= word_burst_wr_len;
    end else if(word_rd) begin
        word_rd_queue <= 1;
        word_addr_captured <= word_addr;
        word_burst_len_captured <= word_burst_len;
    end
    if(burst_rd) begin
        burst_rd_queue <= 1;
        // word_rd/word_wr cover a request pulse landing on this same edge;
        // the *_queue flags cover one already accepted on an earlier edge.
        burst_defer_word <= word_rd | word_wr | word_rd_queue | word_wr_queue;
    end
    if(burstwr) begin
        burstwr_queue <= 1;
    end

    // autorefresh generator
    // every REFRESH_INTERVAL cycles; see the declaration comment for the
    // spec margin.
    refresh_count <= refresh_count + 1'b1;
    if(refresh_count == REFRESH_INTERVAL - 1)
        refresh_count <= 0;
    // A refresh tick (counter wrap) increments the pending count; issuing one
    // in ST_REFRESH_0 decrements it.  Combined into a single assignment so a
    // simultaneous tick+issue nets correctly — the old single flag dropped the
    // second tick in that case.
    refresh_pending <= refresh_pending
                     + ((refresh_count == REFRESH_INTERVAL - 1) ? 2'd1 : 2'd0)
                     - ((state == ST_REFRESH_0) ? 2'd1 : 2'd0);

    if(~reset_n_s) begin
        // reset
        state <= ST_RESET;
        refresh_count <= 0;
        refresh_pending <= 2'd0;
        word_rd_queue <= 0;
        word_wr_queue <= 0;
        burst_rd_queue <= 0;
        burst_defer_word <= 0;
        burstwr_queue <= 0;
        word_addr_captured <= 0;
        word_data_captured <= 0;
        word_wstrb_captured <= 0;
        word_data_next_captured <= 0;
        word_wstrb_next_captured <= 0;
        word_burst_len_captured <= 0;
        word_burst_wr_len_captured <= 0;
        wr_burst_remaining <= 0;
        pull_inflight <= 0;
        word_q <= 0;
        word_busy <= 0;
        word_q_valid <= 0;
        word_wr_data_next <= 0;
        word_wr_done <= 0;
        enable_dq_read_toggle <= 0;
        row_open_v <= 4'd0;
        open_bank <= 2'd0;
        prechg_return <= 2'd0;
        req_row_hit <= 0;
        req_need_prechg <= 0;
        req_bank <= 0;
        req_prechg_bank <= 0;
        nr_prechg_bank <= 0;
    end
end

assign phy_clk = chip_clk;

endmodule
