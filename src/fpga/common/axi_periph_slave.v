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
    // awburst[1:0]: 00 = FIXED (req_addr stays put for repeated writes
    // to one MMIO register, such as LUT uploads), 01 = INCR (default).
    input  wire [1:0]  s_axi_awburst,

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
    input wire         early_vblank,
    input wire [31:0]  cont1_key,
    input wire [31:0]  cont1_joy,
    input wire [15:0]  cont1_trig,
    input wire [31:0]  cont2_key,
    input wire [31:0]  cont2_joy,
    input wire [15:0]  cont2_trig,
    input wire [31:0]  cont3_key,
    input wire [31:0]  cont3_joy,
    input wire [15:0]  cont3_trig,
    input wire [31:0]  cont4_key,
    input wire [31:0]  cont4_joy,
    input wire [15:0]  cont4_trig,
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
    output reg         pal_commit,
    input  wire        pal_busy,

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

    // Audio FIFO status.  v2 routes the HW mixer directly into
    // audio_output, so the slave no longer drives sample writes — it
    // only exposes fifo_level/full for readback (largely informational
    // now; mixer manages its own rate-limiting).
    input wire  [9:0]  audio_fifo_level,
    input wire         audio_fifo_full,

    // CRAM0 ownership mode register (0x4E000000 bit 0):
    //   0 = bridge owns CRAM0 (APF load/save transfers drive the chip)
    //   1 = CPU owns CRAM0 (AXI accesses go through cram0_cdc)
    // Firmware flips this around explicit quiescent periods — see
    // docs/memory-arch-v2.md §4 for the protocol.
    output reg         cram0_mode,

    // Hardware audio mixer control (see audio_mixer.v v3, MMIO at 0x48000000).
    // Flat addressing — MMIO address encodes voice and field directly:
    //   addr[10:6] = voice index, addr[5:2] = vtbl field for per-voice writes
    //   addr[11]=1, [7]=0      = control region (group/master/ctrl/irq)
    //   addr[11]=1, [7]=1      = per-voice POS_INT readback
    output reg         mix_enable,
    output reg         mix_voice_wr,
    output reg  [4:0]  mix_voice_sel,    // derived per-write from addr[10:6]
    output reg  [3:0]  mix_voice_field,  // derived per-write from addr[5:2]
    output reg  [31:0] mix_voice_wdata,
    output reg         mix_irq_clear_wr,
    output reg  [31:0] mix_irq_clear,
    output reg  [7:0]  mix_master_vol,
    output reg  [7:0]  mix_group_vol_0,
    output reg  [7:0]  mix_group_vol_1,
    output reg  [7:0]  mix_group_vol_2,
    output reg  [7:0]  mix_group_vol_3,
    output reg [63:0]  mix_voice_group_packed,  // 32 voices × 2 bits LSB-first
    output wire [4:0]  mix_voice_sel_rd,        // combinational from read addr (POS readback)
    input  wire [31:0] mix_active_mask,     // 32-bit active voice bitmap
    input  wire [21:0] mix_pos_readback,
    input  wire [31:0] mix_voice_end_pending,

    // Link MMIO interface
    output reg         link_reg_wr,
    output reg         link_reg_rd,
    output reg  [4:0]  link_reg_addr,
    output reg  [31:0] link_reg_wdata,
    input wire  [31:0] link_reg_rdata,

    // Save datatable update interface (CPU -> datatable via core_top CDC)
    // save_dt_slot: 0-9 = save slots, 0xF = pre-save config/settings slot.
    output reg  [3:0]  save_dt_slot,
    output reg  [31:0] save_dt_size,    // size to write
    output reg         save_dt_commit,  // toggle: write save_dt_size to datatable

    // UART interface
    output reg         uart_tx_dv,      // TX data valid (1 cycle pulse)
    output reg  [7:0]  uart_tx_byte,    // TX data byte
    input wire         uart_tx_active,  // TX busy
    input wire         uart_rx_dv,      // RX data valid (1 cycle pulse)
    input wire  [7:0]  uart_rx_byte,    // RX data byte

    // App ID from instance JSON memory_writes
    input wire  [31:0] app_id,

    // Analogizer settings mirror. The Pocket bridge writes these through
    // interact.json, while firmware can override them after loading
    // analogizer.cfg. Writes cross to clk_74a in core_top.
    input  wire [31:0] analogizer_settings,
    input  wire [31:0] analogizer_hoffset,
    input  wire [31:0] analogizer_voffset,
    output reg  [2:0]  analogizer_cpu_wr_toggle,
    output reg  [31:0] analogizer_cpu_wr_settings,
    output reg  [31:0] analogizer_cpu_wr_hoffset,
    output reg  [31:0] analogizer_cpu_wr_voffset,

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

    // Display V_TOTAL — output drives scaler timing.
    // Firmware owns the adaptive refresh policy and writes the desired
    // line count through SYSREG word 55. When Analogizer video or SNAC owns
    // the adapter path, the output is locked to fixed NTSC/PAL timing so
    // external hardware does not see adaptive refresh changes.
    output wire [9:0]  vrr_v_total,
    input  wire        analogizer_enabled,

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
    input  wire [31:0] gpu_reg_rdata,

    // CMD_FLIP side-port — single-cycle pulse from gpu_core when its
    // command processor reaches a CMD_FLIP and m_wr_* has drained.
    // Latched into the same fb_ready_idx / fb_swap_pending state the
    // kernel writes via sysreg 0x18; GPU has priority on conflicting
    // same-cycle writes (rare since the kernel only writes during
    // init / fallback path).  See docs/cr-gpu-triggered-flip.md.
    input  wire        gpu_swap_req,
    input  wire [1:0]  gpu_swap_idx,

    // CMD_FLIP backpressure — exposes the live fb_swap_pending bit so
    // gpu_core can stall its CMD_FLIP execution until vsync has
    // consumed the previous swap.  This moves the swap-serialization
    // wait out of the CPU (kernel acquire_next) and into the GPU
    // pipeline, letting old SDK binaries run with a non-blocking
    // kernel without losing frames to gpu_swap_req-vs-pending races.
    output wire        fb_swap_pending_o
);

wire reset = ~reset_n;

// ============================================
// Address decode (combinatorial, on AXI address channels)
// ============================================
wire [31:0] ar_addr = s_axi_araddr;
wire [31:0] aw_addr = s_axi_awaddr;

// ============================================
// SNAC raw shifter/GPIO plus optional PSX poller.
// The RndMnkIII Analogizer/SNAC pinout stays as the physical layer; firmware
// can either drive the raw pins through SNAC_CTRL/SNAC_GPIO or let the PSX
// poller own the same pins and expose decoded state/edges.
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
reg        snac_hw_enable_reg;
reg        snac_hw_analog_reg;
reg        snac_hw_fast_reg;
reg        snac_hw_clear_irq_pulse;
reg        snac_hw_clear_edges_pulse;

