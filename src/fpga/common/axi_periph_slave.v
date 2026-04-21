//
// AXI4 Peripheral Slave (openfpgaOS)
// Handles all local/peripheral accesses from the CPU:
//   - BRAM (32KB, burst reads for I-cache line fills)
//   - System registers (cycle counter, display, palette, dataslot, controllers)
//   - Terminal forwarding
//   - Audio / Link register dispatch
//
// AXI4 slave (NOT AXI4-Lite) — iBus issues burst reads to BRAM for I-cache fills.
//

`default_nettype none

`include "gpu_features.vh"

module axi_periph_slave (
    input wire clk,
    input wire reset_n,

    // AXI4 slave interface (from cpu_system m_local)
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,
    input  wire [31:0] s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,

    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rlast,

    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,
    input  wire [31:0] s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,

    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,

    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]  s_axi_bresp,

    // CDC inputs
    input wire         dataslot_allcomplete,
    input wire         vsync,
    input wire [31:0]  cont1_key,
    input wire [31:0]  cont1_joy,
    input wire [15:0]  cont1_trig,
    input wire [31:0]  cont2_key,
    input wire [31:0]  cont2_joy,
    input wire [15:0]  cont2_trig,
    input wire         target_dataslot_ack,
    input wire         target_dataslot_done,
    input wire [2:0]   target_dataslot_err,

    // Bridge write drain status (for pacing DMA reads)
    input wire         bridge_wr_idle,

    // Display control outputs
    output wire [2:0]  color_mode,      // 0=8bit, 1=4bit, 2=2bit, 3=RGB565, 4=RGB555, 5=RGBA5551
    output wire [24:0] fb_display_addr,

    // Palette write interface
    output reg         pal_wr,
    output reg  [7:0]  pal_addr,
    output reg  [23:0] pal_data,

    // Target dataslot interface
    output reg         target_dataslot_read,
    output reg         target_dataslot_write,
    output reg         target_dataslot_openfile,
    output reg         target_dataslot_getfile,
    output reg  [15:0] target_dataslot_id,
    output reg  [31:0] target_dataslot_slotoffset,
    output reg  [31:0] target_dataslot_bridgeaddr,
    output reg  [31:0] target_dataslot_length,
    output reg  [31:0] target_buffer_param_struct,
    output reg  [31:0] target_buffer_resp_struct,

    // Audio output interface — writes go straight to the audio_output dcfifo.
    // CPU polls AUDIO_FIFO_LEVEL (read of AUDIO_BASE+0x00) to check for room
    // before writing the next stereo sample pair (32 bits {L[15:0], R[15:0]}).
    output reg         audio_sample_wr,
    output reg  [31:0] audio_sample_data,
    input wire  [9:0]  audio_fifo_level,
    input wire         audio_fifo_full,

    // Audio DMA control (SDRAM ring → audio_output).  Firmware programs
    // base + length + enable; HW stream consumes the ring continuously.
    output reg  [31:0] audio_dma_base,       // SDRAM byte address of ring[0]
    output reg  [13:0] audio_dma_len,        // ring length in stereo pairs (= words)
    output reg         audio_dma_enable,
    input  wire [13:0] audio_dma_read_ptr,   // DMA's current position (readback)

    // CRAM1 burst prefetch MMIO (0x4E000000 region) — SW mixer uses this
    // to amortise sample fetches via 8-word bursts through cram1_controller.
    output reg         cram1_burst_addr_wr,      // 1-cycle: WR 0x4E000000
    output reg  [21:0] cram1_burst_addr_wdata,   // 22-bit CRAM1 word address
    output reg         cram1_burst_data_rd,      // 1-cycle: RD 0x4E000008
    input  wire        cram1_burst_busy,         // 1 while burst in flight
    input  wire [31:0] cram1_burst_data_q,       // current FIFO head

    // Link MMIO interface
    output reg         link_reg_wr,
    output reg         link_reg_rd,
    output reg  [4:0]  link_reg_addr,
    output reg  [31:0] link_reg_wdata,
    input wire  [31:0] link_reg_rdata,

    // Save datatable update interface (CPU → datatable via core_top CDC)
    output reg  [3:0]  save_dt_slot,    // save slot index (0-9)
    output reg  [31:0] save_dt_size,    // size to write
    output reg         save_dt_commit,  // pulse: write save_dt_size to datatable

    // UART interface
    output reg         uart_tx_dv,      // TX data valid (1 cycle pulse)
    output reg  [7:0]  uart_tx_byte,    // TX data byte
    input wire         uart_tx_active,  // TX busy
    input wire         uart_rx_dv,      // RX data valid (1 cycle pulse)
    input wire  [7:0]  uart_rx_byte,    // RX data byte

    // App ID from instance JSON memory_writes
    input wire  [31:0] app_id,

    // Shutdown handshake
    input wire         shutdown_pending,
    output reg         shutdown_ack,

    // Hardware timer interrupt
    output wire        timer_irq,
    output wire        uart_rx_irq,

    // External IRQ masking
    input  wire        link_irq,
    output wire        ext_irq,

    // Datatable slot size query (toggle-based CDC to clk_74a)
    output reg  [9:0]  dt_query_addr,
    output reg         dt_query_toggle,   // toggles on each new query
    input  wire [31:0] dt_query_data,
    input  wire        dt_query_valid,

    // VRR (Variable Refresh Rate) — CPU-writable V_TOTAL for video timing
    output reg  [9:0]  vrr_v_total,

    // SNAC shifter / GPIO interface to cart pins
    // Pin mapping: [0]=OUT1/bank1[6], [1]=OUT2/bank1[7],
    //   [2]=IO3/bank0[4], [3]=IN7/bank0[5], [4]=bank0[6],
    //   [5]=IN4/bank0[7], [6]=IO5/pin30, [7]=IO6/pin31
    output wire [7:0]  snac_pin_out,      // output values to cart pins
    output wire [7:0]  snac_pin_dir,      // direction: 1=output, 0=input
    input  wire [7:0]  snac_pin_in,       // input values from cart pins
    output wire        snac_enable,        // 1=SNAC mode (cart pins from CPU), 0=UART mode

    // GPU register interface
    output reg         gpu_reg_wr,
    output reg  [3:0]  gpu_reg_addr,
    output reg  [31:0] gpu_reg_wdata,
    input  wire [31:0] gpu_reg_rdata
);

wire reset = ~reset_n;

// ============================================
// Address decode (combinatorial, on AXI address channels)
// ============================================
wire [31:0] ar_addr = s_axi_araddr;
wire [31:0] aw_addr = s_axi_awaddr;

