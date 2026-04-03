//
// AXI4 Peripheral Slave (openfpgaOS)
// Handles all local/peripheral accesses from the CPU:
//   - BRAM (64KB, burst reads for I-cache line fills)
//   - System registers (cycle counter, display, palette, dataslot, controllers)
//   - Terminal forwarding
//   - Audio/Link/OPL register dispatch
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

    // Terminal memory interface
    output wire        term_mem_valid,
    output wire [31:0] term_mem_addr,
    output wire [31:0] term_mem_wdata,
    output wire [3:0]  term_mem_wstrb,
    input wire  [31:0] term_mem_rdata,
    input wire         term_mem_ready,

    // Display control outputs
    output wire [1:0]  display_mode,
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

    // Audio output interface
    output reg         audio_sample_wr,
    output reg  [31:0] audio_sample_data,
    input wire  [7:0]  audio_fifo_level,
    input wire         audio_fifo_full,

    // Link MMIO interface
    output reg         link_reg_wr,
    output reg         link_reg_rd,
    output reg  [4:0]  link_reg_addr,
    output reg  [31:0] link_reg_wdata,
    input wire  [31:0] link_reg_rdata,

    // OPL hardware interface
    output reg         opl_write_req,
    output reg  [1:0]  opl_write_addr,
    output reg  [7:0]  opl_write_data,
    input wire         opl_ack,

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

    // Tile/sprite engines removed — stub outputs tied to 0
    output wire        tile_enable,    output wire        tile_priority,
    output wire [8:0]  tile_scroll_x,  output wire [7:0]  tile_scroll_y,
    output wire        tilemap_wr,     output wire [10:0] tilemap_waddr,
    output wire [15:0] tilemap_wdata,  output wire        tilechar_wr,
    output wire [10:0] tilechar_waddr, output wire [31:0] tilechar_wdata,
    output wire        sprite_enable,  output wire        sat_wr,
    output wire [5:0]  sat_idx,        output wire [1:0]  sat_field,
    output wire [31:0] sat_wdata,      output wire        sprchar_wr,
    output wire [10:0] sprchar_waddr,  output wire [31:0] sprchar_wdata,

    // Shutdown handshake
    input wire         shutdown_pending,
    output reg         shutdown_ack,

    // Audio DMA control
    output reg         adma_enable,
    output reg  [31:0] adma_ring_base,
    output reg  [12:0] adma_ring_size_log,
    output reg  [12:0] adma_ring_wptr,
    input  wire [12:0] adma_ring_rptr,

    // Audio DMA IRQ
    output reg         adma_irq_enable,
    output reg  [7:0]  adma_threshold,
    input  wire        adma_irq_pending,
    output reg         adma_irq_clear,

    // Datatable slot size query (toggle-based CDC to clk_74a)
    output reg  [9:0]  dt_query_addr,
    output reg         dt_query_toggle,   // toggles on each new query
    input  wire [31:0] dt_query_data,
    input  wire        dt_query_valid
);

wire reset = ~reset_n;

// ============================================
// Address decode (combinatorial, on AXI address channels)
// ============================================
wire [31:0] ar_addr = s_axi_araddr;
wire [31:0] aw_addr = s_axi_awaddr;

// Tile/sprite stub tie-offs (engines removed, rendering in software)
assign tile_enable = 0;  assign tile_priority = 0;
assign tile_scroll_x = 0; assign tile_scroll_y = 0;
assign tilemap_wr = 0; assign tilemap_waddr = 0; assign tilemap_wdata = 0;
assign tilechar_wr = 0; assign tilechar_waddr = 0; assign tilechar_wdata = 0;
assign sprite_enable = 0; assign sat_wr = 0;
assign sat_idx = 0; assign sat_field = 0; assign sat_wdata = 0;
assign sprchar_wr = 0; assign sprchar_waddr = 0; assign sprchar_wdata = 0;

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
assign term_mem_valid = term_pending;
assign term_mem_addr  = req_addr;
assign term_mem_wdata = req_wdata;
assign term_mem_wstrb = is_write ? req_wstrb : 4'b0;

