//------------------------------------------------------------------------------
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileType: SOURCE
// SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
//------------------------------------------------------------------------------

//
// hps_bridge — MiSTer replacement for the APF bridge stack
// (core_bridge_cmd.v + bridge_to_sdram.v + CRAM staging).
//
// Three jobs, all in the clk_cpu/clk_ram domain (hps_io runs with
// clk_sys = clk_cpu, so there is no CDC in here):
//
//  1. boot.rom delivery: the MiSTer framework streams boot.rom (= os.bin)
//     over ioctl at core start.  16-bit ioctl words are packed to 32 bits
//     and written to the SDRAM staging arena via the arbiter M2 write
//     channel, throttled with ioctl_wait (one posted word at a time).
//     On download end, hps_boot_len latches the byte count and
//     boot_rom_loaded asserts — the BRAM bootloader spins on that.
//
//  2. Disk-image sector engine: serves the existing data-slot register
//     handshake in axi_periph_slave (DS_* regs).  Firmware (the MiSTer
//     blockdev HAL) issues sector-aligned DS_CMD_READ/DS_CMD_WRITE on
//     slot 0 with DS_SLOT_OFFSET = byte offset into the mounted image,
//     DS_BRIDGE_ADDR = SDRAM byte offset, DS_LENGTH = 512-multiple.
//     Blocks move through a 512-byte sector buffer:
//       read:  sd_rd → sd_buff stream in → 8×16-beat AXI write bursts
//       write: 8×16-beat AXI read bursts → sd_wr → sd_buff stream out
//
//  3. Plumbing: img_mounted/size/readonly latch (HPS_STATUS block),
//     RTC passthrough, and MiSTer joystick → APF cont1/2 remap.
//
// Handshake contract with axi_periph_slave (matches core_bridge_cmd):
//   - target_dataslot_read/write are LEVEL signals, held until the periph
//     captures DONE.  The periph requires ack LOW→HIGH while cmd_active,
//     then done LOW→HIGH while ack is high.  READY (DS_STATUS bit 5) is
//     ~ack, so ack must drop after the strobe deasserts.
//   - openfile/getfile have no MiSTer backing; they complete immediately
//     with err=7 so a stray command can't wedge the dispatch guard.
//
// The W-channel drain uses an explicit fetch/consume micro-pipeline (a
// one-entry skid on top of the synchronous sector-buffer read) so a
// stalled WREADY can never skip past an unconsumed word.  ~2-3 cycles
// per beat — negligible against HPS sector latency.
//
`default_nettype none

module hps_bridge #(
    // SDRAM byte offset of the boot.rom staging region
    // (= OF_TARGET_CRAM0_BRIDGE + OF_TARGET_CRAM0_OS_OFFSET).
    parameter [31:0] BOOT_STAGE_ADDR = 32'h0330_0000,
    // Watchdog: abort a sector op if the HPS doesn't answer (~1.3 s).
    parameter [27:0] SD_TIMEOUT      = 28'd134_000_000
) (
    input  wire        clk,
    input  wire        reset_n,

    // ── hps_io: ioctl download (WIDE=1, 16-bit) ─────────────────────
    input  wire        ioctl_download,
    input  wire [15:0] ioctl_index,
    input  wire        ioctl_wr,
    input  wire [26:0] ioctl_addr,
    input  wire [15:0] ioctl_dout,
    output wire        ioctl_wait,

    // ── hps_io: disk image (S0) ─────────────────────────────────────
    input  wire        img_mounted,    // strobe: (re)mount/unmount event
    input  wire        img_readonly,
    input  wire [63:0] img_size,

    output wire [31:0] sd_lba,
    output reg         sd_rd,
    output reg         sd_wr,
    input  wire        sd_ack,
    input  wire [7:0]  sd_buff_addr,   // 256 × 16-bit within one sector
    input  wire [15:0] sd_buff_dout,
    output reg  [15:0] sd_buff_din,
    input  wire        sd_buff_wr,

    // ── hps_io: misc ────────────────────────────────────────────────
    input  wire [32:0] timestamp,      // unix time, [32] = update toggle
    input  wire [31:0] joystick_0,
    input  wire [31:0] joystick_1,
    input  wire [15:0] joystick_l_analog_0,
    input  wire [15:0] joystick_r_analog_0,
    input  wire [15:0] joystick_l_analog_1,
    input  wire [15:0] joystick_r_analog_1,

    // ── axi_periph_slave data-slot interface ────────────────────────
    input  wire        target_dataslot_read,
    input  wire        target_dataslot_write,
    input  wire        target_dataslot_openfile,
    input  wire        target_dataslot_getfile,
    input  wire [15:0] target_dataslot_id,
    input  wire [31:0] target_dataslot_slotoffset,
    input  wire [31:0] target_dataslot_bridgeaddr,
    input  wire [31:0] target_dataslot_length,
    output reg         target_dataslot_ack,
    output reg         target_dataslot_done,
    output reg  [2:0]  target_dataslot_err,
    output wire        bridge_wr_idle,

    // ── HPS status block (REGION_HPS read-back) ─────────────────────
    output wire [31:0] hps_status,
    output wire [63:0] hps_img_size,
    output wire [31:0] hps_boot_len,
    output wire        boot_rom_loaded,

    // ── APF-style controller registers ──────────────────────────────
    output wire [31:0] cont1_key,
    output wire [31:0] cont1_joy,
    output wire [15:0] cont1_trig,
    output wire [31:0] cont2_key,
    output wire [31:0] cont2_joy,
    output wire [15:0] cont2_trig,
    output wire [31:0] rtc_epoch_seconds,
    output wire        rtc_valid,

    // ── AXI master → arbiter M2 (read + write) ──────────────────────
    output reg         m_awvalid,
    input  wire        m_awready,
    output reg  [31:0] m_awaddr,
    output reg  [7:0]  m_awlen,
    output reg         m_wvalid,
    input  wire        m_wready,
    output reg  [31:0] m_wdata,
    output wire [3:0]  m_wstrb,
    output reg         m_wlast,
    input  wire        m_bvalid,
    input  wire [1:0]  m_bresp,

    output reg         m_arvalid,
    input  wire        m_arready,
    output reg  [31:0] m_araddr,
    output reg  [7:0]  m_arlen,
    input  wire        m_rvalid,
    input  wire [31:0] m_rdata,
    input  wire [1:0]  m_rresp,
    input  wire        m_rlast,
    output wire        m_rready
);

assign m_wstrb  = 4'hF;
assign m_rready = 1'b1;

// ====================================================================
// Mount / RTC / joystick plumbing
// ====================================================================

reg        mounted_r;
reg        readonly_r;
reg [63:0] img_size_r;

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        mounted_r  <= 1'b0;
        readonly_r <= 1'b0;
        img_size_r <= 64'd0;
    end else if (img_mounted) begin
        // hps_io pulses img_mounted on both mount and unmount; an unmount
        // reports size 0.
        mounted_r  <= (img_size != 64'd0);
        readonly_r <= img_readonly;
        img_size_r <= img_size;
    end
end

assign rtc_epoch_seconds = timestamp[31:0];
assign rtc_valid         = (timestamp[31:0] != 32'd0);

// MiSTer dpad is {up,down,left,right} = joy[3:0] with right at bit 0;
// the APF/OF layout wants up at bit 0.  Buttons 4+ line up 1:1 because
// the CONF_STR J1 order is A,B,X,Y,L,R,L2,R2,L3,R3,Select,Start.
// Type nibble [31:28] stays 0 — firmware infers analog from JOY != 0.
function [31:0] key_remap;
    input [31:0] joy;
    key_remap = {16'h0, joy[15:4], joy[0], joy[1], joy[2], joy[3]};
endfunction

// MiSTer analog axes are signed (-128..127); APF wants unsigned-centered.
function [31:0] joy_remap;
    input [15:0] l;     // {Y, X}
    input [15:0] r;
    joy_remap = {r[15:8] ^ 8'h80, r[7:0] ^ 8'h80,
                 l[15:8] ^ 8'h80, l[7:0] ^ 8'h80};
endfunction

assign cont1_key  = key_remap(joystick_0);
assign cont2_key  = key_remap(joystick_1);
assign cont1_joy  = joy_remap(joystick_l_analog_0, joystick_r_analog_0);
assign cont2_joy  = joy_remap(joystick_l_analog_1, joystick_r_analog_1);
assign cont1_trig = 16'h0;
assign cont2_trig = 16'h0;

// ====================================================================
// boot.rom ioctl ingest
// ====================================================================

reg        boot_active;
reg        boot_loaded_r;
reg [31:0] boot_len_r;
reg [31:0] boot_wr_addr;
reg [15:0] boot_lo_half;
reg        boot_have_lo;
reg        boot_word_pending;   // assembled word awaiting its AXI write
reg [31:0] boot_word;
reg        ioctl_download_d;

wire boot_index = (ioctl_index[5:0] == 6'd0);
wire boot_start = ioctl_download && !ioctl_download_d && boot_index;
wire boot_end   = !ioctl_download && ioctl_download_d && boot_active;

// Throttle the HPS while the posted word drains.
assign ioctl_wait = boot_word_pending;

assign boot_rom_loaded = boot_loaded_r;
assign hps_boot_len    = boot_len_r;

// ====================================================================
// Sector buffer — 512 B as two 128×16 simple-dual-port RAMs.
// One write source at a time (sd stream OR AXI R fill) muxed into a
// single write port, and one muxed read address (AXI W drain OR
// sd_buff_din readback), so both arrays infer as BRAM with clean
// full-width writes.
// ====================================================================

reg [15:0] secbuf_lo [0:127];
reg [15:0] secbuf_hi [0:127];

// FSM states
localparam S_IDLE       = 4'd0;
localparam S_RD_REQ     = 4'd1;   // assert sd_rd, wait ack rise
localparam S_RD_DATA    = 4'd2;   // sd_buff stream in, wait ack fall
localparam S_DRAIN_AW   = 4'd3;   // drain burst: address phase
localparam S_DRAIN_W    = 4'd4;   // drain burst: 16 W beats
localparam S_DRAIN_B    = 4'd5;   // drain burst: response
localparam S_WR_FILL    = 4'd6;   // 8×16-beat AXI read bursts from SDRAM
localparam S_WR_REQ     = 4'd7;   // assert sd_wr, wait ack rise
localparam S_WR_DATA    = 4'd8;   // HPS pulls sd_buff_din, wait ack fall
localparam S_NEXT       = 4'd9;   // advance block or finish
localparam S_DONE       = 4'd10;  // hold ack+done until strobe deasserts
localparam S_BOOT_AW    = 4'd11;  // boot word: address phase
localparam S_BOOT_W     = 4'd12;  // boot word: data phase
localparam S_BOOT_B     = 4'd13;  // boot word: response

reg [3:0]  state;
reg        op_is_write;
reg [31:0] op_lba_bytes;      // byte offset of current block in the image
reg [31:0] op_dma_addr;       // SDRAM byte offset of current block
reg [23:0] op_blocks_left;
reg [2:0]  burst_idx;         // 0..7 within the sector (16 words each)
reg [4:0]  beat_idx;          // 0..15 within a drain burst
reg [6:0]  buf_rd_idx;        // drain-side fetch index
reg [6:0]  buf_wr_idx;        // AXI R fill word index
reg        sd_ack_d;
reg [27:0] wd_count;          // watchdog

// Drain fetch/consume skid (see header).
reg        fetch_settled;     // BRAM read addr stable for one cycle
reg        next_valid;        // next_word holds buf word [buf_rd_idx]
reg [31:0] next_word;

wire sd_ack_rise = sd_ack && !sd_ack_d;
wire sd_ack_fall = !sd_ack && sd_ack_d;

assign sd_lba = {9'd0, op_lba_bytes[31:9]};

// Read-address mux: HPS readback while serving sd_wr, AXI drain otherwise.
wire [6:0] buf_rd_addr = (state == S_WR_REQ || state == S_WR_DATA)
                       ? sd_buff_addr[7:1] : buf_rd_idx;
reg [15:0] buf_rd_lo, buf_rd_hi;
reg        sd_half_d;

always @(posedge clk) begin
    // Single write port per array (one source active at a time).
    if (state == S_RD_DATA && sd_buff_wr) begin
        if (sd_buff_addr[0]) secbuf_hi[sd_buff_addr[7:1]] <= sd_buff_dout;
        else                 secbuf_lo[sd_buff_addr[7:1]] <= sd_buff_dout;
    end else if (state == S_WR_FILL && m_rvalid) begin
        secbuf_lo[buf_wr_idx] <= m_rdata[15:0];
        secbuf_hi[buf_wr_idx] <= m_rdata[31:16];
    end

    // Single (muxed) synchronous read port.
    buf_rd_lo <= secbuf_lo[buf_rd_addr];
    buf_rd_hi <= secbuf_hi[buf_rd_addr];
    sd_half_d <= sd_buff_addr[0];
end

// Registered sd_buff_din from the synchronous read (hps_io allows a
// cycle of slack between addr and din sampling — standard core pattern).
always @(posedge clk)
    sd_buff_din <= sd_half_d ? buf_rd_hi : buf_rd_lo;

// Strobe edge detect (the periph holds these level until DONE capture).
reg ds_read_d, ds_write_d, ds_open_d, ds_get_d;
wire ds_read_rise  = target_dataslot_read  && !ds_read_d;
wire ds_write_rise = target_dataslot_write && !ds_write_d;
wire ds_open_rise  = (target_dataslot_openfile && !ds_open_d) ||
                     (target_dataslot_getfile  && !ds_get_d);
wire ds_any_level  = target_dataslot_read | target_dataslot_write |
                     target_dataslot_openfile | target_dataslot_getfile;

// Request validation (slot 0, sector-aligned, in-bounds, mounted).
wire [31:0] req_off = target_dataslot_slotoffset;
wire [31:0] req_len = target_dataslot_length;
wire req_misaligned = (req_off[8:0] != 9'd0) || (req_len[8:0] != 9'd0) ||
                      (req_len == 32'd0);
wire req_oob        = ({32'd0, req_off} + {32'd0, req_len}) > img_size_r;
wire req_bad_slot   = (target_dataslot_id != 16'd0);

// err codes surfaced to DS_STATUS[4:2]
localparam [2:0] ERR_OK        = 3'd0;
localparam [2:0] ERR_NOMOUNT   = 3'd1;
localparam [2:0] ERR_BADREQ    = 3'd2;
localparam [2:0] ERR_READONLY  = 3'd3;
localparam [2:0] ERR_AXI       = 3'd4;
localparam [2:0] ERR_TIMEOUT   = 3'd5;
localparam [2:0] ERR_NOSUPPORT = 3'd7;

// Write path fully drained ⇔ no AXI write activity is possible.
assign bridge_wr_idle = (state != S_DRAIN_AW) && (state != S_DRAIN_W) &&
                        (state != S_DRAIN_B)  && (state != S_BOOT_AW) &&
                        (state != S_BOOT_W)   && (state != S_BOOT_B)  &&
                        !boot_word_pending;

assign hps_status   = {27'd0, readonly_r,
                       (target_dataslot_err != 3'd0),
                       (state != S_IDLE), mounted_r, boot_loaded_r};
assign hps_img_size = img_size_r;

// ====================================================================
// Main FSM
// ====================================================================

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= S_IDLE;
        sd_rd <= 1'b0;
        sd_wr <= 1'b0;
        sd_ack_d <= 1'b0;
        target_dataslot_ack  <= 1'b0;
        target_dataslot_done <= 1'b0;
        target_dataslot_err  <= 3'd0;
        ds_read_d <= 1'b0; ds_write_d <= 1'b0;
        ds_open_d <= 1'b0; ds_get_d <= 1'b0;
        m_awvalid <= 1'b0; m_awaddr <= 32'd0; m_awlen <= 8'd0;
        m_wvalid <= 1'b0; m_wdata <= 32'd0; m_wlast <= 1'b0;
        m_arvalid <= 1'b0; m_araddr <= 32'd0; m_arlen <= 8'd0;
        op_is_write <= 1'b0;
        op_lba_bytes <= 32'd0;
        op_dma_addr <= 32'd0;
        op_blocks_left <= 24'd0;
        burst_idx <= 3'd0;
        beat_idx <= 5'd0;
        buf_rd_idx <= 7'd0;
        buf_wr_idx <= 7'd0;
        wd_count <= 28'd0;
        fetch_settled <= 1'b0;
        next_valid <= 1'b0;
        next_word <= 32'd0;
        boot_active <= 1'b0;
        boot_loaded_r <= 1'b0;
        boot_len_r <= 32'd0;
        boot_wr_addr <= 32'd0;
        boot_lo_half <= 16'd0;
        boot_have_lo <= 1'b0;
        boot_word_pending <= 1'b0;
        boot_word <= 32'd0;
        ioctl_download_d <= 1'b0;
    end else begin
        sd_ack_d <= sd_ack;
        ds_read_d  <= target_dataslot_read;
        ds_write_d <= target_dataslot_write;
        ds_open_d  <= target_dataslot_openfile;
        ds_get_d   <= target_dataslot_getfile;
        ioctl_download_d <= ioctl_download;

        // ── boot.rom ingest (word assembly is independent of the FSM;
        //    the AXI write is sequenced through S_BOOT_* below) ───────
        if (boot_start) begin
            boot_active   <= 1'b1;
            boot_loaded_r <= 1'b0;
            boot_len_r    <= 32'd0;
            boot_wr_addr  <= BOOT_STAGE_ADDR;
            boot_have_lo  <= 1'b0;
        end

        if (boot_active && ioctl_wr && !boot_word_pending) begin
            boot_len_r <= boot_len_r + 32'd2;
            if (!boot_have_lo) begin
                boot_lo_half <= ioctl_dout;
                boot_have_lo <= 1'b1;
            end else begin
                boot_word <= {ioctl_dout, boot_lo_half};
                boot_word_pending <= 1'b1;
                boot_have_lo <= 1'b0;
            end
        end

        if (boot_end) begin
            if (boot_have_lo) begin
                // dangling 16 bits: pad and flush
                boot_word <= {16'd0, boot_lo_half};
                boot_word_pending <= 1'b1;
                boot_have_lo <= 1'b0;
            end
            boot_active <= 1'b0;
        end

        // Mark complete once the final word has fully landed.
        if (!boot_active && !boot_word_pending && !boot_loaded_r &&
            boot_len_r != 32'd0 &&
            state != S_BOOT_AW && state != S_BOOT_W && state != S_BOOT_B)
            boot_loaded_r <= 1'b1;

        case (state)
        // ──────────────────────────────────────────────────────────
        S_IDLE: begin
            target_dataslot_done <= 1'b0;
            wd_count <= 28'd0;

            if (boot_word_pending) begin
                m_awvalid <= 1'b1;
                m_awaddr  <= boot_wr_addr;
                m_awlen   <= 8'd0;
                state     <= S_BOOT_AW;
            end else if (ds_read_rise || ds_write_rise) begin
                target_dataslot_ack <= 1'b1;
                op_is_write    <= ds_write_rise && !ds_read_rise;
                op_lba_bytes   <= req_off;
                op_dma_addr    <= target_dataslot_bridgeaddr;
                op_blocks_left <= req_len[31:9] == 23'd0 ? 24'd1
                                                         : {1'b0, req_len[31:9]};
                if (!mounted_r) begin
                    target_dataslot_err <= ERR_NOMOUNT;
                    target_dataslot_done <= 1'b1;
                    state <= S_DONE;
                end else if (req_bad_slot || req_misaligned || req_oob) begin
                    target_dataslot_err <= ERR_BADREQ;
                    target_dataslot_done <= 1'b1;
                    state <= S_DONE;
                end else if (ds_write_rise && !ds_read_rise && readonly_r) begin
                    target_dataslot_err <= ERR_READONLY;
                    target_dataslot_done <= 1'b1;
                    state <= S_DONE;
                end else begin
                    target_dataslot_err <= ERR_OK;
                    if (ds_write_rise && !ds_read_rise) begin
                        buf_wr_idx <= 7'd0;
                        burst_idx  <= 3'd0;
                        m_arvalid <= 1'b1;
                        m_araddr  <= target_dataslot_bridgeaddr;
                        m_arlen   <= 8'd15;
                        state <= S_WR_FILL;
                    end else begin
                        sd_rd <= 1'b1;
                        state <= S_RD_REQ;
                    end
                end
            end else if (ds_open_rise) begin
                // OPENFILE/GETFILE have no MiSTer backing; fail fast so
                // the periph dispatch guard never wedges.
                target_dataslot_ack  <= 1'b1;
                target_dataslot_err  <= ERR_NOSUPPORT;
                target_dataslot_done <= 1'b1;
                state <= S_DONE;
            end
        end

        // ── boot word single-beat write ────────────────────────────
        S_BOOT_AW: begin
            if (m_awvalid && m_awready) begin
                m_awvalid <= 1'b0;
                m_wvalid  <= 1'b1;
                m_wdata   <= boot_word;
                m_wlast   <= 1'b1;
                state     <= S_BOOT_W;
            end
        end
        S_BOOT_W: begin
            if (m_wvalid && m_wready) begin
                m_wvalid <= 1'b0;
                m_wlast  <= 1'b0;
                state    <= S_BOOT_B;
            end
        end
        S_BOOT_B: begin
            if (m_bvalid) begin
                boot_wr_addr <= boot_wr_addr + 32'd4;
                boot_word_pending <= 1'b0;
                state <= S_IDLE;
            end
        end

        // ── disk → SDRAM (DS_CMD_READ) ─────────────────────────────
        S_RD_REQ: begin
            wd_count <= wd_count + 28'd1;
            if (sd_ack_rise) begin
                sd_rd <= 1'b0;
                wd_count <= 28'd0;
                state <= S_RD_DATA;
            end else if (wd_count == SD_TIMEOUT) begin
                sd_rd <= 1'b0;
                target_dataslot_err <= ERR_TIMEOUT;
                target_dataslot_done <= 1'b1;
                state <= S_DONE;
            end
        end
        S_RD_DATA: begin
            wd_count <= wd_count + 28'd1;
            if (sd_ack_fall) begin
                // Sector buffered — drain to SDRAM in 8×16-beat bursts.
                burst_idx <= 3'd0;
                buf_rd_idx <= 7'd0;
                fetch_settled <= 1'b0;
                next_valid <= 1'b0;
                m_awvalid <= 1'b1;
                m_awaddr  <= op_dma_addr;
                m_awlen   <= 8'd15;
                state <= S_DRAIN_AW;
            end else if (wd_count == SD_TIMEOUT) begin
                target_dataslot_err <= ERR_TIMEOUT;
                target_dataslot_done <= 1'b1;
                state <= S_DONE;
            end
        end

        // ── sector-buffer → SDRAM drain, one 16-beat burst ─────────
        S_DRAIN_AW: begin
            // Fetch can begin while AW waits.
            if (!next_valid) begin
                if (fetch_settled) begin
                    next_word <= {buf_rd_hi, buf_rd_lo};
                    next_valid <= 1'b1;
                    fetch_settled <= 1'b0;
                    buf_rd_idx <= buf_rd_idx + 7'd1;
                end else
                    fetch_settled <= 1'b1;
            end
            if (m_awvalid && m_awready) begin
                m_awvalid <= 1'b0;
                beat_idx <= 5'd0;
                state <= S_DRAIN_W;
            end
        end
        S_DRAIN_W: begin
            // fetch stage: buf_rd_addr == buf_rd_idx is stable while
            // !next_valid; one settle cycle then capture.
            if (!next_valid && !(m_wvalid && !m_wready)) begin
                if (fetch_settled) begin
                    next_word <= {buf_rd_hi, buf_rd_lo};
                    next_valid <= 1'b1;
                    fetch_settled <= 1'b0;
                    buf_rd_idx <= buf_rd_idx + 7'd1;
                end else
                    fetch_settled <= 1'b1;
            end

            // consume stage
            if (m_wvalid && m_wready) begin
                m_wvalid <= 1'b0;
                if (m_wlast) begin
                    m_wlast <= 1'b0;
                    state <= S_DRAIN_B;
                end else
                    beat_idx <= beat_idx + 5'd1;
            end else if (!m_wvalid && next_valid) begin
                m_wvalid <= 1'b1;
                m_wdata  <= next_word;
                m_wlast  <= (beat_idx == 5'd15);
                next_valid <= 1'b0;
            end
        end
        S_DRAIN_B: begin
            if (m_bvalid) begin
                if (m_bresp != 2'b00) begin
                    target_dataslot_err <= ERR_AXI;
                    target_dataslot_done <= 1'b1;
                    state <= S_DONE;
                end else if (burst_idx == 3'd7) begin
                    state <= S_NEXT;
                end else begin
                    burst_idx <= burst_idx + 3'd1;
                    buf_rd_idx <= {burst_idx + 3'd1, 4'd0};
                    fetch_settled <= 1'b0;
                    next_valid <= 1'b0;
                    m_awvalid <= 1'b1;
                    m_awaddr  <= op_dma_addr + ({23'd0, burst_idx + 3'd1, 6'd0});
                    m_awlen   <= 8'd15;
                    state <= S_DRAIN_AW;
                end
            end
        end

        // ── SDRAM → disk (DS_CMD_WRITE) ────────────────────────────
        S_WR_FILL: begin
            if (m_arvalid && m_arready)
                m_arvalid <= 1'b0;

            if (m_rvalid) begin
                buf_wr_idx <= buf_wr_idx + 7'd1;
                if (m_rresp != 2'b00)
                    target_dataslot_err <= ERR_AXI;
                if (m_rlast) begin
                    if (target_dataslot_err != 3'd0) begin
                        target_dataslot_done <= 1'b1;
                        state <= S_DONE;
                    end else if (burst_idx == 3'd7) begin
                        sd_wr <= 1'b1;
                        wd_count <= 28'd0;
                        state <= S_WR_REQ;
                    end else begin
                        burst_idx <= burst_idx + 3'd1;
                        m_arvalid <= 1'b1;
                        m_araddr  <= op_dma_addr + ({23'd0, burst_idx + 3'd1, 6'd0});
                        m_arlen   <= 8'd15;
                    end
                end
            end
        end
        S_WR_REQ: begin
            wd_count <= wd_count + 28'd1;
            if (sd_ack_rise) begin
                sd_wr <= 1'b0;
                wd_count <= 28'd0;
                state <= S_WR_DATA;
            end else if (wd_count == SD_TIMEOUT) begin
                sd_wr <= 1'b0;
                target_dataslot_err <= ERR_TIMEOUT;
                target_dataslot_done <= 1'b1;
                state <= S_DONE;
            end
        end
        S_WR_DATA: begin
            wd_count <= wd_count + 28'd1;
            if (sd_ack_fall) begin
                state <= S_NEXT;
            end else if (wd_count == SD_TIMEOUT) begin
                target_dataslot_err <= ERR_TIMEOUT;
                target_dataslot_done <= 1'b1;
                state <= S_DONE;
            end
        end

        // ── advance block / finish ─────────────────────────────────
        S_NEXT: begin
            if (op_blocks_left <= 24'd1) begin
                target_dataslot_done <= 1'b1;
                state <= S_DONE;
            end else begin
                op_blocks_left <= op_blocks_left - 24'd1;
                op_lba_bytes   <= op_lba_bytes + 32'd512;
                op_dma_addr    <= op_dma_addr + 32'd512;
                wd_count <= 28'd0;
                if (op_is_write) begin
                    buf_wr_idx <= 7'd0;
                    burst_idx  <= 3'd0;
                    m_arvalid <= 1'b1;
                    m_araddr  <= op_dma_addr + 32'd512;
                    m_arlen   <= 8'd15;
                    state <= S_WR_FILL;
                end else begin
                    sd_rd <= 1'b1;
                    state <= S_RD_REQ;
                end
            end
        end

        // ── completion handshake ───────────────────────────────────
        S_DONE: begin
            // Hold ack+done until the periph captures DONE and drops the
            // level strobe, then release so DS_STATUS.READY (=~ack)
            // returns and the next command can dispatch.
            if (!ds_any_level) begin
                target_dataslot_ack  <= 1'b0;
                target_dataslot_done <= 1'b0;
                state <= S_IDLE;
            end
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