wire        snac_busy;
wire        snac_done;
wire [31:0] snac_rx_data;
wire        snac_shift_clk;
wire        snac_shift_mosi;
wire        snac_shift_latch;
wire [7:0]  snac_hw_pin_out;
wire [7:0]  snac_hw_pin_dir;
wire        snac_hw_valid;
wire        snac_hw_irq_pending;
wire [15:0] snac_hw_buttons;
wire [15:0] snac_hw_pressed;
wire [15:0] snac_hw_released;
wire [15:0] snac_hw_raw_buttons;
wire [15:0] snac_hw_lx;
wire [15:0] snac_hw_ly;
wire [15:0] snac_hw_rx;
wire [15:0] snac_hw_ry;
wire [7:0]  snac_hw_debug_status;
reg  [7:0]  snac_pin_in_meta;
reg  [7:0]  snac_pin_in_sync;

always @(posedge clk) begin
    if (reset) begin
        snac_pin_in_meta <= 8'h00;
        snac_pin_in_sync <= 8'h00;
    end else begin
        snac_pin_in_meta <= snac_pin_in;
        snac_pin_in_sync <= snac_pin_in_meta;
    end
end

// Shifter MISO inputs — synchronized from cart pins
wire snac_miso_a = snac_pin_in_sync[2];  // IO3/bank0[4] (Config A: DATA)
wire snac_miso_b = snac_pin_in_sync[5];  // IN4/bank0[7] (Config B: DAT)

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

snac_psx_poller snac_hw_psx (
    .clk(clk),
    .reset_n(reset_n),
    .enable(snac_hw_enable_reg),
    .analog_en(snac_hw_analog_reg),
    .fast_en(snac_hw_fast_reg),
    .clear_irq(snac_hw_clear_irq_pulse),
    .clear_edges(snac_hw_clear_edges_pulse),
    .pin_in(snac_pin_in_sync),
    .pin_out(snac_hw_pin_out),
    .pin_dir(snac_hw_pin_dir),
    .valid(snac_hw_valid),
    .irq_pending(snac_hw_irq_pending),
    .buttons(snac_hw_buttons),
    .buttons_pressed(snac_hw_pressed),
    .buttons_released(snac_hw_released),
    .raw_buttons_debug(snac_hw_raw_buttons),
    .joy_lx(snac_hw_lx),
    .joy_ly(snac_hw_ly),
    .joy_rx(snac_hw_rx),
    .joy_ry(snac_hw_ry),
    .debug_status(snac_hw_debug_status)
);

// SNAC pin output mux: shifter overrides GPIO when busy
// Config A: shifter drives [0]=CLK, [1]=LATCH
// Config B: shifter drives [6]=CLK/pin30, [7]=CMD(MOSI).  Bank0 stays input
// in PSX mode because the Pocket exposes one direction bit for bank0[7:4].
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
            // Config B: CLK on [6](pin30), MOSI on [7](IO6/pin31)
            snac_pin_out_mux[6] = snac_shift_clk;
            snac_pin_out_mux[7] = snac_shift_mosi;
            snac_pin_dir_mux[6] = 1'b1;
            snac_pin_dir_mux[7] = 1'b1;
        end
    end
end

assign snac_pin_out = snac_hw_enable_reg ? snac_hw_pin_out : snac_pin_out_mux;
assign snac_pin_dir = snac_hw_enable_reg ? snac_hw_pin_dir : snac_pin_dir_mux;
assign snac_enable  = snac_en_reg | snac_hw_enable_reg;

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

// External IRQ mask — bit 5=data-slot completion, bit 4=input,
// bit 3=vsync, bit 2=reserved, bit 1=link, bit 0=uart_rx.
// Bit 2 was the hardware-mixer voice-end IRQ; mixer retired, bit kept
// reserved so firmware IRQ_MASK_* bit positions stay stable.
reg [5:0] irq_mask;
reg vsync_irq_pending;
reg dataslot_irq_pending;
wire input_irq_pending;
assign ext_irq = (uart_rx_irq & irq_mask[0]) |
                 (link_irq & irq_mask[1]) |
                 (vsync_irq_pending & irq_mask[3]) |
                 (input_irq_pending & irq_mask[4]) |
                 (dataslot_irq_pending & irq_mask[5]);

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
// Bit  7: WiFi (reserved)                  Bit 15: GPU param span-list
//                                          Bit 16: GPU param span z-write
//                                          Bit 17: GPU param span z-test/write
//
// Perspective and parametric span commands are implemented and covered by
// the GPU acceptance tests. Keep the caps exposed so renderers can select
// these paths without local force-enable hacks.
localparam [31:0] HW_FEATURES =
    32'h0000_0001                           // Audio stereo FIFO present
    |
`ifdef EXCLUDE_LINK
    32'h0000_0000
`else
    32'h0000_0004