// ============================================
// System registers
// ============================================
reg [31:0] sysreg_rdata;
reg [63:0] cycle_counter;
reg [1:0] display_mode_reg /* synthesis preserve */;
reg [2:0] color_mode_reg;

reg [15:0] ds_slot_id_reg;
reg [31:0] ds_slot_offset_reg;
reg [31:0] ds_bridge_addr_reg;
reg [31:0] ds_length_reg;
reg [31:0] ds_param_addr_reg;
reg [31:0] ds_resp_addr_reg;

reg [7:0] pal_index_reg;

// Triple-buffered framebuffer
localparam FB_ADDR_0 = 25'h0000000;
localparam FB_ADDR_1 = 25'h0080000;
localparam FB_ADDR_2 = 25'h0100000;
reg [1:0] fb_display_idx;
reg [1:0] fb_ready_idx;
reg fb_swap_pending;

wire [24:0] fb_display_addr_reg = (fb_display_idx == 2'd0) ? FB_ADDR_0 :
                                   (fb_display_idx == 2'd1) ? FB_ADDR_1 :
                                                              FB_ADDR_2;

assign display_mode = display_mode_reg;
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
        display_mode_reg <= 0;
        color_mode_reg <= 0;
        fb_display_idx <= 2'd0;
        fb_ready_idx <= 2'd0;
        fb_swap_pending <= 1'b0;
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
        adma_enable <= 0;
        adma_ring_base <= 0;
        adma_ring_size_log <= 0;
        adma_ring_wptr <= 0;
        adma_irq_enable <= 0;
        adma_threshold <= 0;
        adma_irq_clear <= 0;
        dt_query_addr <= 0;
        dt_query_toggle <= 0;
    end else begin
        cycle_counter <= cycle_counter + 1;
        pal_wr <= 0;
        save_dt_commit <= 0;
        adma_irq_clear <= 0;

        if (target_ack_s) begin
            target_dataslot_read <= 0;
            target_dataslot_write <= 0;
            target_dataslot_openfile <= 0;
        end

        // Latch ACK/DONE from bridge only during active command.
        // Guard: must see signal LOW before accepting HIGH, to prevent
        // capturing stale ACK/DONE from the previous command that
        // hasn't fully deasserted through the CDC yet.
        if (ds_cmd_active) begin
            if (!target_ack_s && !ds_ack_latched)
                ds_ack_seen_low <= 1;
            if (ds_ack_seen_low && target_ack_s && !ds_ack_latched)
                ds_ack_latched <= 1;
            if (!target_done_s && ds_ack_latched && !ds_done_latched)
                ds_done_seen_low <= 1;
            if (ds_done_seen_low && target_done_s && ds_ack_latched && !ds_done_latched) begin
                ds_done_latched <= 1;
                ds_err_latched <= target_err_s;
                ds_cmd_active <= 0;
            end
        end

        if (sysreg_wr_fire) begin
            case (req_addr[7:2])
                6'b000011: display_mode_reg <= req_wdata[1:0];
                6'b011100: color_mode_reg <= req_wdata[2:0];  // offset 0x70
                6'b000110: if (req_wdata[0]) begin
                    fb_ready_idx <= req_wdata[2:1];
                    fb_swap_pending <= 1'b1;
                end
                6'b001000: ds_slot_id_reg <= req_wdata[15:0];
                6'b001001: ds_slot_offset_reg <= req_wdata;
                6'b001010: ds_bridge_addr_reg <= req_wdata;
                6'b001011: ds_length_reg <= req_wdata;
                6'b001100: ds_param_addr_reg <= req_wdata;
                6'b001101: ds_resp_addr_reg <= req_wdata;
                6'b001110: begin
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
                6'b010000: pal_index_reg <= req_wdata[7:0];
                6'b010001: begin
                    pal_wr <= 1;
                    pal_addr <= pal_index_reg;
                    pal_data <= req_wdata[23:0];
                    pal_index_reg <= pal_index_reg + 1;
                end

                6'b010010: save_dt_slot <= req_wdata[3:0];  // SAVE_DT_SLOT (0x48)
                6'b010011: begin                               // SAVE_DT_SIZE (0x4C) — write triggers commit
                    save_dt_size <= req_wdata;
                    save_dt_commit <= 1;
                end

                // Tile/sprite registers (0x80-0xAC) — no-ops, engines removed
                // Writes to these addresses are silently ignored.

                6'b101100: begin  // SYS_SHUTDOWN (0xB0)
                    shutdown_ack <= req_wdata[0];
                end

                // Datatable slot size query (0x90)
                6'b100100: begin
                    dt_query_addr <= req_wdata[9:0];
                    dt_query_toggle <= ~dt_query_toggle;
                end


                // Audio DMA registers (0xE0-0xEC)
                6'b111000: adma_ring_base <= req_wdata;
                6'b111001: begin
                    adma_ring_size_log <= req_wdata[12:0];
                    adma_enable <= req_wdata[16];
                end
                6'b111010: adma_ring_wptr <= req_wdata[12:0];

                // Audio DMA IRQ registers (0xF0-0xF4)
                6'b111100: begin
                    adma_threshold <= req_wdata[7:0];
                    adma_irq_enable <= req_wdata[16];
                end
                6'b111101: adma_irq_clear <= req_wdata[0];

                default: ;
            endcase
        end

        // Triple buffer vsync swap
        if (fb_swap_pending && vsync_rising) begin
            fb_display_idx <= fb_ready_idx;
            fb_swap_pending <= 1'b0;
        end
    end
end

// System register read mux
always @(*) begin
    case (req_addr[7:2])
        6'b000000: sysreg_rdata = {30'b0, dataslot_allcomplete_s, 1'b1};
        6'b000001: sysreg_rdata = cycle_counter[31:0];
        6'b000010: sysreg_rdata = cycle_counter[63:32];
        6'b000011: sysreg_rdata = {30'b0, display_mode_reg};
        6'b011100: sysreg_rdata = {29'b0, color_mode_reg};
        6'b000100: sysreg_rdata = {7'b0, fb_display_addr_reg};
        6'b000101: sysreg_rdata = {30'b0, fb_display_idx};
        6'b000110: sysreg_rdata = {29'b0, fb_display_idx, fb_swap_pending};
        6'b001000: sysreg_rdata = {16'b0, ds_slot_id_reg};
        6'b001001: sysreg_rdata = ds_slot_offset_reg;
        6'b001010: sysreg_rdata = ds_bridge_addr_reg;
        6'b001011: sysreg_rdata = ds_length_reg;
        6'b001100: sysreg_rdata = ds_param_addr_reg;
        6'b001101: sysreg_rdata = ds_resp_addr_reg;
        6'b001110: sysreg_rdata = 32'h0;
        6'b001111: sysreg_rdata = {26'b0, ~target_ack_s, ds_err_latched, ds_done_latched, ds_ack_latched};
        6'b010000: sysreg_rdata = {24'b0, pal_index_reg};
        6'b010001: sysreg_rdata = 32'h0;
        6'b010100: sysreg_rdata = cont1_key_s;
        6'b010101: sysreg_rdata = cont1_joy_s;
        6'b010110: sysreg_rdata = {16'b0, cont1_trig_s};
        6'b010111: sysreg_rdata = cont2_key_s;
        6'b011000: sysreg_rdata = cont2_joy_s;
        6'b011001: sysreg_rdata = {16'b0, cont2_trig_s};
        6'b011010: sysreg_rdata = app_id;
        // Tile/sprite registers (readback) — return 0, engines removed
        // Shutdown handshake
        6'b101100: sysreg_rdata = {31'b0, shutdown_pending};  // SYS_SHUTDOWN (0xB0)
        // Audio DMA registers (0xE0-0xEC)
        6'b111000: sysreg_rdata = adma_ring_base;
        6'b111001: sysreg_rdata = {15'b0, adma_enable, 3'b0, adma_ring_size_log};
        6'b111010: sysreg_rdata = {19'b0, adma_ring_wptr};
        6'b111011: sysreg_rdata = {19'b0, adma_ring_rptr};
        // Datatable slot size query (0x90): bit 31 = valid, bits 30:0 = data
        6'b100100: sysreg_rdata = {dt_query_valid, dt_query_data[30:0]};
        // Audio DMA IRQ registers (0xF0-0xF4)
        6'b111100: sysreg_rdata = {15'b0, adma_irq_enable, 8'b0, adma_threshold};
        6'b111101: sysreg_rdata = {16'b0, 3'b0, audio_fifo_level, 4'b0, adma_irq_pending};
        default: sysreg_rdata = 32'h0;
    endcase