// ============================================
// SNAC Shifter + GPIO (registers 0xA0-0xAC)
// ============================================
// SNAC_CTRL (0xA0): [0]=start, [5:1]=bit_count-1, [6]=latch_en, [7]=snac_en, [9:8]=mode
// SNAC_DIV  (0xA4): [15:0]=clock divider half-period
// SNAC_DATA (0xA8): write=TX data, read=RX data
// SNAC_GPIO (0xAC): write=[7:0]=pin out, [15:8]=pin dir; read=[7:0]=pin in
reg        snac_en_reg;
reg [1:0]  snac_mode_reg;
reg [15:0] snac_div_reg;
reg [31:0] snac_tx_reg;
reg [7:0]  snac_gpio_out_reg;
reg [7:0]  snac_gpio_dir_reg;   // 0=input, 1=output; bits [1:0] always output
reg        snac_start_pulse;

wire        snac_busy;
wire        snac_done;
wire [31:0] snac_rx_data;
wire        snac_shift_clk;
wire        snac_shift_mosi;
wire        snac_shift_latch;

// Shifter MISO inputs — directly from synced pin inputs
wire snac_miso_a = snac_pin_in[2];  // IO3/bank0[4] (Config A: DATA)
wire snac_miso_b = snac_pin_in[5];  // IN4/bank0[7] (Config B: DAT)

// Shift parameters latched from SNAC_CTRL write
reg [4:0] snac_bit_count_reg;
reg       snac_latch_en_reg;

snac_shifter snac_shift (
    .clk(clk),
    .reset_n(reset_n),
    .start(snac_start_pulse),
    .bit_count(snac_bit_count_reg),
    .latch_en(snac_latch_en_reg),
    .mode(snac_mode_reg),
    .clk_div(snac_div_reg),
    .tx_data(snac_tx_reg),
    .rx_data(snac_rx_data),
    .busy(snac_busy),
    .done(snac_done),
    .shift_clk(snac_shift_clk),
    .shift_mosi(snac_shift_mosi),
    .shift_latch(snac_shift_latch),
    .miso_a(snac_miso_a),
    .miso_b(snac_miso_b)
);

// SNAC pin output mux: shifter overrides GPIO when busy
// Config A: shifter drives [0]=CLK, [1]=LATCH
// Config B: shifter drives [2]=CLK, [7]=CMD(MOSI)
reg [7:0] snac_pin_out_mux;
reg [7:0] snac_pin_dir_mux;