`endif
    |
    32'h0000_0010      // GPU span renderer
    |
    32'h0007_E000      // GPU persp + fragpipe + param span/list/z/scale caps (bits 13..18)
    | 32'h0000_0348;  // Analogizer(3) + MIDI(6) + FPU(8) + Save slots(9) — always present

localparam FB_ADDR_0 = 25'h0000000;     // byte 0x000000 → CPU 0x10000000
localparam FB_ADDR_1 = 25'h0080000;     // byte 0x100000 → CPU 0x10100000
localparam FB_ADDR_2 = 25'h0100000;     // byte 0x200000 → CPU 0x10200000
localparam TERM_FB_ADDR = 25'h0180000;  // byte 0x300000 → CPU 0x10300000
reg [1:0] fb_display_idx;
reg [1:0] fb_ready_idx;
reg fb_swap_pending;
reg fb_swap_consumed_this_frame;
assign fb_swap_pending_o = fb_swap_pending;
reg term_fb_active;  // 1=scanout reads terminal FB, 0=app triple-buffered FB

// V_TOTAL split: fixed NTSC/PAL value for Analogizer/SNAC adapter mode,
// firmware-computed value for normal Pocket LCD mode.  The fixed adapter
// value is derived directly from the current Analogizer settings so
// SNAC-only configurations also lock to a stable rate.
reg  [9:0] vrr_v_total_reg;
wire       analogizer_video_enabled = analogizer_settings[15];
wire [3:0] analogizer_video_mode    = analogizer_settings[13:10];
wire       analogizer_pal_mode      =
    (analogizer_video_mode == 4'h4) || (analogizer_video_mode == 4'hC);
wire       snac_configured          = (analogizer_settings[4:0] != 5'd0);
wire       vrr_fixed_rate_mode      =
    analogizer_enabled || analogizer_video_enabled || snac_configured || snac_en_reg;
wire [9:0] vrr_fixed_v_total        =
    (analogizer_video_enabled && analogizer_pal_mode) ? 10'd315 : 10'd262;
assign vrr_v_total = vrr_fixed_rate_mode ? vrr_fixed_v_total
                                         : vrr_v_total_reg;

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

reg [2:0] early_vblank_sync;
always @(posedge clk) begin
    early_vblank_sync <= {early_vblank_sync[1:0], early_vblank};
end
wire early_vblank_s = early_vblank_sync[2];

function [9:0] clamp_v_total;
    input [9:0] vt;
    begin
        if (vt < 10'd258)
            clamp_v_total = 10'd258;
        else if (vt > 10'd375)
            clamp_v_total = 10'd375;
        else
            clamp_v_total = vt;
    end
endfunction

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

reg [31:0] cont1_key_meta, cont1_key_s;
reg [31:0] cont1_joy_meta, cont1_joy_s;
reg [15:0] cont1_trig_meta, cont1_trig_s;
reg [31:0] cont2_key_meta, cont2_key_s;
reg [31:0] cont2_joy_meta, cont2_joy_s;
reg [15:0] cont2_trig_meta, cont2_trig_s;
reg [31:0] cont3_key_meta, cont3_key_s;
reg [31:0] cont3_joy_meta, cont3_joy_s;
reg [15:0] cont3_trig_meta, cont3_trig_s;
reg [31:0] cont4_key_meta, cont4_key_s;
reg [31:0] cont4_joy_meta, cont4_joy_s;
reg [15:0] cont4_trig_meta, cont4_trig_s;

// Controller buses are sampled state, not edge pulses.  Keep only the two
// synchronizer flops needed by this clock domain; the APF synch_2 helper also
// carries edge-detect history that these vector buses never use.
always @(posedge clk) begin
    cont1_key_meta  <= cont1_key;
    cont1_key_s     <= cont1_key_meta;
    cont1_joy_meta  <= cont1_joy;
    cont1_joy_s     <= cont1_joy_meta;
    cont1_trig_meta <= cont1_trig;
    cont1_trig_s    <= cont1_trig_meta;

    cont2_key_meta  <= cont2_key;
    cont2_key_s     <= cont2_key_meta;
    cont2_joy_meta  <= cont2_joy;
    cont2_joy_s     <= cont2_joy_meta;
    cont2_trig_meta <= cont2_trig;
    cont2_trig_s    <= cont2_trig_meta;

    cont3_key_meta  <= cont3_key;
    cont3_key_s     <= cont3_key_meta;
    cont3_joy_meta  <= cont3_joy;
    cont3_joy_s     <= cont3_joy_meta;
    cont3_trig_meta <= cont3_trig;
    cont3_trig_s    <= cont3_trig_meta;

    cont4_key_meta  <= cont4_key;
    cont4_key_s     <= cont4_key_meta;
    cont4_joy_meta  <= cont4_joy;
    cont4_joy_s     <= cont4_joy_meta;
    cont4_trig_meta <= cont4_trig;
    cont4_trig_s    <= cont4_trig_meta;
end

// ============================================
// Input hub: raw APF slots + compact change FIFO
// ============================================
localparam [7:0] INPUT_EVENT_SLOT_CHANGE = 8'h01;

reg [3:0] input_irq_mask;
reg       input_overflow;
reg [15:0] input_seq;

reg [31:0] input_prev_key0, input_prev_key1, input_prev_key2, input_prev_key3;
reg [31:0] input_prev_joy0, input_prev_joy1, input_prev_joy2, input_prev_joy3;
reg [15:0] input_prev_trig0, input_prev_trig1, input_prev_trig2, input_prev_trig3;
reg [1:0]  input_scan_slot;

reg [31:0] input_cur_key_sel;
reg [31:0] input_cur_joy_sel;
reg [15:0] input_cur_trig_sel;
reg [31:0] input_prev_key_sel;
reg [31:0] input_prev_joy_sel;
reg [15:0] input_prev_trig_sel;

always @(*) begin
    case (input_scan_slot)
        2'd0: begin
            input_cur_key_sel   = cont1_key_s;
            input_cur_joy_sel   = cont1_joy_s;
            input_cur_trig_sel  = cont1_trig_s;
            input_prev_key_sel  = input_prev_key0;
            input_prev_joy_sel  = input_prev_joy0;
            input_prev_trig_sel = input_prev_trig0;
        end
        2'd1: begin
            input_cur_key_sel   = cont2_key_s;
            input_cur_joy_sel   = cont2_joy_s;
            input_cur_trig_sel  = cont2_trig_s;
            input_prev_key_sel  = input_prev_key1;
            input_prev_joy_sel  = input_prev_joy1;
            input_prev_trig_sel = input_prev_trig1;
        end
        2'd2: begin
            input_cur_key_sel   = cont3_key_s;
            input_cur_joy_sel   = cont3_joy_s;
            input_cur_trig_sel  = cont3_trig_s;
            input_prev_key_sel  = input_prev_key2;
            input_prev_joy_sel  = input_prev_joy2;
            input_prev_trig_sel = input_prev_trig2;
        end
        default: begin
            input_cur_key_sel   = cont4_key_s;
            input_cur_joy_sel   = cont4_joy_s;
            input_cur_trig_sel  = cont4_trig_s;
            input_prev_key_sel  = input_prev_key3;
            input_prev_joy_sel  = input_prev_joy3;
            input_prev_trig_sel = input_prev_trig3;
        end
    endcase
end

wire [2:0] input_event_fields = {input_cur_trig_sel != input_prev_trig_sel,
                                 input_cur_joy_sel  != input_prev_joy_sel,
                                 input_cur_key_sel  != input_prev_key_sel};
wire [1:0] input_event_slot = input_scan_slot;
wire       input_event_valid = input_irq_mask[input_scan_slot] && (|input_event_fields);

wire        input_fifo_empty;
wire        input_fifo_full;
wire [4:0]  input_fifo_count;
wire [63:0] input_fifo_dout;
wire [63:0] input_fifo_din = {
    INPUT_EVENT_SLOT_CHANGE,
    6'b0, input_event_slot,
    5'b0, input_event_fields,
    8'h00,
    16'b0, input_seq
};
wire input_fifo_pop  = (state == S_PERIPH_RD) && can_push_beat && req_is_sysreg &&
                       req_addr[8] && (req_addr[7:2] == 6'd21) && !input_fifo_empty; // INPUT_FIFO_DATA1 (0x154)
wire input_fifo_push = input_event_valid && !input_fifo_full;
reg  input_fifo_clear;

sync_fifo #(
    .WIDTH(64),
    .DEPTH(16),
    .ADDR_WIDTH(4),
    .RAMSTYLE("MLAB, no_rw_check")
) input_event_fifo (
    .clk(clk),
    .reset(reset),
    .clear(input_fifo_clear),
    .push(input_fifo_push),
    .din(input_fifo_din),
    .pop(input_fifo_pop),
    .dout(input_fifo_dout),
    .empty(input_fifo_empty),
    .full(input_fifo_full),
    .count(input_fifo_count)
);

assign input_irq_pending = !input_fifo_empty || input_overflow || snac_hw_irq_pending;

always @(posedge clk) begin
    if (reset) begin
        input_irq_mask <= 4'b0;
        input_overflow <= 1'b0;
        input_seq <= 16'd0;
        input_scan_slot <= 2'd0;
        input_fifo_clear <= 1'b1;
    end else begin
        input_fifo_clear <= 1'b0;
        input_scan_slot <= input_scan_slot + 2'd1;

        if (sysreg_wr_fire && req_addr[8] && req_addr[7:2] == 6'd1) begin // INPUT_IRQ_MASK (0x104)
            input_irq_mask <= req_wdata[3:0];
            input_prev_key0 <= cont1_key_s;
            input_prev_key1 <= cont2_key_s;
            input_prev_key2 <= cont3_key_s;
            input_prev_key3 <= cont4_key_s;
            input_prev_joy0 <= cont1_joy_s;
            input_prev_joy1 <= cont2_joy_s;
            input_prev_joy2 <= cont3_joy_s;
            input_prev_joy3 <= cont4_joy_s;
            input_prev_trig0 <= cont1_trig_s;
            input_prev_trig1 <= cont2_trig_s;
            input_prev_trig2 <= cont3_trig_s;
            input_prev_trig3 <= cont4_trig_s;
        end

        if (sysreg_wr_fire && req_addr[8] && req_addr[7:2] == 6'd2) begin // INPUT_IRQ_CLEAR (0x108)
            if (req_wdata[0]) begin
                input_fifo_clear <= 1'b1;
                input_prev_key0 <= cont1_key_s;
                input_prev_key1 <= cont2_key_s;
                input_prev_key2 <= cont3_key_s;
                input_prev_key3 <= cont4_key_s;
                input_prev_joy0 <= cont1_joy_s;
                input_prev_joy1 <= cont2_joy_s;
                input_prev_joy2 <= cont3_joy_s;
                input_prev_joy3 <= cont4_joy_s;
                input_prev_trig0 <= cont1_trig_s;
                input_prev_trig1 <= cont2_trig_s;
                input_prev_trig2 <= cont3_trig_s;
                input_prev_trig3 <= cont4_trig_s;
            end
            if (req_wdata[3])
                input_overflow <= 1'b0;
        end else if (input_event_valid) begin
            if (input_fifo_full) begin
                input_overflow <= 1'b1;
            end else begin
                input_seq <= input_seq + 16'd1;
                case (input_event_slot)
                    2'd0: begin
                        input_prev_key0 <= cont1_key_s;
                        input_prev_joy0 <= cont1_joy_s;
                        input_prev_trig0 <= cont1_trig_s;
                    end
                    2'd1: begin
                        input_prev_key1 <= cont2_key_s;
                        input_prev_joy1 <= cont2_joy_s;
                        input_prev_trig1 <= cont2_trig_s;
                    end
                    2'd2: begin
                        input_prev_key2 <= cont3_key_s;
                        input_prev_joy2 <= cont3_joy_s;
                        input_prev_trig2 <= cont3_trig_s;
                    end
                    default: begin
                        input_prev_key3 <= cont4_key_s;
                        input_prev_joy3 <= cont4_joy_s;
                        input_prev_trig3 <= cont4_trig_s;
                    end
                endcase
            end
        end

        if (!input_irq_mask[0]) begin
            input_prev_key0 <= cont1_key_s;
            input_prev_joy0 <= cont1_joy_s;
            input_prev_trig0 <= cont1_trig_s;
        end
        if (!input_irq_mask[1]) begin
            input_prev_key1 <= cont2_key_s;
            input_prev_joy1 <= cont2_joy_s;
            input_prev_trig1 <= cont2_trig_s;
        end
        if (!input_irq_mask[2]) begin
            input_prev_key2 <= cont3_key_s;
            input_prev_joy2 <= cont3_joy_s;
            input_prev_trig2 <= cont3_trig_s;
        end
        if (!input_irq_mask[3]) begin
            input_prev_key3 <= cont4_key_s;
            input_prev_joy3 <= cont4_joy_s;
            input_prev_trig3 <= cont4_trig_s;
        end
    end
end

// ============================================
// System register write logic
// ============================================
reg sysreg_wr_fire;

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
        fb_swap_consumed_this_frame <= 1'b0;
        term_fb_active <= 1'b1;  // terminal FB visible by default at boot
        vrr_v_total_reg <= 10'd262;
        pal_wr <= 0;
        pal_addr <= 0;
        pal_data <= 0;
        pal_commit <= 0;
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
        irq_mask <= 6'b0;
        vsync_irq_pending <= 0;
        dataslot_irq_pending <= 0;
        save_dt_slot <= 0;
        save_dt_size <= 0;
        save_dt_commit <= 0;
        analogizer_cpu_wr_toggle <= 0;
        analogizer_cpu_wr_settings <= 0;
        analogizer_cpu_wr_hoffset <= 0;
        analogizer_cpu_wr_voffset <= 0;
        dt_query_addr <= 0;
        dt_query_toggle <= 0;
        snac_en_reg <= 0;
        snac_mode_reg <= 0;
        snac_div_reg <= 16'd499;  // default: 100KHz at 100MHz
        snac_tx_reg <= 0;
        snac_gpio_out_reg <= 8'hFF; // idle high
        snac_gpio_dir_reg <= 8'h03; // bits [1:0] output by default (OUT1, OUT2)
        snac_start_pulse <= 0;
        snac_bit_count_reg <= 0;
        snac_latch_en_reg <= 0;
        snac_hw_enable_reg <= 1'b0;
        snac_hw_analog_reg <= 1'b0;
        snac_hw_fast_reg <= 1'b0;
        snac_hw_clear_irq_pulse <= 1'b0;
        snac_hw_clear_edges_pulse <= 1'b0;
        /* mix_* reset lives in the main FSM always block
         * to avoid Quartus multi-driver errors (10028).  Verilator
         * tolerates split drivers if the conflict is provably
         * non-overlapping; Quartus does not. */
    end else begin
        cycle_counter <= cycle_counter + 1;
        pal_wr <= 0;
        pal_commit <= 0;
        snac_start_pulse <= 0;
        snac_hw_clear_irq_pulse <= 1'b0;
        snac_hw_clear_edges_pulse <= 1'b0;

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
                dataslot_irq_pending <= 1'b1;
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

        if (sysreg_wr_fire && !req_addr[8]) begin
            case (req_addr[7:2])
                6'd3: term_fb_active <= req_wdata[0];  // TERM_FB_CTRL
                6'd28: color_mode_reg <= req_wdata[2:0];  // offset 0x70
                6'd6: if (req_wdata[0]) begin
                    fb_ready_idx <= req_wdata[2:1];
                    fb_swap_pending <= 1'b1;
                end
                6'd8: ds_slot_id_reg <= req_wdata[15:0];
                6'd9: ds_slot_offset_reg <= req_wdata;
                6'd10: ds_bridge_addr_reg <= req_wdata;
                6'd11: ds_length_reg <= req_wdata;
                6'd12: ds_param_addr_reg <= req_wdata;
                6'd13: ds_resp_addr_reg <= req_wdata;
                6'd14: begin
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
                        dataslot_irq_pending <= 1'b0;
                    end
                end
                6'd15: begin  // DS_STATUS (0x3C) bit 7 W1C clears data-slot IRQ pending
                    if (req_wdata[7])
                        dataslot_irq_pending <= 1'b0;
                end
                6'd16: begin
                    pal_index_reg <= req_wdata[7:0];
                    pal_commit <= req_wdata[31];
                end
                6'd17: begin
                    pal_wr <= 1;
                    pal_addr <= pal_index_reg;
                    pal_data <= req_wdata[23:0];
                    pal_index_reg <= pal_index_reg + 1;
                end

                6'd18: save_dt_slot <= req_wdata[3:0];  // SAVE_DT_SLOT (0x48)
                6'd19: begin                               // SAVE_DT_SIZE (0x4C) — write triggers commit
                    save_dt_size <= req_wdata;
                    save_dt_commit <= ~save_dt_commit;
                end

                6'd32: begin  // ANALOGIZER_SETTINGS (0x80)
                    analogizer_cpu_wr_settings <= req_wdata;
                    analogizer_cpu_wr_toggle[0] <= ~analogizer_cpu_wr_toggle[0];
                end
                6'd33: begin  // ANALOGIZER_H_OFFSET (0x84)
                    analogizer_cpu_wr_hoffset <= req_wdata;
                    analogizer_cpu_wr_toggle[1] <= ~analogizer_cpu_wr_toggle[1];
                end
                6'd34: begin  // ANALOGIZER_V_OFFSET (0x88)
                    analogizer_cpu_wr_voffset <= req_wdata;
                    analogizer_cpu_wr_toggle[2] <= ~analogizer_cpu_wr_toggle[2];
                end

                // SNAC Shifter + GPIO registers (0xA0-0xAC)
                6'd40: begin  // SNAC_CTRL (0xA0)
                    snac_en_reg   <= req_wdata[7];
                    snac_mode_reg <= req_wdata[9:8];
                    if (req_wdata[7])
                        snac_hw_enable_reg <= 1'b0;
                    // Start a shift if bit[0] set and shifter not busy
                    if (req_wdata[0] && !snac_busy) begin
                        snac_start_pulse  <= 1;
                        snac_bit_count_reg <= req_wdata[5:1];
                        snac_latch_en_reg  <= req_wdata[6];
                    end
                end
                6'd41: snac_div_reg <= req_wdata[15:0];   // SNAC_DIV (0xA4)
                6'd42: snac_tx_reg  <= req_wdata;          // SNAC_DATA (0xA8)
                6'd43: begin                                // SNAC_GPIO (0xAC)
                    snac_gpio_out_reg <= req_wdata[7:0];
                    snac_gpio_dir_reg <= req_wdata[15:8];
                end

                6'd44: begin  // SYS_SHUTDOWN (0xB0)
                    shutdown_ack <= req_wdata[0];
                end

                // Hardware timer (0xB4-0xB8)
                6'd45: begin  // TIMER_PERIOD (0xB4)
                    timer_period <= req_wdata;
                    timer_counter <= req_wdata - 1;
                end
                6'd46: begin  // TIMER_CTRL (0xB8)
                    timer_enable <= req_wdata[0];
                    if (req_wdata[1]) timer_irq_pending <= 0;
                end

                // Datatable slot size query (0x90)
                6'd36: begin
                    dt_query_addr <= req_wdata[9:0];
                    dt_query_toggle <= ~dt_query_toggle;
                end


                6'd39: vsync_irq_pending <= 0;                    // VSYNC_IRQ_CLEAR (0x9C) W1C
                6'd55: vrr_v_total_reg <= clamp_v_total(req_wdata[9:0]); // VIDEO_VTOTAL (0xDC)
                6'd63: irq_mask <= req_wdata[5:0];             // IRQ_MASK (0xFC)

                default: ;
            endcase
        end

        if (sysreg_wr_fire && req_addr[8]) begin
            case (req_addr[7:2])
                6'd24: begin  // SNAC_HW_CTRL (0x160)
                    snac_hw_enable_reg <= req_wdata[0];
                    snac_hw_analog_reg <= req_wdata[1];
                    snac_hw_fast_reg   <= req_wdata[2];
                    if (req_wdata[0])
                        snac_en_reg <= 1'b0;
                end
                6'd31: begin  // SNAC_HW_CLEAR (0x17C)
                    snac_hw_clear_irq_pulse   <= req_wdata[0];
                    snac_hw_clear_edges_pulse <= req_wdata[1];
                end
                default: ;
            endcase
        end

        // Vsync IRQ — set on every vsync rising edge, cleared by W1C at 0x9C
        if (vsync_rising)
            vsync_irq_pending <= 1'b1;

        // Triple buffer swap. Vsync is the normal consume point; the early
        // vblank level lets a just-late frame catch the same refresh before
        // visible scanout starts.  Only one pending swap may be consumed per
        // display frame, so a new GPU CMD_FLIP arriving after a real vsync
        // consume waits for the next frame instead of skipping a frame that
        // was already presented.
        if (vsync_rising)
            fb_swap_consumed_this_frame <= 1'b0;
        if (fb_swap_pending && vsync_rising) begin
            fb_display_idx <= fb_ready_idx;
            fb_swap_pending <= 1'b0;
            fb_swap_consumed_this_frame <= 1'b1;
        end else if (fb_swap_pending && early_vblank_s &&
                     !fb_swap_consumed_this_frame) begin
            fb_display_idx <= fb_ready_idx;
            fb_swap_pending <= 1'b0;
            fb_swap_consumed_this_frame <= 1'b1;
        end

        // CMD_FLIP side-port — single-cycle pulse from gpu_core after
        // its m_wr_* drain completes. Placed LAST so its assignments
        // override the sysreg-path kernel write and consume path above
        // (later non-blocking wins on same-cycle conflicts).
        if (gpu_swap_req) begin
            fb_ready_idx    <= gpu_swap_idx;
            fb_swap_pending <= 1'b1;
        end

    end
end

wire        sysreg_input_page  = req_addr[8];
wire [5:0]  sysreg_word        = req_addr[7:2];
wire        input_slot_read    = sysreg_input_page
                               && (req_addr[7:4] >= 4'h1)
                               && (req_addr[7:4] <= 4'h4);
reg [31:0] input_slot_key_rdata;
reg [31:0] input_slot_joy_rdata;
reg [15:0] input_slot_trig_rdata;
reg        input_slot_irq_en_rdata;
reg [31:0] input_slot_rdata;

always @(*) begin
    case (req_addr[7:4])
        4'h1: begin
            input_slot_key_rdata    = cont1_key_s;
            input_slot_joy_rdata    = cont1_joy_s;
            input_slot_trig_rdata   = cont1_trig_s;
            input_slot_irq_en_rdata = input_irq_mask[0];
        end
        4'h2: begin
            input_slot_key_rdata    = cont2_key_s;
            input_slot_joy_rdata    = cont2_joy_s;
            input_slot_trig_rdata   = cont2_trig_s;
            input_slot_irq_en_rdata = input_irq_mask[1];
        end
        4'h3: begin
            input_slot_key_rdata    = cont3_key_s;
            input_slot_joy_rdata    = cont3_joy_s;
            input_slot_trig_rdata   = cont3_trig_s;
            input_slot_irq_en_rdata = input_irq_mask[2];
        end
        default: begin
            input_slot_key_rdata    = cont4_key_s;
            input_slot_joy_rdata    = cont4_joy_s;
            input_slot_trig_rdata   = cont4_trig_s;
            input_slot_irq_en_rdata = input_irq_mask[3];
        end
    endcase

    case (req_addr[3:2])
        2'd0: input_slot_rdata = {23'b0, input_slot_irq_en_rdata, 7'b0, 1'b1};
        2'd1: input_slot_rdata = input_slot_key_rdata;
        2'd2: input_slot_rdata = input_slot_joy_rdata;
        default: input_slot_rdata = {16'b0, input_slot_trig_rdata};
    endcase
end

// System register read mux
always @(*) begin
    sysreg_rdata = 32'h0;
    if (sysreg_input_page) begin
        // Input hub (0x100-0x158): raw APF slots + compact event FIFO.
        if (input_slot_read) begin
            sysreg_rdata = input_slot_rdata;
        end else begin
            case (sysreg_word)
                6'd0:  sysreg_rdata = {19'b0, input_fifo_count, 4'b0,
                                        input_overflow, input_fifo_full,
                                        input_fifo_empty, input_irq_pending}; // INPUT_STATUS
                6'd1:  sysreg_rdata = {28'b0, input_irq_mask};         // INPUT_IRQ_MASK
                6'd3:  sysreg_rdata = {16'b0, input_seq};              // INPUT_SEQ
                6'd20: sysreg_rdata = input_fifo_dout[31:0];           // INPUT_FIFO_DATA0
                6'd21: sysreg_rdata = input_fifo_dout[63:32];          // INPUT_FIFO_DATA1
                6'd22: sysreg_rdata = {27'b0, input_fifo_count};       // INPUT_FIFO_COUNT
                6'd24: sysreg_rdata = {snac_hw_debug_status, 14'b0,
                                        snac_hw_irq_pending,
                                        snac_hw_valid,
                                        5'b0,
                                        snac_hw_fast_reg,
                                        snac_hw_analog_reg,
                                        snac_hw_enable_reg};             // SNAC_HW_CTRL
                6'd25: sysreg_rdata = {16'b0, snac_hw_buttons};         // SNAC_HW_BUTTONS
                6'd26: sysreg_rdata = {16'b0, snac_hw_pressed};         // SNAC_HW_PRESSED
                6'd27: sysreg_rdata = {16'b0, snac_hw_released};        // SNAC_HW_RELEASED
                6'd28: sysreg_rdata = {snac_hw_ly, snac_hw_lx};         // SNAC_HW_JOY_L
                6'd29: sysreg_rdata = {snac_hw_ry, snac_hw_rx};         // SNAC_HW_JOY_R
                6'd30: sysreg_rdata = {snac_hw_debug_status, 8'b0,
                                        snac_hw_released[3:0],
                                        snac_hw_pressed[3:0],
                                        snac_hw_buttons[7:0]};           // SNAC_HW_DEBUG
                6'd32: sysreg_rdata = {16'b0, snac_hw_raw_buttons};     // SNAC_HW_RAW_BUTTONS
                default: ;
            endcase
        end
    end else begin
        case (sysreg_word)
            6'd0:  sysreg_rdata = {30'b0, dataslot_allcomplete_s, 1'b1};
            6'd1:  sysreg_rdata = cycle_counter[31:0];
            6'd2:  sysreg_rdata = cycle_counter[63:32];
            6'd3:  sysreg_rdata = {31'b0, term_fb_active};  // TERM_FB_CTRL
            6'd28: sysreg_rdata = {29'b0, color_mode_reg};
            6'd4:  sysreg_rdata = {7'b0, fb_display_addr_reg};
            6'd5:  sysreg_rdata = {30'b0, fb_display_idx};
            6'd6:  sysreg_rdata = {29'b0, fb_display_idx, fb_swap_pending};
            6'd8:  sysreg_rdata = {16'b0, ds_slot_id_reg};
            6'd9:  sysreg_rdata = ds_slot_offset_reg;
            6'd10: sysreg_rdata = ds_bridge_addr_reg;
            6'd11: sysreg_rdata = ds_length_reg;
            6'd12: sysreg_rdata = ds_param_addr_reg;
            6'd13: sysreg_rdata = ds_resp_addr_reg;
            6'd15: sysreg_rdata = {24'b0, dataslot_irq_pending, bridge_wr_idle,
                                    ~target_ack_s, ds_err_latched,
                                    ds_done_latched, ds_ack_latched};
            6'd16: sysreg_rdata = {pal_busy, 23'b0, pal_index_reg};
            // 0x44 (PAL_DATA write) has no meaningful read-back.
            6'd20: sysreg_rdata = cont1_key_s;
            6'd21: sysreg_rdata = cont1_joy_s;
            6'd22: sysreg_rdata = {16'b0, cont1_trig_s};
            6'd23: sysreg_rdata = cont2_key_s;
            6'd24: sysreg_rdata = cont2_joy_s;
            6'd25: sysreg_rdata = {16'b0, cont2_trig_s};
            6'd26: sysreg_rdata = app_id;
            6'd32: sysreg_rdata = analogizer_settings;             // ANALOGIZER_SETTINGS
            6'd33: sysreg_rdata = analogizer_hoffset;              // ANALOGIZER_H_OFFSET
            6'd34: sysreg_rdata = analogizer_voffset;              // ANALOGIZER_V_OFFSET
            6'd36: sysreg_rdata = {dt_query_valid, dt_query_data[30:0]};
            6'd37: sysreg_rdata = dt_query_data;                    // DT_QUERY_DATA full 32-bit result
            6'd38: sysreg_rdata = HW_FEATURES;                     // HW_FEATURES
            6'd39: sysreg_rdata = {31'b0, vsync_irq_pending};      // VSYNC_IRQ_PENDING
            // SNAC Shifter + GPIO registers (0xA0-0xAC)
            6'd40: sysreg_rdata = {22'b0, snac_mode_reg, snac_en_reg, 6'b0, snac_busy};
            6'd41: sysreg_rdata = {16'b0, snac_div_reg};
            6'd42: sysreg_rdata = snac_rx_data;
            6'd43: sysreg_rdata = {16'b0, snac_pin_dir, snac_pin_in_sync};
            // Shutdown handshake
            6'd44: sysreg_rdata = {31'b0, shutdown_pending};
            // Hardware timer
            6'd45: sysreg_rdata = timer_period;
            6'd46: sysreg_rdata = {30'b0, timer_irq_pending, timer_enable};
            6'd47: sysreg_rdata = timer_counter;
            // Live swap state. Legacy event counters read as zero.
            6'd48: sysreg_rdata = {12'b0,
                                    term_fb_active,
                                    2'b0,
                                    8'b0,
                                    4'b0,
                                    fb_ready_idx,
                                    fb_display_idx,
                                    fb_swap_pending};
            // Display timing live readback.
            6'd55: sysreg_rdata = {22'b0, vrr_v_total};
            6'd56: sysreg_rdata = 32'b0;  // swap hold retired
            6'd63: sysreg_rdata = {26'b0, irq_mask};
            default: ;
        endcase
    end
end

// ============================================
// Peripheral read data mux
// ============================================
// ============================================
// UART RX FIFO — 1024-byte buffer so CPU doesn't have to read every byte within 10μs.
// Pin to M10K: without the hint Quartus can fall back to MLAB under ALM pressure
// (each MLAB burns an ALM pair), so at 82% ALM util this 8 Kb array adds ~20-30
// ALMs needlessly.  One M10K block is trivially cheap here.
// ============================================
(* ramstyle = "M10K" *)
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
        if (state == S_PERIPH_RD && can_push_beat && req_is_uart && req_addr[3:2] == 2'b10 && !uart_rx_empty)
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
 *   0x04/0x08/0x0C/0x10: retired DMA registers, read as 0 */
wire [31:0] audio_rdata = (req_addr[4:2] == 3'd0) ? {21'b0, audio_fifo_full, audio_fifo_level} :
                          32'h0;

/* CRAM0 ownership mode read decode:
 *   0x00: {31'b0, cram0_mode} — current owner (0=bridge, 1=CPU). */
wire [31:0] cram0_mode_rdata = (req_addr[3:2] == 2'd0) ? {31'b0, cram0_mode} :
                               32'h0;

/* Mixer (0x4B) read decode.  The read mux fans in
 *   - control regs (group/master/voice_group/ctrl/irq)
 *   - per-voice POS readback
 * The per-voice POS path needs voice_sel_rd driven combinationally to
 * audio_mixer.v before this read mux fires, so the pos_latch[voice_sel_rd]
 * mux there has settled.  audio_mixer's pos_latch is a plain reg array,
 * so the indexed read is purely combinational — no extra cycle. */
assign mix_voice_sel_rd = req_addr[6:2];

wire mixer_per_voice_rd = (req_addr[11] == 1'b1) && (req_addr[7] == 1'b1);

reg [31:0] mixer_ctrl_rdata;
always @(*) begin
    case (req_addr[6:2])
        5'h00: mixer_ctrl_rdata = {24'b0, mix_master_vol};        // 0x800
        5'h01: mixer_ctrl_rdata = {24'b0, mix_group_vol_0};       // 0x804
        5'h02: mixer_ctrl_rdata = {24'b0, mix_group_vol_1};       // 0x808
        5'h03: mixer_ctrl_rdata = {24'b0, mix_group_vol_2};       // 0x80C
        5'h04: mixer_ctrl_rdata = {24'b0, mix_group_vol_3};       // 0x810
        5'h05: mixer_ctrl_rdata = mix_voice_group_packed[31:0];   // 0x814
        5'h06: mixer_ctrl_rdata = mix_voice_group_packed[63:32];  // 0x818
        5'h08: mixer_ctrl_rdata = {31'b0, mix_enable};            // 0x820
        5'h09: mixer_ctrl_rdata = mix_voice_end_pending;          // 0x824
        5'h0C: mixer_ctrl_rdata = mix_active_mask;                // 0x830
        default: mixer_ctrl_rdata = 32'h0;
    endcase
end

wire [31:0] mixer_rdata = mixer_per_voice_rd ? {10'b0, mix_pos_readback}
                                             : mixer_ctrl_rdata;

reg [31:0] periph_rd_data_r;

// ============================================
// FSM
// ============================================
localparam S_IDLE           = 3'd0;
localparam S_BRAM_RD        = 3'd1;
localparam S_PERIPH_RD      = 3'd2;
localparam S_PERIPH_WR      = 3'd3;
localparam S_PERIPH_RD_LATCH = 3'd4;
localparam S_WR_NEXT        = 3'd5;
localparam S_BRAM_WR        = 3'd6;
localparam S_PERIPH_RD_WAIT = 3'd7;

reg [2:0] state;

// Latched request fields
reg [31:0] req_addr;
reg [31:0] req_wdata;
reg [3:0]  req_wstrb;
reg [7:0]  burst_len;
reg [7:0]  burst_count;
// awburst latched at AW handshake.  00 = FIXED → req_addr is held for
// every beat of the burst (target one MMIO register repeatedly).
// 01 = INCR → req_addr += 4 per beat.
reg [1:0]  awburst_latched;
wire       burst_is_fixed = (awburst_latched == 2'b00);

// Region ID latched on accept.  A single decoded field keeps the read/write
// FSM from carrying a bank of parallel one-hot region registers through every
// MMIO path.
localparam [3:0] REGION_NONE   = 4'd0;
localparam [3:0] REGION_RAM    = 4'd1;
localparam [3:0] REGION_SYSREG = 4'd2;
localparam [3:0] REGION_AUDIO  = 4'd3;
localparam [3:0] REGION_CRAM0  = 4'd4;
localparam [3:0] REGION_LINK   = 4'd5;
localparam [3:0] REGION_UART   = 4'd6;
localparam [3:0] REGION_GPU    = 4'd7;
localparam [3:0] REGION_MIXER  = 4'd8;

reg [3:0] req_region;

wire req_is_ram    = (req_region == REGION_RAM);
wire req_is_sysreg = (req_region == REGION_SYSREG);
wire req_is_audio  = (req_region == REGION_AUDIO);
wire req_is_cram0  = (req_region == REGION_CRAM0);
wire req_is_link   = (req_region == REGION_LINK);
wire req_is_uart   = (req_region == REGION_UART);
wire req_is_gpu    = (req_region == REGION_GPU);
wire req_is_mixer  = (req_region == REGION_MIXER);

wire beat_is_last = (burst_count == burst_len);
wire [31:0] periph_next_addr = burst_is_fixed ? req_addr : (req_addr + 32'd4);

// Terminal moved to software; state 4 is reused as a peripheral read latch.

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
function [3:0] decode_region;
    input [31:0] addr;
    begin
        // BRAM: 32KB, 0x00000-0x07FFF.
        if (addr[31:15] == 17'b0) begin
            decode_region = REGION_RAM;
        // SYSREG: 0x40000000-0x400001FF.  The low page is the legacy
        // control block; bit 8 selects the input-hub page.
        end else if (addr[31:9] == 23'h200000) begin
            decode_region = REGION_SYSREG;
        end else begin
            case (addr[31:24])
                8'h48: decode_region = REGION_MIXER;
                8'h4A: decode_region = REGION_GPU;
                8'h4C: decode_region = REGION_AUDIO;
                8'h4D: decode_region = REGION_LINK;
                8'h4E: decode_region = REGION_CRAM0;
                8'h4F: decode_region = REGION_UART;
                default: decode_region = REGION_NONE;
            endcase
        end
    end
endfunction

wire [3:0] ar_region = decode_region(ar_addr);
wire [3:0] aw_region = decode_region(aw_addr);

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
        periph_rd_data_r <= 32'h0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_bvalid <= 0;
        s_axi_bresp <= 0;

        req_addr <= 0;
        req_wdata <= 0;
        req_wstrb <= 0;
        burst_len <= 0;
        burst_count <= 0;
        awburst_latched <= 2'b01;  // INCR by default

        req_region <= REGION_NONE;
        gpu_reg_wr <= 0;

        sysreg_wr_fire <= 0;
        cram0_mode <= 1'b0;     // bridge owns CRAM0 at reset
        uart_tx_fifo_we  <= 0;
        uart_tx_fifo_din <= 0;
        link_reg_wr <= 0;
        link_reg_rd <= 0;
        link_reg_addr <= 0;
        link_reg_wdata <= 0;

        /* Mixer (0x48) state — kept in this always block so all the
         * mix_* drivers live in one process (Quartus 10028). */
        mix_enable             <= 1'b0;
        mix_voice_wr           <= 1'b0;
        mix_voice_sel          <= 5'd0;
        mix_voice_field        <= 4'd0;
        mix_voice_wdata        <= 32'd0;
        mix_irq_clear_wr       <= 1'b0;
        mix_irq_clear          <= 32'd0;
        mix_master_vol         <= 8'hFF;
        mix_group_vol_0        <= 8'hFF;
        mix_group_vol_1        <= 8'hFF;
        mix_group_vol_2        <= 8'hFF;
        mix_group_vol_3        <= 8'hFF;
        mix_voice_group_packed <= 64'd0;

    end else begin
        // Defaults: deassert single-cycle pulses.  rvalid and bvalid
        // are NOT here — they are held until their corresponding
        // ready fires.  The drop-on-handshake block below clears
        // them on the cycle the master accepts the response.
        s_axi_arready <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;

        if (s_axi_rvalid && s_axi_rready) s_axi_rvalid <= 1'b0;
        if (s_axi_bvalid && s_axi_bready) s_axi_bvalid <= 1'b0;
        sysreg_wr_fire <= 0;
        uart_tx_fifo_we <= 0;
        link_reg_wr <= 0;
        link_reg_rd <= 0;
        gpu_reg_wr <= 0;
        mix_voice_wr     <= 1'b0;   // one-cycle pulse
        mix_irq_clear_wr <= 1'b0;   // one-cycle pulse

        case (state)

        // ============================================
        // IDLE: Accept AR (read) or AW+W (write)
        // ============================================
        S_IDLE: begin
            // Don't accept a new AR while the previous burst's last
            // beat is still draining — would smash held rvalid/rdata.
            if (s_axi_arvalid && !s_axi_rvalid) begin
                s_axi_arready <= 1;
                req_addr <= ar_addr;
                burst_len <= s_axi_arlen;
                burst_count <= 0;

                req_region <= ar_region;

                if (ar_region == REGION_RAM)
                    state <= S_BRAM_RD;
                else begin
                    /* All peripheral reads now stage through a local data
                     * register before driving AXI RDATA.  That breaks the
                     * large final periph read mux out of the AXI response
                     * path.  Mixer position readback still needs one extra
                     * latch cycle inside S_PERIPH_RD_WAIT because core_top
                     * registers the selected voice after req_addr changes. */
                    state <= S_PERIPH_RD_WAIT;
                    if (ar_region == REGION_LINK) begin
                        link_reg_addr <= ar_addr[6:2];
                        link_reg_rd <= 1;
                    end
                    if (ar_region == REGION_GPU) begin
                        gpu_reg_addr <= ar_addr[5:2];
                    end
                end

            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1;
                req_addr <= aw_addr;
                burst_len <= s_axi_awlen;
                burst_count <= 0;
                awburst_latched <= s_axi_awburst;

                req_region <= aw_region;

                if (s_axi_wvalid) begin
                    s_axi_wready <= 1;
                    req_wdata <= s_axi_wdata;
                    req_wstrb <= s_axi_wstrb;

                    if (aw_region == REGION_RAM)
                        state <= S_BRAM_WR;
                    else begin
                        state <= S_PERIPH_WR;
                        /* Peripheral side effects are applied centrally in
                         * S_PERIPH_WR.  This keeps the bundled AW+W path and
                         * later W-beat path from synthesizing duplicate
                         * decode/control cones. */
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
                if (!burst_is_fixed) req_addr <= req_addr + 32'd4;
                state <= S_WR_NEXT;
            end
        end

        // ============================================
        // Peripheral read — same hold-until-drain pattern
        // ============================================
        S_PERIPH_RD_WAIT: begin
            if (req_is_mixer || req_is_sysreg) begin
                state <= S_PERIPH_RD_LATCH;
            end else begin
                if (req_is_audio)
                    periph_rd_data_r <= audio_rdata;
                else if (req_is_cram0)
                    periph_rd_data_r <= cram0_mode_rdata;
                else if (req_is_link)
                    periph_rd_data_r <= link_reg_rdata;
                else if (req_is_uart)
                    periph_rd_data_r <= uart_rdata;
                else if (req_is_gpu)
                    periph_rd_data_r <= gpu_reg_rdata;
                else
                    periph_rd_data_r <= 32'h0;
                state <= S_PERIPH_RD;
            end
        end

        S_PERIPH_RD_LATCH: begin
            if (req_is_mixer)
                periph_rd_data_r <= mixer_rdata;
            else if (req_is_sysreg)
                periph_rd_data_r <= sysreg_rdata;
            else
                periph_rd_data_r <= 32'h0;
            state <= S_PERIPH_RD;
        end

        S_PERIPH_RD: begin
            if (can_push_beat) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rdata  <= periph_rd_data_r;
                s_axi_rresp  <= 2'b00;
                s_axi_rlast  <= beat_is_last;
                burst_count  <= burst_count + 1;
                if (beat_is_last) state <= S_IDLE;
                else begin
                    if (!burst_is_fixed)
                        req_addr <= periph_next_addr;
                    if (req_is_link)
                        link_reg_addr <= periph_next_addr[6:2];
                    if (req_is_gpu)
                        gpu_reg_addr <= periph_next_addr[5:2];
                    state <= S_PERIPH_RD_WAIT;
                end
                /* Pop-pulse side effects on specific read addresses. */
                // cram0_mode is a simple read-mirror; no pop needed.
            end
        end

        // ============================================
        // Peripheral write
        // ============================================
        S_PERIPH_WR: begin
            if (|req_wstrb) begin
                if (req_is_sysreg)
                    sysreg_wr_fire <= 1'b1;
                /* AUDIO_BASE writes retired in v2.  The HW mixer drives
                 * audio_output directly via MIX_* registers.  The address
                 * range is still accepted so legacy probes no-op rather than
                 * raising a bus fault. */
                if (req_is_cram0 && req_addr[3:2] == 2'd0)
                    cram0_mode <= req_wdata[0];
                if (req_is_link) begin
                    link_reg_wr    <= 1'b1;
                    link_reg_addr  <= req_addr[6:2];
                    link_reg_wdata <= req_wdata;
                end
                if (req_is_uart && req_addr[3:2] == 2'b01) begin
                    uart_tx_fifo_din <= req_wdata[7:0];
                    uart_tx_fifo_we  <= 1'b1;
                end
                if (req_is_gpu) begin
                    gpu_reg_wr    <= 1'b1;
                    gpu_reg_addr  <= req_addr[5:2];
                    gpu_reg_wdata <= req_wdata;
                end
                if (req_is_mixer) begin
                    if (req_addr[11] == 1'b0) begin
                        mix_voice_wr    <= 1'b1;
                        mix_voice_sel   <= req_addr[10:6];
                        mix_voice_field <= req_addr[5:2];
                        mix_voice_wdata <= req_wdata;
                    end else if (req_addr[7] == 1'b0) begin
                        case (req_addr[6:2])
                            5'h00: mix_master_vol         <= req_wdata[7:0];
                            5'h01: mix_group_vol_0        <= req_wdata[7:0];
                            5'h02: mix_group_vol_1        <= req_wdata[7:0];
                            5'h03: mix_group_vol_2        <= req_wdata[7:0];
                            5'h04: mix_group_vol_3        <= req_wdata[7:0];
                            5'h05: mix_voice_group_packed[31:0]  <= req_wdata;
                            5'h06: mix_voice_group_packed[63:32] <= req_wdata;
                            5'h08: mix_enable             <= req_wdata[0];
                            5'h09: begin
                                mix_irq_clear    <= req_wdata;
                                mix_irq_clear_wr <= 1'b1;
                            end
                            default: ;
                        endcase
                    end
                end
            end

            burst_count <= burst_count + 1;
            if (beat_is_last) begin
                s_axi_bvalid <= 1;
                s_axi_bresp <= 2'b00;
                state <= S_IDLE;
            end else begin
                // Keep req_addr stable through the next cycle so the delayed
                // sysreg_wr_fire pulse is observed with the address/data for
                // this beat.  S_WR_NEXT advances req_addr when it accepts the
                // following W beat.
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

                if (req_is_ram) begin
                    state <= S_BRAM_WR;
                end else begin
                    state <= S_PERIPH_WR;
                    if (!burst_is_fixed)
                        req_addr <= req_addr + 32'd4;
                end
            end
        end

        endcase
    end
end

endmodule