end

// ============================================
// Peripheral read data mux
// ============================================
// ============================================
// UART RX FIFO — 256-byte buffer so CPU doesn't have to read every byte within 10μs
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

// UART read data mux:
//   0x4F000000 (offset 0x00): status — bit0=1, bit1=TX ready, bit2=RX valid, [15:8]=FIFO count
//   0x4F000004 (offset 0x04): TX data (write-only, reads as 0)
//   0x4F000008 (offset 0x08): RX data (read pops from FIFO)
wire [31:0] uart_rdata = (req_addr[3:2] == 2'b00) ? {14'b0, uart_rx_count, 5'b0, uart_rx_valid, ~uart_tx_active, 1'b1} :
                          (req_addr[3:2] == 2'b10) ? {24'b0, uart_rx_data} :
                          32'h0;

wire [31:0] periph_rd_mux = reg_sysreg ? sysreg_rdata :
                             reg_audio  ? {23'b0, audio_fifo_full, audio_fifo_level} :
                             reg_link   ? link_reg_rdata :
                             reg_uart   ? uart_rdata :
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
localparam S_OPL_WAIT  = 3'd7;

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
reg reg_term;
reg reg_sysreg;
reg reg_audio;
reg reg_link;
reg reg_opm;
reg reg_uart;

wire beat_is_last = (burst_count == burst_len);

// Terminal pending flag
wire term_pending = (state == S_TERM);

// BRAM address mux (32KB: 13-bit word address = [14:2])
wire [12:0] bram_next_word = req_addr[14:2] + 13'd1;

always @(*) begin
    case (state)
        S_IDLE: begin
            if (s_axi_arvalid && !s_axi_awvalid)
                ram_addr_mux = ar_addr[14:2];
            else if (s_axi_awvalid)
                ram_addr_mux = aw_addr[14:2];
            else
                ram_addr_mux = 13'd0;
        end
        S_BRAM_RD: begin
            if (!beat_is_last)
                ram_addr_mux = bram_next_word;
            else
                ram_addr_mux = req_addr[14:2];
        end
        default: ram_addr_mux = req_addr[14:2];
    endcase
end

assign ram_wren = (state == S_BRAM_WR) && (|req_wstrb);

// ============================================
// Region decode helpers
// ============================================
wire ar_dec_ram    = (ar_addr[31:18] == 14'b0);  // 192KB: 0x00000-0x2FFFF
wire ar_dec_term   = (ar_addr[31:13] == 19'h10000);
wire ar_dec_sysreg = (ar_addr[31:8]  == 24'h400000);
wire ar_dec_audio  = (ar_addr[31:24] == 8'h4C);
wire ar_dec_link   = (ar_addr[31:24] == 8'h4D);
wire ar_dec_opm    = (ar_addr[31:24] == 8'h4E);
wire ar_dec_uart   = (ar_addr[31:24] == 8'h4F);

wire aw_dec_ram    = (aw_addr[31:18] == 14'b0);  // 192KB: 0x00000-0x2FFFF
wire aw_dec_term   = (aw_addr[31:13] == 19'h10000);
wire aw_dec_sysreg = (aw_addr[31:8]  == 24'h400000);
wire aw_dec_audio  = (aw_addr[31:24] == 8'h4C);
wire aw_dec_link   = (aw_addr[31:24] == 8'h4D);
wire aw_dec_opm    = (aw_addr[31:24] == 8'h4E);
wire aw_dec_uart   = (aw_addr[31:24] == 8'h4F);

// ============================================
// OPL write request tracking
// ============================================
reg opl_req_pending;  // Set when OPL write issued, cleared on opl_ack

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
        reg_term <= 0;
        reg_sysreg <= 0;
        reg_audio <= 0;
        reg_link <= 0;
        reg_opm <= 0;
        reg_uart <= 0;

        sysreg_wr_fire <= 0;
        audio_sample_wr <= 0;
        audio_sample_data <= 0;
        uart_tx_dv <= 0;
        uart_tx_byte <= 0;
        link_reg_wr <= 0;
        link_reg_rd <= 0;
        link_reg_addr <= 0;
        link_reg_wdata <= 0;

        opl_write_req <= 0;
        opl_write_addr <= 0;
        opl_write_data <= 0;
        opl_req_pending <= 0;
    end else begin
        // Defaults: deassert single-cycle pulses
        s_axi_arready <= 0;
        s_axi_rvalid <= 0;
        s_axi_awready <= 0;
        s_axi_wready <= 0;
        s_axi_bvalid <= 0;
        sysreg_wr_fire <= 0;
        audio_sample_wr <= 0;
        uart_tx_dv <= 0;
        link_reg_wr <= 0;
        link_reg_rd <= 0;

        // OPL ack clears request
        if (opl_ack) begin
            opl_write_req <= 0;
            opl_req_pending <= 0;
        end

        case (state)

        // ============================================
        // IDLE: Accept AR (read) or AW+W (write)
        // ============================================
        S_IDLE: begin
            if (s_axi_arvalid) begin
                s_axi_arready <= 1;
                is_write <= 0;
                req_addr <= ar_addr;
                burst_len <= s_axi_arlen;
                burst_count <= 0;

                reg_ram    <= ar_dec_ram;
                reg_term   <= ar_dec_term;
                reg_sysreg <= ar_dec_sysreg;
                reg_audio  <= ar_dec_audio;
                reg_link   <= ar_dec_link;
                reg_opm    <= ar_dec_opm;
                reg_uart   <= ar_dec_uart;

                if (ar_dec_ram)
                    state <= S_BRAM_RD;
                else if (ar_dec_term)
                    state <= S_TERM;
                else begin
                    state <= S_PERIPH_RD;
                    if (ar_dec_link) begin
                        link_reg_addr <= ar_addr[6:2];
                        link_reg_rd <= 1;
                    end
                end

            end else if (s_axi_awvalid) begin
                s_axi_awready <= 1;
                is_write <= 1;
                req_addr <= aw_addr;
                burst_len <= s_axi_awlen;
                burst_count <= 0;

                reg_ram    <= aw_dec_ram;
                reg_term   <= aw_dec_term;
                reg_sysreg <= aw_dec_sysreg;
                reg_audio  <= aw_dec_audio;
                reg_link   <= aw_dec_link;
                reg_opm    <= aw_dec_opm;
                reg_uart   <= aw_dec_uart;

                if (s_axi_wvalid) begin
                    s_axi_wready <= 1;
                    req_wdata <= s_axi_wdata;
                    req_wstrb <= s_axi_wstrb;

                    if (aw_dec_ram)
                        state <= S_BRAM_WR;
                    else if (aw_dec_term)
                        state <= S_TERM;
                    else if (aw_dec_opm && |s_axi_wstrb) begin
                        // OPL write: issue request and wait for ack
                        opl_write_req <= 1;
                        opl_write_addr <= aw_addr[3:2];
                        opl_write_data <= s_axi_wdata[7:0];
                        opl_req_pending <= 1;
                        state <= S_OPL_WAIT;
                    end else begin
                        state <= S_PERIPH_WR;
                        if (aw_dec_sysreg && |s_axi_wstrb)
                            sysreg_wr_fire <= 1;
                        if (aw_dec_audio && |s_axi_wstrb && aw_addr[3:2] == 2'b00) begin
                            audio_sample_wr <= 1;
                            audio_sample_data <= s_axi_wdata;
                        end
                        if (aw_dec_link && |s_axi_wstrb) begin
                            link_reg_wr <= 1;
                            link_reg_addr <= aw_addr[6:2];
                            link_reg_wdata <= s_axi_wdata;
                        end
                    end
                end else begin
                    state <= S_WR_NEXT;
                end
            end
        end

        // ============================================
        // BRAM read
        // ============================================
        S_BRAM_RD: begin
            s_axi_rvalid <= 1;
            s_axi_rdata <= ram_rdata;
            s_axi_rresp <= 2'b00;
            s_axi_rlast <= beat_is_last;
            burst_count <= burst_count + 1;
            if (beat_is_last) begin
                state <= S_IDLE;
            end else begin
                req_addr <= req_addr + 32'd4;
            end
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
        // Peripheral read
        // ============================================
        S_PERIPH_RD: begin
            s_axi_rvalid <= 1;
            s_axi_rdata <= periph_rd_mux;
            s_axi_rresp <= 2'b00;
            s_axi_rlast <= beat_is_last;
            burst_count <= burst_count + 1;
            if (beat_is_last) begin
                state <= S_IDLE;
            end else begin
                req_addr <= req_addr + 32'd4;
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

        // ============================================
        // Terminal: wait for ready
        // ============================================
        S_TERM: begin
            if (term_mem_ready) begin
                if (is_write) begin
                    burst_count <= burst_count + 1;
                    if (beat_is_last) begin
                        s_axi_bvalid <= 1;
                        s_axi_bresp <= 2'b00;
                        state <= S_IDLE;
                    end else begin
                        req_addr <= req_addr + 32'd4;
                        state <= S_WR_NEXT;
                    end
                end else begin
                    s_axi_rvalid <= 1;
                    s_axi_rdata <= term_mem_rdata;
                    s_axi_rresp <= 2'b00;
                    s_axi_rlast <= beat_is_last;
                    burst_count <= burst_count + 1;
                    if (beat_is_last) begin
                        state <= S_IDLE;
                    end else begin
                        req_addr <= req_addr + 32'd4;
                    end
                end
            end
        end

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
                end else if (reg_term) begin
                    state <= S_TERM;
                end else if (reg_opm && |s_axi_wstrb) begin
                    opl_write_req <= 1;
                    opl_write_addr <= req_addr[3:2];
                    opl_write_data <= s_axi_wdata[7:0];
                    opl_req_pending <= 1;
                    state <= S_OPL_WAIT;
                end else begin
                    state <= S_PERIPH_WR;
                    if (reg_sysreg && |s_axi_wstrb)
                        sysreg_wr_fire <= 1;
                    if (reg_audio && |s_axi_wstrb && req_addr[3:2] == 2'b00) begin
                        audio_sample_wr <= 1;
                        audio_sample_data <= s_axi_wdata;
                    end
                    if (reg_link && |s_axi_wstrb) begin
                        link_reg_wr <= 1;
                        link_reg_addr <= req_addr[6:2];
                        link_reg_wdata <= s_axi_wdata;
                    end
                    if (reg_uart && |s_axi_wstrb && req_addr[3:2] == 2'b01) begin
                        uart_tx_byte <= s_axi_wdata[7:0];
                        uart_tx_dv <= 1;
                    end
                end
            end
        end

        // ============================================
        // OPL_WAIT: Wait for OPL write to complete
        // ============================================
        S_OPL_WAIT: begin
            if (!opl_req_pending) begin
                // OPL write completed (opl_ack received)
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
        end

        endcase
    end
end

endmodule