always @(*) begin
    snac_pin_out_mux = snac_gpio_out_reg;
    snac_pin_dir_mux = snac_gpio_dir_reg | 8'b00000011; // bits [1:0] always output
    if (snac_busy) begin
        if (snac_mode_reg == 2'b00) begin
            // Config A: CLK on [0], LATCH on [1]
            snac_pin_out_mux[0] = snac_shift_clk;
            snac_pin_out_mux[1] = snac_shift_latch;
            snac_pin_dir_mux[0] = 1'b1;
            snac_pin_dir_mux[1] = 1'b1;
        end else begin
            // Config B: CLK on [2](IO3/bank0[4]), MOSI on [7](IO6/pin31)
            snac_pin_out_mux[2] = snac_shift_clk;
            snac_pin_out_mux[7] = snac_shift_mosi;
            snac_pin_dir_mux[2] = 1'b1;
            snac_pin_dir_mux[7] = 1'b1;
        end
    end
end

assign snac_pin_out = snac_pin_out_mux;
assign snac_pin_dir = snac_pin_dir_mux;
assign snac_enable  = snac_en_reg;

// ============================================
// BRAM (32KB = 8192 x 32-bit words)
// ============================================
wire [31:0] ram_rdata;
reg  [12:0] ram_addr_mux;
wire ram_wren;

altsyncram #(
    .operation_mode("SINGLE_PORT"),
    .width_a(32),
    .widthad_a(13),
    .numwords_a(8192),
    .width_byteena_a(4),
    .lpm_type("altsyncram"),
    .outdata_reg_a("UNREGISTERED"),
    .init_file("firmware.mif"),
    .intended_device_family("Cyclone V"),
    .read_during_write_mode_port_a("NEW_DATA_NO_NBE_READ")
) ram (
    .clock0(clk),
    .address_a(ram_addr_mux),
    .data_a(req_wdata),
    .wren_a(ram_wren),
    .byteena_a(req_wstrb),
    .q_a(ram_rdata),
    .aclr0(1'b0),
    .aclr1(1'b0),
    .address_b(1'b0),
    .addressstall_a(1'b0),
    .addressstall_b(1'b0),
    .byteena_b(1'b1),
    .clock1(1'b1),
    .clocken0(1'b1),
    .clocken1(1'b1),
    .clocken2(1'b1),
    .clocken3(1'b1),
    .data_b({32{1'b0}}),
    .eccstatus(),
    .q_b(),
    .rden_a(1'b1),
    .rden_b(1'b0),
    .wren_b(1'b0)
);

// ============================================
// Terminal forwarding
// ============================================
// Terminal moved to software — no hardware VRAM forwarding

// ============================================
// System registers
// ============================================
reg [31:0] sysreg_rdata;
reg [63:0] cycle_counter;
reg [2:0] color_mode_reg;

reg [15:0] ds_slot_id_reg;
reg [31:0] ds_slot_offset_reg;
reg [31:0] ds_bridge_addr_reg;
reg [31:0] ds_length_reg;
reg [31:0] ds_param_addr_reg;
reg [31:0] ds_resp_addr_reg;

reg [7:0] pal_index_reg;

// Hardware timer — countdown with auto-reload
reg [31:0] timer_period;
reg [31:0] timer_counter;
reg        timer_enable;
reg        timer_irq_pending;
assign timer_irq = timer_irq_pending & timer_enable;
assign uart_rx_irq = !uart_rx_empty;  // IRQ when UART RX FIFO has data

// External IRQ mask — bits[3:0] = {vsync, reserved, link, uart_rx}
// Bit 2 was the hardware-mixer voice-end IRQ; mixer retired, bit kept
// reserved so firmware IRQ_MASK_* bit positions stay stable.
reg [3:0] irq_mask;
reg vsync_irq_pending;
assign ext_irq = (uart_rx_irq & irq_mask[0]) |
                 (link_irq & irq_mask[1]) |
                 (vsync_irq_pending & irq_mask[3]);

// Triple-buffered framebuffer
// 25-bit SDRAM half-word addresses (16-bit bus, byte addr = word addr × 2)
// Hardware feature flags — read-only, derived from variant defines at synthesis time
// Bit  0: Audio (stereo FIFO + CPU mixer)  Bit  8: FPU (RISC-V F ext)
// Bit  1: (reserved)                       Bit  9: Save slots
// Bit  2: Link cable                       Bit 10: GPU vertex color
// Bit  3: Analogizer                       Bit 11: GPU bilinear filter
// Bit  4: GPU span renderer (always)       Bit 12: GPU alpha blending
// Bit  5: GPU triangle rasterizer          Bit 13: GPU perspective spans
// Bit  6: MIDI (sample-based synth)        Bit 14: GPU pipelined fragments
// Bit  7: WiFi (reserved)                  Bit 15: (reserved)
localparam [31:0] HW_FEATURES =
    32'h0000_0001                           // Audio stereo FIFO present
    |
`ifdef EXCLUDE_LINK
    32'h0000_0000
`else
    32'h0000_0004
`endif
    |
    32'h0000_0010      // GPU span renderer — always present in any variant
    |
`ifdef GPU_FEAT_TRIANGLE
    32'h0000_0020      // GPU triangle rasterizer (bit 5)
`else
    32'h0000_0000
`endif
    |
`ifdef GPU_FEAT_VCOLOR
    32'h0000_0400      // GPU vertex color (bit 10)
`else
    32'h0000_0000
`endif
    |
`ifdef GPU_FEAT_BILINEAR
    32'h0000_0800      // GPU bilinear filter (bit 11)
`else
    32'h0000_0000
`endif
    |
`ifdef GPU_FEAT_ALPHA
    32'h0000_1000      // GPU alpha blending (bit 12)
`else
    32'h0000_0000
`endif
    |
`ifdef GPU_PERSP_IMPL
    32'h0000_2000      // GPU perspective spans (bit 13)
`else
    32'h0000_0000
`endif
    |
`ifdef GPU_FEAT_FRAG_PIPELINE
    32'h0000_4000      // GPU pipelined fragment processor (bit 14)
`else
    32'h0000_0000
`endif
    | 32'h0000_0348;  // Analogizer(3) + MIDI(6) + FPU(8) + Save slots(9) — always present

localparam FB_ADDR_0 = 25'h0000000;     // byte 0x000000 → CPU 0x10000000
localparam FB_ADDR_1 = 25'h0080000;     // byte 0x100000 → CPU 0x10100000
localparam FB_ADDR_2 = 25'h0100000;     // byte 0x200000 → CPU 0x10200000
localparam TERM_FB_ADDR = 25'h0180000;  // byte 0x300000 → CPU 0x10300000
reg [1:0] fb_display_idx;
reg [1:0] fb_ready_idx;
reg fb_swap_pending;
reg term_fb_active;  // 1=scanout reads terminal FB, 0=app triple-buffered FB

// VRR swap hold: skip N vsyncs before presenting a queued frame
reg [3:0] vrr_swap_hold;     // firmware-written: vsyncs to skip per swap
reg [3:0] vrr_hold_counter;  // counts down to 0 then swaps

wire [24:0] fb_app_addr = (fb_display_idx == 2'd0) ? FB_ADDR_0 :
                           (fb_display_idx == 2'd1) ? FB_ADDR_1 :
                                                      FB_ADDR_2;
wire [24:0] fb_display_addr_reg = term_fb_active ? TERM_FB_ADDR : fb_app_addr;

assign color_mode = color_mode_reg;
assign fb_display_addr = fb_display_addr_reg;

// ============================================
// CDC synchronizers
// ============================================
reg [2:0] dataslot_allcomplete_sync;
always @(posedge clk) begin
    dataslot_allcomplete_sync <= {dataslot_allcomplete_sync[1:0], dataslot_allcomplete};
end
wire dataslot_allcomplete_s = dataslot_allcomplete_sync[2];

reg [2:0] vsync_sync;
always @(posedge clk) begin
    vsync_sync <= {vsync_sync[1:0], vsync};
end
wire vsync_rising = vsync_sync[1] && !vsync_sync[2];

reg [2:0] target_ack_sync;
reg [2:0] target_done_sync;
reg [2:0] target_err_sync [2:0];
always @(posedge clk or posedge reset) begin
    if (reset) begin
        target_ack_sync <= 3'b0;
        target_done_sync <= 3'b0;
        target_err_sync[0] <= 3'b0;
        target_err_sync[1] <= 3'b0;
        target_err_sync[2] <= 3'b0;
    end else begin
        target_ack_sync <= {target_ack_sync[1:0], target_dataslot_ack};
        target_done_sync <= {target_done_sync[1:0], target_dataslot_done};
        target_err_sync[0] <= {target_err_sync[0][1:0], target_dataslot_err[0]};
        target_err_sync[1] <= {target_err_sync[1][1:0], target_dataslot_err[1]};
        target_err_sync[2] <= {target_err_sync[2][1:0], target_dataslot_err[2]};
    end
end
wire target_ack_s = target_ack_sync[2];
wire target_done_s = target_done_sync[2];
wire [2:0] target_err_s = {target_err_sync[2][2], target_err_sync[1][2], target_err_sync[0][2]};

wire [31:0] cont1_key_s;
wire [31:0] cont1_joy_s;
wire [15:0] cont1_trig_s;
wire [31:0] cont2_key_s;
wire [31:0] cont2_joy_s;
wire [15:0] cont2_trig_s;
synch_3 #(.WIDTH(32)) s_cont1_key(.i(cont1_key), .o(cont1_key_s), .clk(clk), .rise(), .fall());
synch_3 #(.WIDTH(32)) s_cont2_key(.i(cont2_key), .o(cont2_key_s), .clk(clk), .rise(), .fall());
synch_3 #(.WIDTH(32)) s_cont2_joy(.i(cont2_joy), .o(cont2_joy_s), .clk(clk), .rise(), .fall());
synch_3 #(.WIDTH(16)) s_cont2_trig(.i(cont2_trig), .o(cont2_trig_s), .clk(clk), .rise(), .fall());
synch_3 #(.WIDTH(32)) s_cont1_joy(.i(cont1_joy), .o(cont1_joy_s), .clk(clk), .rise(), .fall());
synch_3 #(.WIDTH(16)) s_cont1_trig(.i(cont1_trig), .o(cont1_trig_s), .clk(clk), .rise(), .fall());

// ============================================
// System register write logic
// ============================================
reg sysreg_wr_fire;

// Loop variable for the save_slot_base reset sweep below.  Declared
// at module scope (Verilog-2001) so the always block can reference it.
integer rsi;

// Command state tracking: latch ACK/DONE per-command so firmware
// never sees stale status from a previous command.
// ds_cmd_active: set when DS_COMMAND fires, cleared when DONE is captured.
// ds_ack_latched/ds_done_latched: captured from CDC'd bridge signals
//   only during the active command window.
reg ds_cmd_active;
reg ds_ack_latched;
reg ds_done_latched;
reg ds_ack_seen_low;    // Must see ACK go low before accepting new ACK (prevents stale capture)
reg ds_done_seen_low;   // Must see DONE go low before accepting new DONE (prevents stale capture)
reg [2:0] ds_err_latched;

always @(posedge clk) begin
    if (reset) begin
        cycle_counter <= 0;
        color_mode_reg <= 0;
        fb_display_idx <= 2'd0;
        fb_ready_idx <= 2'd0;
        fb_swap_pending <= 1'b0;
        term_fb_active <= 1'b1;  // terminal FB visible by default at boot
        vrr_swap_hold <= 4'd0;
        vrr_hold_counter <= 4'd0;
        pal_wr <= 0;
        pal_addr <= 0;
        pal_data <= 0;
        pal_index_reg <= 0;
        ds_slot_id_reg <= 0;
        ds_slot_offset_reg <= 0;
        ds_bridge_addr_reg <= 0;
        ds_length_reg <= 0;
        ds_param_addr_reg <= 0;
        ds_resp_addr_reg <= 0;
        target_dataslot_read <= 0;
        target_dataslot_write <= 0;
        target_dataslot_openfile <= 0;
        target_dataslot_getfile <= 0;
        target_dataslot_id <= 0;
        target_dataslot_slotoffset <= 0;
        target_dataslot_bridgeaddr <= 0;
        target_dataslot_length <= 0;
        target_buffer_param_struct <= 0;
        target_buffer_resp_struct <= 0;
        ds_cmd_active <= 0;
        ds_ack_latched <= 0;
        ds_done_latched <= 0;
        ds_ack_seen_low <= 1;
        ds_done_seen_low <= 1;
        ds_err_latched <= 0;
        shutdown_ack <= 0;
        timer_period <= 0;
        timer_counter <= 0;
        timer_enable <= 0;
        timer_irq_pending <= 0;
        irq_mask <= 4'b0;
        vsync_irq_pending <= 0;
        dt_query_addr <= 0;
        dt_query_toggle <= 0;
        vrr_v_total <= 10'd262;
        snac_en_reg <= 0;
        snac_mode_reg <= 0;
        snac_div_reg <= 16'd499;  // default: 100KHz at 100MHz
        snac_tx_reg <= 0;
        snac_gpio_out_reg <= 8'hFF; // idle high
        snac_gpio_dir_reg <= 8'h03; // bits [1:0] output by default (OUT1, OUT2)
        snac_start_pulse <= 0;
        snac_bit_count_reg <= 0;
        snac_latch_en_reg <= 0;
    end else begin
        cycle_counter <= cycle_counter + 1;
        pal_wr <= 0;
        save_dt_commit <= 0;
        snac_start_pulse <= 0;

        // Hardware timer countdown
        if (timer_enable && timer_period != 0) begin
            if (timer_counter == 0) begin
                timer_counter <= timer_period - 1;
                timer_irq_pending <= 1;
            end else begin
                timer_counter <= timer_counter - 1;
            end
        end

        if (target_ack_s) begin
            target_dataslot_read <= 0;
            target_dataslot_write <= 0;
            target_dataslot_openfile <= 0;
            target_dataslot_getfile <= 0;
        end

        // Latch ACK/DONE from bridge only during active command.
        // Guard: must see signal LOW before accepting HIGH, to prevent
        // capturing stale ACK/DONE from the previous command that
        // hasn't fully deasserted through the CDC yet.
        //
        // Note: ds_done_seen_low is NOT gated on ds_ack_latched.
        // The bridge clears done (DATASLOTOP) before the host ACKs.
        // If the bridge completes fast, done goes LOW→HIGH before
        // ACK arrives. Gating on ds_ack_latched would miss the LOW
        // transition and hang forever waiting for DONE.
        if (ds_cmd_active) begin
            if (!target_ack_s && !ds_ack_latched)
                ds_ack_seen_low <= 1;
            if (ds_ack_seen_low && target_ack_s && !ds_ack_latched)
                ds_ack_latched <= 1;
            if (!target_done_s && !ds_done_latched)
                ds_done_seen_low <= 1;
            if (ds_done_seen_low && target_done_s && ds_ack_latched && !ds_done_latched) begin
                ds_done_latched <= 1;
                ds_err_latched <= target_err_s;
                ds_cmd_active <= 0;
                /* Deassert the command-type trigger once the bridge
                 * says DONE, so the next DS_COMMAND write generates a
                 * fresh 0→1 edge for core_bridge_cmd.v's edge detector.
                 * Without this, back-to-back same-type commands
                 * (e.g. two GETFILEs) never fire the second queue and
                 * time out waiting for ACK. */
                target_dataslot_read     <= 0;
                target_dataslot_write    <= 0;
                target_dataslot_openfile <= 0;
                target_dataslot_getfile  <= 0;
            end
        end

        if (sysreg_wr_fire) begin
            case (req_addr[8:2])
                7'b0_000011: term_fb_active <= req_wdata[0];  // TERM_FB_CTRL
                7'b0_011100: color_mode_reg <= req_wdata[2:0];  // offset 0x70
                7'b0_000110: if (req_wdata[0]) begin
                    fb_ready_idx <= req_wdata[2:1];
                    fb_swap_pending <= 1'b1;
                end
                7'b0_001000: ds_slot_id_reg <= req_wdata[15:0];
                7'b0_001001: ds_slot_offset_reg <= req_wdata;
                7'b0_001010: ds_bridge_addr_reg <= req_wdata;
                7'b0_001011: ds_length_reg <= req_wdata;
                7'b0_001100: ds_param_addr_reg <= req_wdata;
                7'b0_001101: ds_resp_addr_reg <= req_wdata;
                7'b0_001110: begin
                    if (!(target_dataslot_read || target_dataslot_write || target_dataslot_openfile || target_ack_s)) begin
                        target_dataslot_id <= ds_slot_id_reg;
                        target_dataslot_slotoffset <= ds_slot_offset_reg;
                        target_dataslot_bridgeaddr <= ds_bridge_addr_reg;
                        target_dataslot_length <= ds_length_reg;
                        target_buffer_param_struct <= ds_param_addr_reg;
                        target_buffer_resp_struct <= ds_resp_addr_reg;
                        target_dataslot_read <= 0;
                        target_dataslot_write <= 0;
                        target_dataslot_openfile <= 0;
                        target_dataslot_getfile <= 0;
                        case (req_wdata[2:0])
                            3'd1: target_dataslot_read <= 1;
                            3'd2: target_dataslot_write <= 1;
                            3'd3: target_dataslot_openfile <= 1;
                            3'd4: target_dataslot_getfile <= 1;
                            default: ;
                        endcase
                        // Reset status latches for new command
                        ds_cmd_active <= 1;
                        ds_ack_latched <= 0;
                        ds_done_latched <= 0;
                        ds_ack_seen_low <= 0;
                        ds_done_seen_low <= 0;
                        ds_err_latched <= 0;
                    end
                end
                7'b0_010000: pal_index_reg <= req_wdata[7:0];
                7'b0_010001: begin
                    pal_wr <= 1;
                    pal_addr <= pal_index_reg;
                    pal_data <= req_wdata[23:0];
                    pal_index_reg <= pal_index_reg + 1;
                end

                7'b0_010010: save_dt_slot <= req_wdata[3:0];  // SAVE_DT_SLOT (0x48)
                7'b0_010011: begin                               // SAVE_DT_SIZE (0x4C) — write triggers commit
                    save_dt_size <= req_wdata;
                    save_dt_commit <= 1;
                end

                // SNAC Shifter + GPIO registers (0xA0-0xAC)
                7'b0_101000: begin  // SNAC_CTRL (0xA0)
                    snac_en_reg   <= req_wdata[7];
                    snac_mode_reg <= req_wdata[9:8];
                    // Start a shift if bit[0] set and shifter not busy
                    if (req_wdata[0] && !snac_busy) begin
                        snac_start_pulse  <= 1;
                        snac_bit_count_reg <= req_wdata[5:1];
                        snac_latch_en_reg  <= req_wdata[6];
                    end
                end
                7'b0_101001: snac_div_reg <= req_wdata[15:0];   // SNAC_DIV (0xA4)
                7'b0_101010: snac_tx_reg  <= req_wdata;          // SNAC_DATA (0xA8)
                7'b0_101011: begin                                // SNAC_GPIO (0xAC)
                    snac_gpio_out_reg <= req_wdata[7:0];
                    snac_gpio_dir_reg <= req_wdata[15:8];
                end

                7'b0_101100: begin  // SYS_SHUTDOWN (0xB0)
                    shutdown_ack <= req_wdata[0];
                end

                // Hardware timer (0xB4-0xB8)
                7'b0_101101: begin  // TIMER_PERIOD (0xB4)
                    timer_period <= req_wdata;
                    timer_counter <= req_wdata - 1;
                end
                7'b0_101110: begin  // TIMER_CTRL (0xB8)
                    timer_enable <= req_wdata[0];
                    if (req_wdata[1]) timer_irq_pending <= 0;
                end

                // Datatable slot size query (0x90)
                7'b0_100100: begin
                    dt_query_addr <= req_wdata[9:0];
                    dt_query_toggle <= ~dt_query_toggle;
                end


                // Hardware mixer removed — register range 0xC0..0x10C is
                // now unused.  IRQ_MASK and VRR registers remain.
                7'b0_100111: vsync_irq_pending <= 0;                    // VSYNC_IRQ_CLEAR (0x9C) W1C
                7'b0_111111: irq_mask <= req_wdata[3:0];             // IRQ_MASK (0xFC)

                7'b0_110111: vrr_v_total <= req_wdata[9:0];          // VRR_V_TOTAL (0xDC)
                7'b0_111000: vrr_swap_hold <= req_wdata[3:0];        // VRR_SWAP_HOLD (0xE0)

                default: ;
            endcase
        end

        // Vsync IRQ — set on every vsync rising edge, cleared by W1C at 0x9C
        if (vsync_rising)
            vsync_irq_pending <= 1'b1;

        // Triple buffer vsync swap (with VRR hold support)
        if (fb_swap_pending && vsync_rising) begin
            if (vrr_hold_counter > 0) begin
                vrr_hold_counter <= vrr_hold_counter - 1;
            end else begin
                fb_display_idx <= fb_ready_idx;
                fb_swap_pending <= 1'b0;
            end
        end

        // Reload hold counter only for a fresh swap — not when replacing
        // an already-pending one, otherwise fast-flipping apps starve the
        // countdown and the display never updates.
        if (sysreg_wr_fire && req_addr[8:2] == 7'b0_000110 && req_wdata[0] && !fb_swap_pending)
            vrr_hold_counter <= vrr_swap_hold;
    end
end

// System register read mux
always @(*) begin
    case (req_addr[8:2])
        7'b0_000000: sysreg_rdata = {30'b0, dataslot_allcomplete_s, 1'b1};
        7'b0_000001: sysreg_rdata = cycle_counter[31:0];
        7'b0_000010: sysreg_rdata = cycle_counter[63:32];
        7'b0_000011: sysreg_rdata = {31'b0, term_fb_active};  // TERM_FB_CTRL
        7'b0_011100: sysreg_rdata = {29'b0, color_mode_reg};
        7'b0_000100: sysreg_rdata = {7'b0, fb_display_addr_reg};
        7'b0_000101: sysreg_rdata = {30'b0, fb_display_idx};
        7'b0_000110: sysreg_rdata = {29'b0, fb_display_idx, fb_swap_pending};
        7'b0_001000: sysreg_rdata = {16'b0, ds_slot_id_reg};
        7'b0_001001: sysreg_rdata = ds_slot_offset_reg;
        7'b0_001010: sysreg_rdata = ds_bridge_addr_reg;
        7'b0_001011: sysreg_rdata = ds_length_reg;
        7'b0_001100: sysreg_rdata = ds_param_addr_reg;
        7'b0_001101: sysreg_rdata = ds_resp_addr_reg;
        7'b0_001110: sysreg_rdata = 32'h0;
        7'b0_001111: sysreg_rdata = {25'b0, bridge_wr_idle, ~target_ack_s, ds_err_latched, ds_done_latched, ds_ack_latched};
        7'b0_010000: sysreg_rdata = {24'b0, pal_index_reg};
        // 0x44 (PAL_DATA write) doesn't have a meaningful read-back —
        // case row dropped to save the comparator LUT.  Reads return 0
        // via the default branch.
        7'b0_010100: sysreg_rdata = cont1_key_s;
        7'b0_010101: sysreg_rdata = cont1_joy_s;
        7'b0_010110: sysreg_rdata = {16'b0, cont1_trig_s};
        7'b0_010111: sysreg_rdata = cont2_key_s;
        7'b0_011000: sysreg_rdata = cont2_joy_s;
        7'b0_011001: sysreg_rdata = {16'b0, cont2_trig_s};
        7'b0_011010: sysreg_rdata = app_id;
        // SNAC Shifter + GPIO registers (0xA0-0xAC)
        7'b0_101000: sysreg_rdata = {22'b0, snac_mode_reg, snac_en_reg, 6'b0, snac_busy};  // SNAC_CTRL
        7'b0_101001: sysreg_rdata = {16'b0, snac_div_reg};                                   // SNAC_DIV
        7'b0_101010: sysreg_rdata = snac_rx_data;                                             // SNAC_DATA (RX)
        7'b0_101011: sysreg_rdata = {16'b0, snac_pin_dir, snac_pin_in};                      // SNAC_GPIO (read: input + dir)
        // Shutdown handshake
        7'b0_101100: sysreg_rdata = {31'b0, shutdown_pending};  // SYS_SHUTDOWN (0xB0)
        // Hardware timer (0xB4-0xBC)
        7'b0_101101: sysreg_rdata = timer_period;
        7'b0_101110: sysreg_rdata = {30'b0, timer_irq_pending, timer_enable};
        7'b0_101111: sysreg_rdata = timer_counter;
        7'b0_111111: sysreg_rdata = {28'b0, irq_mask};               // IRQ_MASK (0xFC)
        7'b0_110111: sysreg_rdata = {22'b0, vrr_v_total};             // VRR_V_TOTAL (0xDC)
        7'b0_111000: sysreg_rdata = {28'b0, vrr_swap_hold};           // VRR_SWAP_HOLD (0xE0)
        // Datatable slot size query (0x90): bit 31 = valid, bits 30:0 = data
        7'b0_100100: sysreg_rdata = {dt_query_valid, dt_query_data[30:0]};
        // Bridge debug (0x94): internal latch state for diagnosing DMA hangs
        7'b0_100101: sysreg_rdata = {24'b0,
            target_dataslot_read,   // bit 7: command signal (CPU domain)
            target_done_s,          // bit 6: done CDC output
            target_ack_s,           // bit 5: ack CDC output
            ds_done_latched,        // bit 4
            ds_done_seen_low,       // bit 3
            ds_ack_latched,         // bit 2
            ds_ack_seen_low,        // bit 1
            ds_cmd_active           // bit 0
        };
        7'b0_100110: sysreg_rdata = HW_FEATURES;                    // HW_FEATURES (0x98)
        7'b0_100111: sysreg_rdata = {31'b0, vsync_irq_pending};   // VSYNC_IRQ_PENDING (0x9C)
        default: sysreg_rdata = 32'h0;
    endcase
end

// ============================================
// Peripheral read data mux
// ============================================
// ============================================
// UART RX FIFO — 1024-byte buffer so CPU doesn't have to read every byte within 10μs
// ============================================
reg [7:0]  uart_rx_fifo [0:1023];
reg [9:0]  uart_rx_wr_ptr;
reg [9:0]  uart_rx_rd_ptr;
wire [9:0] uart_rx_count = uart_rx_wr_ptr - uart_rx_rd_ptr;
wire       uart_rx_empty = (uart_rx_wr_ptr == uart_rx_rd_ptr);
wire       uart_rx_full  = (uart_rx_count == 10'd1023);

// CPU reads the byte at rd_ptr
wire [7:0] uart_rx_data  = uart_rx_fifo[uart_rx_rd_ptr];
wire       uart_rx_valid = !uart_rx_empty;

always @(posedge clk) begin
    if (reset) begin
        uart_rx_wr_ptr <= 0;
        uart_rx_rd_ptr <= 0;
    end else begin
        // Write: new byte from UART RX module
        if (uart_rx_dv && !uart_rx_full) begin
            uart_rx_fifo[uart_rx_wr_ptr[9:0]] <= uart_rx_byte;
            uart_rx_wr_ptr <= uart_rx_wr_ptr + 10'd1;
        end
        // Read: CPU reads the RX register → advance rd_ptr
        if (state == S_PERIPH_RD && reg_uart && req_addr[3:2] == 2'b10 && !uart_rx_empty)
            uart_rx_rd_ptr <= uart_rx_rd_ptr + 10'd1;
    end
end

// ============================================
// UART TX — single-byte passthrough.  The kernel maintains its own
// software TX ring (terminal.c uart_tx_ring) and polls UART_TX_RDY
// before each write, so the FPGA-side FIFO would just duplicate
// that buffering for ~50 ALMs.  Drop it: pulse uart_tx_dv with the
// CPU's write data when uart_tx is idle, drop the byte if not.
// ============================================
reg uart_tx_fifo_we;       // pulse from FSM on UART_TX_DATA write
reg [7:0] uart_tx_fifo_din; // CPU's write data, latched

wire uart_tx_idle = !uart_tx_active;
wire uart_tx_rdy  = !uart_tx_active;

always @(posedge clk) begin
    if (reset) begin
        uart_tx_dv   <= 0;
        uart_tx_byte <= 0;
    end else begin
        uart_tx_dv <= 0;
        if (uart_tx_fifo_we && !uart_tx_active) begin
            uart_tx_byte <= uart_tx_fifo_din;
            uart_tx_dv   <= 1'b1;
        end
    end
end

// UART read data mux:
//   0x4F000000 (offset 0x00): status
//     bit 0  = 1               (UART present marker)
//     bit 1  = uart_tx_rdy     (TX accepts a byte right now)
//     bit 2  = uart_rx_valid   (RX has data)
//     bit 3  = uart_tx_idle    (uart_tx not shifting; same as TX_RDY now
//                               that the FPGA TX FIFO is gone)
//     bits [17:8] = uart_rx_count (RX FIFO depth, 10 bits)
//   0x4F000004 (offset 0x04): TX data (write-only, reads as 0)
//   0x4F000008 (offset 0x08): RX data (read pops from FIFO)
wire [31:0] uart_rdata = (req_addr[3:2] == 2'b00) ?
        {14'b0, uart_rx_count, 4'b0,
         uart_tx_idle, uart_rx_valid, uart_tx_rdy, 1'b1}
    : (req_addr[3:2] == 2'b10) ? {24'b0, uart_rx_data}
    : 32'h0;

/* Audio region read decode:
 *   0x00: {21'b0, fifo_full, fifo_level[9:0]}
 *   0x04: DMA ring_base (readback)
 *   0x08: DMA ring_len  (readback)
 *   0x0C: {31'b0, DMA enable}
 *   0x10: DMA read_ptr  (live — consumer position) */
wire [31:0] audio_rdata = (req_addr[4:2] == 3'd0) ? {21'b0, audio_fifo_full, audio_fifo_level} :
                          (req_addr[4:2] == 3'd1) ? audio_dma_base :
                          (req_addr[4:2] == 3'd2) ? {18'b0, audio_dma_len} :
                          (req_addr[4:2] == 3'd3) ? {31'b0, audio_dma_enable} :
                          (req_addr[4:2] == 3'd4) ? {18'b0, audio_dma_read_ptr} :
                          32'h0;

/* CRAM1 burst prefetch read decode:
 *   0x00: (write-only — returns 0 on read)
 *   0x04: {31'b0, busy}
 *   0x08: current FIFO head (auto-advances on read, see pop pulse below) */
wire [31:0] cram1_burst_rdata = (req_addr[3:2] == 2'd1) ? {31'b0, cram1_burst_busy} :
                                (req_addr[3:2] == 2'd2) ? cram1_burst_data_q :
                                32'h0;

wire [31:0] periph_rd_mux = reg_sysreg ? sysreg_rdata :
                             reg_audio  ? audio_rdata :
                             reg_cram1  ? cram1_burst_rdata :
                             reg_link   ? link_reg_rdata :
                             reg_uart   ? uart_rdata :
                             reg_gpu    ? gpu_reg_rdata :
                             32'h0;

// ============================================
// FSM
// ============================================
localparam S_IDLE      = 3'd0;
localparam S_BRAM_RD   = 3'd1;
localparam S_PERIPH_RD = 3'd2;
localparam S_PERIPH_WR = 3'd3;
localparam S_TERM      = 3'd4;
localparam S_WR_NEXT   = 3'd5;
localparam S_BRAM_WR   = 3'd6;

reg [2:0] state;

// Latched request fields
reg [31:0] req_addr;
reg [31:0] req_wdata;
reg [3:0]  req_wstrb;
reg        is_write;
reg [7:0]  burst_len;
reg [7:0]  burst_count;

// Region flags (latched on accept)
reg reg_ram;
// reg_term removed — terminal in software
reg reg_sysreg;
reg reg_audio;
reg reg_cram1;
reg reg_link;
reg reg_uart;
reg reg_gpu;

wire beat_is_last = (burst_count == burst_len);

// Terminal moved to software (S_TERM state unused)

// BRAM address mux (32KB: 13-bit word address = [14:2])
wire [12:0] bram_next_word = req_addr[14:2] + 13'd1;

// Forward declare can_push_beat / bram_hold so the ram_addr_mux
// combinational block below can use them.  bram_hold freezes the
// BRAM pre-fetch optimisation when we can't accept a new beat.
wire can_push_beat = !s_axi_rvalid || (s_axi_rvalid && s_axi_rready);
wire bram_hold     = !can_push_beat;

always @(*) begin
    case (state)
        S_IDLE: begin
            // Priority matches the FSM's AR-before-AW arbitration below.
            // A mismatch here would mis-prefetch when both AR and AW are
            // valid simultaneously.
            if (s_axi_arvalid)
                ram_addr_mux = ar_addr[14:2];
            else if (s_axi_awvalid)
                ram_addr_mux = aw_addr[14:2];
            else
                ram_addr_mux = 13'd0;
        end
        S_BRAM_RD: begin
            if (bram_hold || beat_is_last)
                ram_addr_mux = req_addr[14:2];     // freeze on current beat
            else
                ram_addr_mux = bram_next_word;     // pre-fetch next
        end
        default: ram_addr_mux = req_addr[14:2];
    endcase
end

assign ram_wren = (state == S_BRAM_WR) && (|req_wstrb);

// ============================================
// Region decode helpers
// ============================================
wire ar_dec_cram1  = (ar_addr[31:24] == 8'h4E);
wire aw_dec_cram1  = (aw_addr[31:24] == 8'h4E);

wire ar_dec_ram    = (ar_addr[31:15] == 17'b0); // 32KB: 0x00000-0x07FFF
// sysreg covers 0x40000000-0x400001FF (512 bytes / 128 word slots) so the
// case statement on req_addr[8:2] reaches every defined slot, including
// the extended mixer regs (0x100-0x108) and the AWE registers (0x110-0x148).
// Pre-AWE this was 24'h400000 (256 bytes) — silently dropped MIX_VOICE_FILTER*
// writes at 0x100+; the SW path's filter calls were no-ops.
wire ar_dec_sysreg = (ar_addr[31:9]  == 23'h200000);
wire ar_dec_audio  = (ar_addr[31:24] == 8'h4C);
wire ar_dec_link   = (ar_addr[31:24] == 8'h4D);
wire ar_dec_uart   = (ar_addr[31:24] == 8'h4F);
wire ar_dec_gpu    = (ar_addr[31:24] == 8'h4A);

wire aw_dec_ram    = (aw_addr[31:15] == 17'b0); // 32KB: 0x00000-0x07FFF
wire aw_dec_sysreg = (aw_addr[31:9]  == 23'h200000);
wire aw_dec_audio  = (aw_addr[31:24] == 8'h4C);
wire aw_dec_link   = (aw_addr[31:24] == 8'h4D);
wire aw_dec_uart   = (aw_addr[31:24] == 8'h4F);
wire aw_dec_gpu    = (aw_addr[31:24] == 8'h4A);

// ============================================
// R channel hold-until-rready (proper AXI)
// ============================================
// axi_periph_slave covers BRAM + UART + system registers — the boot
// stub runs entirely out of BRAM via this slave.  The old 1-cycle
// pulse rvalid pattern dropped L1 refill beats whenever
// cpu_target_port's 1-entry registered response slot was holding
// the previous beat.  Fix: hold rvalid until rready fires, and
// only advance req_addr / latch new ram_rdata when the slot is
// draining or empty.  can_push_beat / bram_hold are declared
// earlier (just above the ram_addr_mux block) so the mux can freeze
// the pre-fetch in the same combinational window.

// ============================================
// Main FSM
// ============================================
always @(posedge clk or posedge reset) begin
    if (reset) begin
        state <= S_IDLE;
        s_axi_arready <= 0;
        s_axi_rvalid <= 0;
        s_axi_rdata <= 0;
        s_axi_rresp <= 0;
        s_axi_rlast <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_bvalid <= 0;
        s_axi_bresp <= 0;

        req_addr <= 0;
        req_wdata <= 0;
        req_wstrb <= 0;
        is_write <= 0;
        burst_len <= 0;
        burst_count <= 0;

        reg_ram <= 0;
        reg_sysreg <= 0;
        reg_audio <= 0;
        reg_cram1 <= 0;
        reg_link <= 0;
        reg_gpu <= 0;
        gpu_reg_wr <= 0;
        reg_uart <= 0;

        sysreg_wr_fire <= 0;
        audio_sample_wr <= 0;
        audio_sample_data <= 0;
        audio_dma_base <= 32'd0;
        audio_dma_len <= 14'd0;
        audio_dma_enable <= 1'b0;
        cram1_burst_addr_wr    <= 0;
        cram1_burst_addr_wdata <= 22'd0;
        cram1_burst_data_rd    <= 0;
        uart_tx_fifo_we  <= 0;
        uart_tx_fifo_din <= 0;
        link_reg_wr <= 0;
        link_reg_rd <= 0;
        link_reg_addr <= 0;
        link_reg_wdata <= 0;

    end else begin
        // Defaults: deassert single-cycle pulses.  rvalid and bvalid
        // are NOT here — they are held until their corresponding
        // ready fires.  The drop-on-handshake block below clears
        // them on the cycle the master accepts the response.
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        cram1_burst_addr_wr <= 0;
        cram1_burst_data_rd <= 0;

        if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
        sysreg_wr_fire <= 0;
        audio_sample_wr <= 0;
        uart_tx_fifo_we <= 0;
        link_reg_wr <= 0;
        link_reg_rd <= 0;
        gpu_reg_wr <= 0;

        case (state)

        // ============================================
        // IDLE: Accept AR (read) or AW+W (write)
        // ============================================
        S_IDLE: begin
            // Don't accept a new AR while the previous burst's last
            // beat is still draining — would smash held rvalid/rdata.
            if (s_axi_arvalid && !s_axi_rvalid) begin
                s_axi_arready <= 1;
                is_write <= 0;
                req_addr <= ar_addr;
                burst_len <= s_axi_arlen;
                burst_count <= 0;

                reg_ram    <= ar_dec_ram;
                reg_sysreg <= ar_dec_sysreg;
                reg_audio  <= ar_dec_audio;
                reg_cram1  <= ar_dec_cram1;
                reg_link   <= ar_dec_link;

                reg_uart   <= ar_dec_uart;
                reg_gpu    <= ar_dec_gpu;

                if (ar_dec_ram)
                    state <= S_BRAM_RD;
                else begin
                    state <= S_PERIPH_RD;
                    if (ar_dec_link) begin
                        link_reg_addr <= ar_addr[6:2];
                        link_reg_rd <= 1;
                    end
                    if (ar_dec_gpu) begin
                        gpu_reg_addr <= ar_addr[5:2];
                    end
                end

            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1;
                is_write <= 1;
                req_addr <= aw_addr;
                burst_len <= s_axi_awlen;
                burst_count <= 0;

                reg_ram    <= aw_dec_ram;
                reg_sysreg <= aw_dec_sysreg;
                reg_audio  <= aw_dec_audio;
                reg_cram1  <= aw_dec_cram1;
                reg_link   <= aw_dec_link;

                reg_uart   <= aw_dec_uart;
                reg_gpu    <= aw_dec_gpu;

                if (s_axi_wvalid) begin
                    s_axi_wready <= 1;
                    req_wdata <= s_axi_wdata;
                    req_wstrb <= s_axi_wstrb;

                    if (aw_dec_ram)
                        state <= S_BRAM_WR;
                    else begin
                        state <= S_PERIPH_WR;
                        if (aw_dec_sysreg && |s_axi_wstrb)
                            sysreg_wr_fire <= 1;
                        if (aw_dec_audio && |s_axi_wstrb) begin
                            /* AUDIO_BASE + 0x00: stereo sample push to dcfifo.
                             * AUDIO_BASE + 0x04: DMA ring_base (SDRAM byte addr).
                             * AUDIO_BASE + 0x08: DMA ring_len (stereo pairs).
                             * AUDIO_BASE + 0x0C: DMA enable (bit 0). */
                            case (aw_addr[4:2])
                                3'd0: begin
                                    audio_sample_wr   <= 1;
                                    audio_sample_data <= s_axi_wdata;
                                end
                                3'd1: audio_dma_base   <= s_axi_wdata;
                                3'd2: audio_dma_len    <= s_axi_wdata[13:0];
                                3'd3: audio_dma_enable <= s_axi_wdata[0];
                                default: ;
                            endcase
                        end
                        if (aw_dec_cram1 && |s_axi_wstrb && aw_addr[3:2] == 2'd0) begin
                            /* CRAM1_BURST + 0x00: fire an 8-word burst. */
                            cram1_burst_addr_wr    <= 1;
                            cram1_burst_addr_wdata <= s_axi_wdata[21:0];
                        end
                        if (aw_dec_link && |s_axi_wstrb) begin
                            link_reg_wr <= 1;
                            link_reg_addr <= aw_addr[6:2];
                            link_reg_wdata <= s_axi_wdata;
                        end
                        if (aw_dec_gpu && |s_axi_wstrb) begin
                            gpu_reg_wr <= 1;
                            gpu_reg_addr <= aw_addr[5:2];
                            gpu_reg_wdata <= s_axi_wdata;
                        end
                        if (aw_dec_uart && |s_axi_wstrb && aw_addr[3:2] == 2'b01) begin
                            // UART_TX_DATA (offset 0x04): push into TX FIFO.
                            // Mirrors the S_WR_NEXT path so bundled AW+W
                            // writes don't drop the byte.
                            uart_tx_fifo_din <= s_axi_wdata[7:0];
                            uart_tx_fifo_we  <= 1;
                        end
                    end
                end else begin
                    state <= S_WR_NEXT;
                end
            end
        end

        // ============================================
        // BRAM read — only advance when the output slot is empty
        // or draining this cycle.  bram_hold freezes the BRAM
        // pre-fetch when holding so ram_rdata stays stable.
        // ============================================
        S_BRAM_RD: begin
            if (can_push_beat) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= ram_rdata;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= beat_is_last;
                burst_count  <= burst_count + 1;
                if (beat_is_last) state <= S_IDLE;
                else req_addr <= req_addr + 32'd4;
            end
            // else: hold (bram_hold freezes BRAM read)
        end

        // ============================================
        // BRAM write
        // ============================================
        S_BRAM_WR: begin
            burst_count <= burst_count + 1;
            if (beat_is_last) begin
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end else begin
                req_addr <= req_addr + 32'd4;
                state <= S_WR_NEXT;
            end
        end

        // ============================================
        // Peripheral read — same hold-until-drain pattern
        // ============================================
        S_PERIPH_RD: begin
            if (can_push_beat) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= periph_rd_mux;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= beat_is_last;
                burst_count  <= burst_count + 1;
                if (beat_is_last) state <= S_IDLE;
                else req_addr <= req_addr + 32'd4;
                /* Pop-pulse side effects on specific read addresses. */
                if (reg_cram1 && req_addr[3:2] == 2'd2)
                    cram1_burst_data_rd <= 1;
            end
        end

        // ============================================
        // Peripheral write
        // ============================================
        S_PERIPH_WR: begin
            burst_count <= burst_count + 1;
            if (beat_is_last) begin
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end else begin
                req_addr <= req_addr + 32'd4;
                state <= S_WR_NEXT;
            end
        end

        // S_TERM removed — terminal now rendered in software

        // ============================================
        // WR_NEXT: Accept next W beat
        // ============================================
        S_WR_NEXT: begin
            if (s_axi_wvalid) begin
                s_axi_wready <= 1;
                req_wdata <= s_axi_wdata;
                req_wstrb <= s_axi_wstrb;

                if (reg_ram) begin
                    state <= S_BRAM_WR;
                end else begin
                    state <= S_PERIPH_WR;
                    if (reg_sysreg && |s_axi_wstrb)
                        sysreg_wr_fire <= 1;
                    if (reg_audio && |s_axi_wstrb) begin
                        case (req_addr[4:2])
                            3'd0: begin
                                audio_sample_wr   <= 1;
                                audio_sample_data <= s_axi_wdata;
                            end
                            3'd1: audio_dma_base   <= s_axi_wdata;
                            3'd2: audio_dma_len    <= s_axi_wdata[13:0];
                            3'd3: audio_dma_enable <= s_axi_wdata[0];
                            default: ;
                        endcase
                    end
                    if (reg_cram1 && |s_axi_wstrb && req_addr[3:2] == 2'd0) begin
                        cram1_burst_addr_wr    <= 1;
                        cram1_burst_addr_wdata <= s_axi_wdata[21:0];
                    end
                    if (reg_link && |s_axi_wstrb) begin
                        link_reg_wr <= 1;
                        link_reg_addr <= req_addr[6:2];
                        link_reg_wdata <= s_axi_wdata;
                    end
                    if (reg_uart && |s_axi_wstrb && req_addr[3:2] == 2'b01) begin
                        // Push into the TX FIFO; the FIFO drain block
                        // hands bytes to uart_tx asynchronously. Drops
                        // silently if FIFO is full -- matches the prior
                        // fire-and-forget software contract.
                        uart_tx_fifo_din <= s_axi_wdata[7:0];
                        uart_tx_fifo_we  <= 1;
                    end
                    if (reg_gpu && |s_axi_wstrb) begin
                        gpu_reg_wr <= 1;
                        gpu_reg_addr <= req_addr[5:2];
                        gpu_reg_wdata <= s_axi_wdata;
                    end
                end
            end
        end

        endcase
    end
end

endmodule
