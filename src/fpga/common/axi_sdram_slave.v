//
// AXI4 Slave Wrapper for SDRAM (io_sdram word interface)
//
// Converts AXI4 transactions to the io_sdram word-level protocol:
//   word_rd/wr pulse → accepted → busy → rdata_valid (reads)
//
// Features:
//   - Burst reads: ARLEN → word_burst_len (0=1 word, 7=8 words)
//   - Single and burst writes: each W beat → word_wr
//   - Single outstanding transaction
//   - 0 M10K (pure register/LUT)
//

`default_nettype none

module axi_sdram_slave (
    input wire clk,
    input wire reset_n,

    // AXI4 Slave interface
    // AR channel (read address)
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    // R channel (read data)
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    // AW channel (write address)
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    // W channel (write data)
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    // B channel (write response)
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]  s_axi_bresp,

    // SDRAM word interface (directly to arbiter in core_top.v)
    output reg         sdram_rd,
    output reg         sdram_wr,
    output reg  [23:0] sdram_addr,
    output reg  [31:0] sdram_wdata,
    output reg  [3:0]  sdram_wstrb,
    output reg  [3:0]  sdram_burst_len,
    output reg  [3:0]  sdram_burst_wr_len,
    input  wire [31:0] sdram_rdata,
    input  wire        sdram_busy,
    input  wire        sdram_accepted,
    input  wire        sdram_rdata_valid,
    input  wire        sdram_wr_data_next, // io_sdram needs next word for burst write
    output wire [31:0] sdram_next_wdata,   // Pre-staged next word (combinational)
    output wire [3:0]  sdram_next_wstrb
);

wire reset = ~reset_n;

// FSM states
localparam S_IDLE      = 4'd0;
localparam S_RD_CMD    = 4'd1;  // Issue word_rd, wait for accepted
localparam S_RD_DAT    = 4'd2;  // Wait for rdata_valid, send R beats
localparam S_WR_CMD    = 4'd3;  // Issue word_wr, wait for accepted
localparam S_WR_DON    = 4'd4;  // Wait for write completion (!busy)
localparam S_WR_NEXT   = 4'd5;  // Accept next W beat (single-word writes)
localparam S_WR_BURST  = 4'd6;  // Streaming: io_sdram pulls data via wr_data_next

reg [3:0] state;

// Transaction tracking
reg [7:0]  burst_len;    // ARLEN/AWLEN
reg [7:0]  beat_count;   // Beats completed
reg [31:0] addr_r;       // Current address (advances per beat)
reg        cmd_issued;   // word_rd/wr issued, waiting for accepted
reg [31:0] next_wdata;   // Pre-staged next W beat data (burst write)
reg [3:0]  next_wstrb;
reg        wready_given; // Next W beat already accepted
reg        wr_busy_seen; // word_busy seen high during burst (guards early bvalid)

// Combinational export of pre-staged data for burst write forwarding
assign sdram_next_wdata = next_wdata;
assign sdram_next_wstrb = next_wstrb;
reg        started;      // accepted seen, waiting for completion

// 2-entry skid buffer (primary R slot + skid) so that back-pressure
// from the master doesn't drop sdram_rdata_valid pulses.  Required now
// that cpu_target_port has a registered response slot that can
// transiently lower rready mid-burst.  Without this, 1-cycle
// rdata_valid pulses get silently lost when rvalid is still held from
// the previous beat.
reg [31:0] rskid_data;
reg        rskid_last;
reg        rskid_valid;

wire beat_is_last = (beat_count == burst_len);

always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        burst_len <= 0;
        beat_count <= 0;
        addr_r <= 0;
        cmd_issued <= 0;
        started <= 0;

        s_axi_arready <= 0;
        s_axi_rvalid <= 0;
        s_axi_rdata <= 0;
        s_axi_rresp <= 0;
        s_axi_rlast <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_bvalid <= 0;
        s_axi_bresp <= 0;

        sdram_rd <= 0;
        sdram_wr <= 0;
        sdram_addr <= 0;
        sdram_wdata <= 0;
        sdram_wstrb <= 0;
        sdram_burst_len <= 0;
        sdram_burst_wr_len <= 0;
        next_wdata <= 0;
        next_wstrb <= 0;
        wready_given <= 0;
        wr_busy_seen <= 0;

        rskid_data  <= 0;
        rskid_last  <= 0;
        rskid_valid <= 0;
    end else begin
        // Defaults: deassert single-cycle signals.  rvalid and bvalid
        // are NOT in this list — they are "hold until *ready" signals
        // and are explicitly dropped on their respective handshakes
        // below.
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        sdram_rd <= 0;
        sdram_wr <= 0;
        sdram_burst_len <= 0;
        sdram_burst_wr_len <= 0;

        // AXI handshake drop-on-accept for rvalid.  When the master
        // consumes a beat, either drain the skid into the R slot or
        // clear the slot so the next sdram_rdata_valid pulse can land.
        if (s_axi_rvalid && s_axi_rready) begin
            if (rskid_valid) begin
                s_axi_rdata  <= rskid_data;
                s_axi_rlast  <= rskid_last;
                rskid_valid  <= 1'b0;
                // rvalid stays high — skid is promoted into the slot.
            end else begin
                s_axi_rvalid <= 1'b0;
            end
        end
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;

        case (state)

        S_IDLE: begin
            cmd_issued <= 0;
            started <= 0;
            // Reads have priority over writes.  Don't accept a new AR
            // while the previous burst's last beat is still draining
            // through the R slot or skid — otherwise we'd smash the
            // held rvalid/rdata with the new burst's first beat.
            if (s_axi_arvalid && !s_axi_rvalid && !rskid_valid) begin
                s_axi_arready <= 1;
                addr_r <= s_axi_araddr;
                burst_len <= s_axi_arlen;
                beat_count <= 0;
                // Early command issue: if SDRAM idle, start read 1 cycle sooner
                if (!sdram_busy) begin
                    sdram_rd <= 1;
                    sdram_addr <= s_axi_araddr[25:2];
                    sdram_burst_len <= s_axi_arlen[3:0];
                    cmd_issued <= 1;
                end
                state <= S_RD_CMD;
            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1;
                addr_r <= s_axi_awaddr;
                burst_len <= s_axi_awlen;
                beat_count <= 0;
                // Also accept W if valid (common case: AW and W arrive together)
                if (s_axi_wvalid) begin
                    s_axi_wready <= 1;
                    sdram_wdata <= s_axi_wdata;
                    sdram_wstrb <= s_axi_wstrb;
                    // Early command issue for writes too
                    if (!sdram_busy) begin
                        sdram_wr <= 1;
                        sdram_addr <= s_axi_awaddr[25:2];
                        sdram_burst_wr_len <= s_axi_awlen[3:0];
                        cmd_issued <= 1;
                    end
                    state <= S_WR_CMD;
                end else begin
                    state <= S_WR_NEXT;
                end
            end
        end

        // ============================================
        // Read path
        // ============================================
        S_RD_CMD: begin
            // Issue read to SDRAM, hold until accepted
            if (!cmd_issued) begin
                if (!sdram_busy) begin
                    sdram_rd <= 1;
                    sdram_addr <= addr_r[25:2];
                    sdram_burst_len <= burst_len[3:0];
                    cmd_issued <= 1;
                    started <= 0;
                end
            end else begin
                // Hold read request until arbiter accepts
                sdram_rd <= 1;
                sdram_addr <= addr_r[25:2];
                sdram_burst_len <= burst_len[3:0];
                if (sdram_accepted) begin
                    started <= 1;
                    state <= S_RD_DAT;
                end
            end
        end

        S_RD_DAT: begin
            // Wait for read data.  Gate with started to prevent
            // capturing peripheral data before our command was
            // accepted.  Incoming sdram_rdata_valid pulses route to
            // either the primary R slot (if empty OR just drained
            // this cycle by the handshake-drop block above) or the
            // 1-entry skid.  If both are full the beat is lost —
            // shouldn't happen once rready is properly propagated
            // through the arbiter.
            if (started && sdram_rdata_valid) begin
                // "slot_free_next_cycle" accounts for the handshake-drop
                // block above which may be draining the slot in the
                // same cycle.  If a drop is happening, the new beat
                // can land directly into the slot.
                if (!s_axi_rvalid ||
                    (s_axi_rvalid && s_axi_rready && !rskid_valid)) begin
                    s_axi_rvalid <= 1;
                    s_axi_rdata  <= sdram_rdata;
                    s_axi_rresp  <= 2'b00;
                    s_axi_rlast  <= beat_is_last;
                end else if (!rskid_valid) begin
                    rskid_valid <= 1'b1;
                    rskid_data  <= sdram_rdata;
                    rskid_last  <= beat_is_last;
                end
                // else: both full → overflow. Guarded by rready propagation.
                beat_count <= beat_count + 1;
                if (beat_is_last) begin
                    cmd_issued <= 0;
                    started    <= 0;
                    state      <= S_IDLE;
                end
            end
        end

        // ============================================
        // Write path
        // ============================================
        S_WR_CMD: begin
            // Issue write to SDRAM, hold until accepted
            if (!cmd_issued) begin
                if (!sdram_busy) begin
                    sdram_wr <= 1;
                    sdram_addr <= addr_r[25:2];
                    sdram_burst_wr_len <= burst_len[3:0];
                    cmd_issued <= 1;
                    started <= 0;
                end
            end else begin
                sdram_wr <= 1;
                sdram_addr <= addr_r[25:2];
                sdram_burst_wr_len <= burst_len[3:0];
                if (sdram_accepted) begin
                    started <= 1;
                    if (burst_len == 0)
                        state <= S_WR_DON;   // Single word: wait for !busy
                    else begin
                        wready_given <= 0;
                        wr_busy_seen <= 0;
                        state <= S_WR_BURST;  // Burst: stream data
                    end
                end
            end
        end

        S_WR_BURST: begin
            // Streaming burst write: when io_sdram signals wr_data_next,
            // accept the next W beat and update sdram_wdata.
            // io_sdram has 3 gap cycles (ST_WRITE_5/6/7) to let data propagate.
            // Track when io_sdram actually starts (word_busy goes high)
            // — required because sdram_busy lags sdram_accepted by a
            // few cycles; without the gate we'd false-complete during
            // the request-to-busy window.  Completion (wr_busy_seen &&
            // !sdram_busy) takes priority over the wr_data_next pull
            // so the beat_count increment can't collide.
            if (sdram_busy) wr_busy_seen <= 1;
            if (wr_busy_seen && !sdram_busy) begin
                cmd_issued <= 0;
                started <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end else if (sdram_wr_data_next) begin
                beat_count <= beat_count + 1;
                if (s_axi_wvalid) begin
                    s_axi_wready <= 1;
                    sdram_wdata <= s_axi_wdata;
                    sdram_wstrb <= s_axi_wstrb;
                end
            end
        end

        S_WR_DON: begin
            // Wait for single-word write completion (burst_len == 0 only).
            if (started && !sdram_busy) begin
                cmd_issued <= 0;
                started <= 0;
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end
        end

        S_WR_NEXT: begin
            // Accept next W beat (single-word fallback path)
            if (s_axi_wvalid) begin
                s_axi_wready <= 1;
                sdram_wdata <= s_axi_wdata;
                sdram_wstrb <= s_axi_wstrb;
                state <= S_WR_CMD;
            end
        end

        default: state <= S_IDLE;

        endcase
    end
end

endmodule
