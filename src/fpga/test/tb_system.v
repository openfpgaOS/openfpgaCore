//
// Verilator System Testbench: VexiiRiscv CPU + Memory Stack + Bridge Save Path
//
// Full CPU system running real firmware in simulation:
//   VexiiRiscv (Wishbone) → cpu_system → { BRAM, SDRAM, PSRAM (CRAM1) }
//   Bridge responder simulates APF save path: reads CRAM1, writes SDRAM via FIFO
//
// Result protocol (BRAM backdoor):
//   BRAM[0x3FF00] = test status (0 = running, 1 = pass, 2 = fail)
//   BRAM[0x3FF04] = test result value
//

`default_nettype none

module tb_system (
    input wire clk,
    input wire reset_n,

    // Backdoor BRAM access (firmware loading + result checking)
    input  wire        bd_we,
    input  wire [15:0] bd_addr,
    input  wire [31:0] bd_wdata,
    output wire [31:0] bd_rdata,

    // UART output
    output wire        uart_tx_valid,
    output wire [7:0]  uart_tx_byte,

    // SDRAM backdoor (for OS binary preload)
    input  wire        sd_bd_we,
    input  wire [23:0] sd_bd_addr,
    input  wire [31:0] sd_bd_wdata,

    // CRAM1 backdoor (for save data preload/verify)
    input  wire        cram_bd_we,
    input  wire [21:0] cram_bd_addr,
    input  wire [31:0] cram_bd_wdata,

    // Save capture verification (from bridge responder)
    output wire        save_complete,
    output wire [31:0] save_captured_words,
    input  wire [15:0] save_bd_addr,
    output wire [31:0] save_bd_rdata,

    // Debug
    output wire [31:0] dbg_fetch_addr,
    output wire        dbg_fetch_valid,
    output wire [31:0] dbg_lsu_addr,
    output wire        dbg_lsu_valid
);

// ============================================================
// CPU system (Wishbone + word-level bus routing)
// ============================================================

// SDRAM word-level bus
wire        cpu_sdram_rd, cpu_sdram_wr;
wire [23:0] cpu_sdram_addr;
wire [31:0] cpu_sdram_wdata;
wire [3:0]  cpu_sdram_wstrb;
wire [3:0]  cpu_sdram_burst_len, cpu_sdram_burst_wr_len;
wire [31:0] cpu_sdram_rdata;
wire        cpu_sdram_busy, cpu_sdram_accepted;
wire        cpu_sdram_rdata_valid, cpu_sdram_wr_data_next;

// PSRAM word-level bus
wire        cpu_psram_rd, cpu_psram_wr;
wire [25:0] cpu_psram_addr;
wire [31:0] cpu_psram_wdata;
wire [3:0]  cpu_psram_wstrb;
wire [31:0] cpu_psram_rdata;
wire        cpu_psram_busy, cpu_psram_rdata_valid;
wire        cpu_psram_burst_rd;
wire [5:0]  cpu_psram_burst_len;
wire        cpu_psram_burst_rdata_valid;
wire [31:0] cpu_psram_burst_rdata;

// Local word-level bus
wire        cpu_local_rd, cpu_local_wr;
wire [31:0] cpu_local_addr;
wire [31:0] cpu_local_wdata;
wire [3:0]  cpu_local_wstrb;
wire [7:0]  cpu_local_burst_len;
wire [31:0] cpu_local_rdata;
wire        cpu_local_rdata_valid, cpu_local_rdata_last;
wire        cpu_local_wr_done, cpu_local_busy;

cpu_system cpu (
    .clk(clk), .reset_n(reset_n),
    // SDRAM
    .m_sdram_rd(cpu_sdram_rd), .m_sdram_wr(cpu_sdram_wr),
    .m_sdram_addr(cpu_sdram_addr), .m_sdram_wdata(cpu_sdram_wdata),
    .m_sdram_wstrb(cpu_sdram_wstrb),
    .m_sdram_burst_len(cpu_sdram_burst_len),
    .m_sdram_burst_wr_len(cpu_sdram_burst_wr_len),
    .m_sdram_rdata(cpu_sdram_rdata), .m_sdram_busy(cpu_sdram_busy),
    .m_sdram_accepted(cpu_sdram_accepted),
    .m_sdram_rdata_valid(cpu_sdram_rdata_valid),
    .m_sdram_wr_data_next(cpu_sdram_wr_data_next),
    // PSRAM
    .m_psram_rd(cpu_psram_rd), .m_psram_wr(cpu_psram_wr),
    .m_psram_addr(cpu_psram_addr), .m_psram_wdata(cpu_psram_wdata),
    .m_psram_wstrb(cpu_psram_wstrb),
    .m_psram_rdata(cpu_psram_rdata), .m_psram_busy(cpu_psram_busy),
    .m_psram_rdata_valid(cpu_psram_rdata_valid),
    .m_psram_burst_rd(cpu_psram_burst_rd),
    .m_psram_burst_len(cpu_psram_burst_len),
    .m_psram_burst_rdata_valid(cpu_psram_burst_rdata_valid),
    .m_psram_burst_rdata(cpu_psram_burst_rdata),
    // Local
    .m_local_rd(cpu_local_rd), .m_local_wr(cpu_local_wr),
    .m_local_addr(cpu_local_addr), .m_local_wdata(cpu_local_wdata),
    .m_local_wstrb(cpu_local_wstrb),
    .m_local_burst_len(cpu_local_burst_len),
    .m_local_rdata(cpu_local_rdata),
    .m_local_rdata_valid(cpu_local_rdata_valid),
    .m_local_rdata_last(cpu_local_rdata_last),
    .m_local_wr_done(cpu_local_wr_done),
    .m_local_busy(cpu_local_busy)
);

// ============================================================
// BRAM + Peripheral model (word-level)
// ============================================================

wire        ds_read, ds_write;
wire [15:0] ds_slot_id;
wire [31:0] ds_bridge_addr, ds_length;
wire        ds_ack, ds_done;
wire [3:0]  save_dt_slot_w;
wire [31:0] save_dt_size_w;
wire        save_dt_commit_w;

bram_word_model bram (
    .clk(clk), .reset_n(reset_n),
    .req_rd(cpu_local_rd), .req_wr(cpu_local_wr),
    .req_addr_in(cpu_local_addr), .req_wdata_in(cpu_local_wdata),
    .req_wstrb_in(cpu_local_wstrb), .req_burst_len_in(cpu_local_burst_len),
    .rsp_rdata(cpu_local_rdata), .rsp_rdata_valid(cpu_local_rdata_valid),
    .rsp_rdata_last(cpu_local_rdata_last),
    .rsp_wr_done(cpu_local_wr_done), .rsp_busy(cpu_local_busy),
    .bd_we(bd_we), .bd_addr(bd_addr), .bd_wdata(bd_wdata), .bd_rdata(bd_rdata),
    .uart_tx_valid(uart_tx_valid), .uart_tx_byte(uart_tx_byte),
    .target_dataslot_read(ds_read), .target_dataslot_write(ds_write),
    .target_dataslot_id(ds_slot_id),
    .target_dataslot_bridgeaddr(ds_bridge_addr),
    .target_dataslot_length(ds_length),
    .target_dataslot_ack(ds_ack), .target_dataslot_done(ds_done),
    .save_dt_slot(save_dt_slot_w), .save_dt_size(save_dt_size_w),
    .save_dt_commit(save_dt_commit_w)
);

// ============================================================
// SDRAM: word_sdram_arbiter → sdram_word_model
// ============================================================

// Arbiter → SDRAM model
wire        arb_sdram_rd, arb_sdram_wr;
wire [23:0] arb_sdram_addr;
wire [31:0] arb_sdram_wdata;
wire [3:0]  arb_sdram_wstrb;
wire [3:0]  arb_sdram_burst_len, arb_sdram_burst_wr_len;
wire [31:0] sdram_word_q;
wire        sdram_word_busy, sdram_word_q_valid, sdram_word_wr_data_next;
wire [31:0] arb_sdram_wdata_direct;
wire [3:0]  arb_sdram_wstrb_direct;

word_sdram_arbiter sdram_arb (
    .clk(clk), .reset_n(reset_n),
    // M0: Audio DMA (tied off)
    .m0_rd(1'b0), .m0_addr(24'b0), .m0_burst_len(4'b0),
    .m0_rdata(), .m0_busy(), .m0_accepted(), .m0_rdata_valid(),
    // M1: DMA (tied off)
    .m1_rd(1'b0), .m1_wr(1'b0), .m1_addr(24'b0), .m1_wdata(32'b0),
    .m1_wstrb(4'b0), .m1_burst_len(4'b0), .m1_burst_wr_len(4'b0),
    .m1_rdata(), .m1_busy(), .m1_accepted(), .m1_rdata_valid(), .m1_wr_data_next(),
    // M2: CPU
    .m2_rd(cpu_sdram_rd), .m2_wr(cpu_sdram_wr),
    .m2_addr(cpu_sdram_addr), .m2_wdata(cpu_sdram_wdata),
    .m2_wstrb(cpu_sdram_wstrb),
    .m2_burst_len(cpu_sdram_burst_len), .m2_burst_wr_len(cpu_sdram_burst_wr_len),
    .m2_rdata(cpu_sdram_rdata), .m2_busy(cpu_sdram_busy),
    .m2_accepted(cpu_sdram_accepted), .m2_rdata_valid(cpu_sdram_rdata_valid),
    .m2_wr_data_next(cpu_sdram_wr_data_next),
    // M3: Bridge (tied off — saves go direct CRAM→SD, not through SDRAM)
    .m3_rd(1'b0), .m3_wr(1'b0),
    .m3_addr(24'b0), .m3_wdata(32'b0),
    .m3_wstrb(4'b0),
    .m3_burst_len(4'b0), .m3_burst_wr_len(4'b0),
    .m3_rdata(), .m3_busy(),
    .m3_accepted(), .m3_rdata_valid(),
    .m3_wr_data_next(),
    // SDRAM output
    .sdram_rd(arb_sdram_rd), .sdram_wr(arb_sdram_wr),
    .sdram_addr(arb_sdram_addr), .sdram_wdata(arb_sdram_wdata),
    .sdram_wstrb(arb_sdram_wstrb),
    .sdram_burst_len(arb_sdram_burst_len),
    .sdram_burst_wr_len(arb_sdram_burst_wr_len),
    .sdram_rdata(sdram_word_q), .sdram_busy(sdram_word_busy),
    .sdram_rdata_valid(sdram_word_q_valid),
    .sdram_wr_data_next(sdram_word_wr_data_next),
    .sdram_wdata_direct(arb_sdram_wdata_direct),
    .sdram_wstrb_direct(arb_sdram_wstrb_direct)
);

sdram_word_model sdram (
    .clk(clk), .reset_n(reset_n),
    .word_rd(arb_sdram_rd), .word_wr(arb_sdram_wr),
    .word_addr(arb_sdram_addr), .word_data(arb_sdram_wdata),
    .word_wstrb(arb_sdram_wstrb),
    .word_burst_len(arb_sdram_burst_len),
    .word_burst_wr_len(arb_sdram_burst_wr_len),
    .word_q(sdram_word_q), .word_busy(sdram_word_busy),
    .word_q_valid(sdram_word_q_valid),
    .word_wr_data_next(sdram_word_wr_data_next),
    .burst_wr_direct_data(arb_sdram_wdata_direct),
    .burst_wr_direct_strb(arb_sdram_wstrb_direct),
    .bd_we(sd_bd_we), .bd_word_addr(sd_bd_addr), .bd_wdata(sd_bd_wdata)
);

// ============================================================
// PSRAM (CRAM1) model
// ============================================================

// Bridge direct read port (for bridge responder)
wire        bridge_cram_rd;
wire [21:0] bridge_cram_addr;
wire [31:0] bridge_cram_rdata;
wire        bridge_cram_rdata_valid;

psram_word_model psram (
    .clk(clk), .reset_n(reset_n),
    .word_rd(cpu_psram_rd), .word_wr(cpu_psram_wr),
    .word_addr(cpu_psram_addr), .word_wdata(cpu_psram_wdata),
    .word_wstrb(cpu_psram_wstrb),
    .word_rdata(cpu_psram_rdata), .word_busy(cpu_psram_busy),
    .word_rdata_valid(cpu_psram_rdata_valid),
    .burst_rd(cpu_psram_burst_rd), .burst_len(cpu_psram_burst_len),
    .burst_rdata_valid(cpu_psram_burst_rdata_valid),
    .burst_rdata(cpu_psram_burst_rdata),
    .direct_rd(bridge_cram_rd), .direct_addr(bridge_cram_addr),
    .direct_rdata(bridge_cram_rdata), .direct_rdata_valid(bridge_cram_rdata_valid),
    .bd_we(cram_bd_we), .bd_addr(cram_bd_addr), .bd_wdata(cram_bd_wdata)
);

// ============================================================
// Bridge responder (save path: reads CRAM1, captures to verify buffer)
// ============================================================

bridge_responder bridge_resp (
    .clk(clk), .reset_n(reset_n),
    .target_dataslot_write(ds_write),
    .target_dataslot_read(ds_read),
    .target_dataslot_id(ds_slot_id),
    .target_dataslot_bridgeaddr(ds_bridge_addr),
    .target_dataslot_length(ds_length),
    .target_dataslot_ack(ds_ack),
    .target_dataslot_done(ds_done),
    .cram_rd(bridge_cram_rd), .cram_addr(bridge_cram_addr),
    .cram_rdata(bridge_cram_rdata), .cram_rdata_valid(bridge_cram_rdata_valid),
    .save_complete(save_complete),
    .save_captured_words(save_captured_words),
    .save_slot_id(),
    .save_bd_addr(save_bd_addr),
    .save_bd_rdata(save_bd_rdata)
);

// ============================================================
// Debug: expose Wishbone fetch address for PC tracing
// ============================================================
assign dbg_fetch_addr = {cpu.fetch_adr, 2'b00};
assign dbg_fetch_valid = cpu.fetch_cyc & cpu.fetch_stb;
assign dbg_lsu_addr = {cpu.lsu_adr, 2'b00};
assign dbg_lsu_valid = cpu.lsu_cyc & cpu.lsu_stb;

endmodule
