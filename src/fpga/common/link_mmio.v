//
// link_mmio.v
//
// MMIO link peripheral for Pocket link-cable transport.
//
// Register map (word offsets):
//   0x00 ID         RO  0x4C4E4B31 ("LNK1")
//   0x04 VER        RO  capability/version bits
//   0x08 STATUS     RO  [0]=link_up [1]=peer_present [2]=tx_full [3]=rx_empty
//                        [4]=rx_crc_err [5]=rx_overflow [6]=tx_overflow [7]=desync
//   0x0C CTRL       WO  [0]=enable [1]=reset [2]=clear_err [3]=flush_rx [4]=flush_tx
//                        [5]=master [6]=poll (master idle clocking)
//   0x10 TX_DATA    WO  push one 32-bit word
//   0x14 RX_DATA    RO  pop one 32-bit word
//   0x18 TX_SPACE   RO  number of free TX words (low 16 bits)
//   0x1C RX_COUNT   RO  number of queued RX words (low 16 bits)
//
// Physical transport:
// - Uses SCK as synchronous bit clock.
// - Uses SO (output) / SI (input) for full-duplex serial data.
// - Each transfer slot is 33 bits: {valid, data[31:0]} MSB-first.
// - Received word is enqueued only when peer valid bit is 1.
//
`default_nettype none

module link_mmio #(
    parameter integer CLK_HZ     = 72000000,
    parameter integer SCK_HZ     = 2000000,
    parameter integer POLL_HZ    = 8000,
    parameter integer FIFO_DEPTH = 64
) (
    input  wire        clk,
    input  wire        reset_n,

    input  wire        reg_wr,
    input  wire        reg_rd,
    input  wire [4:0]  reg_addr,
    input  wire [31:0] reg_wdata,
    output reg  [31:0] reg_rdata,

    input  wire        link_si_i,
    output reg         link_so_o,
    output wire        link_so_oe,
    input  wire        link_sck_i,
    output reg         link_sck_o,
    output wire        link_sck_oe,
    input  wire        link_sd_i,
    output wire        link_sd_o,
    output wire        link_sd_oe
);

function integer clog2;
    input integer value;
    integer v;
    begin
        v = value - 1;
        clog2 = 0;
        while (v > 0) begin
            v = v >> 1;
            clog2 = clog2 + 1;
        end
    end
endfunction

localparam [31:0] LINK_ID_CONST  = 32'h4C4E4B31; // "LNK1"
localparam [31:0] LINK_VER_CONST = 32'h0001_0007; // sync + valid-slot + master/poll

localparam [4:0] ADDR_ID       = 5'd0;
localparam [4:0] ADDR_VER      = 5'd1;
localparam [4:0] ADDR_STATUS   = 5'd2;
localparam [4:0] ADDR_CTRL     = 5'd3;
localparam [4:0] ADDR_TX_DATA  = 5'd4;
localparam [4:0] ADDR_RX_DATA  = 5'd5;
localparam [4:0] ADDR_TX_SPACE = 5'd6;
localparam [4:0] ADDR_RX_COUNT = 5'd7;

localparam integer FIFO_AW = (FIFO_DEPTH <= 2) ? 1 : clog2(FIFO_DEPTH);

localparam integer HALF_DIV_I = (SCK_HZ > 0) ? (CLK_HZ / (SCK_HZ * 2)) : 1;
localparam integer HALF_DIV = (HALF_DIV_I < 1) ? 1 : HALF_DIV_I;
localparam integer HALF_DIV_W = (HALF_DIV <= 1) ? 1 : clog2(HALF_DIV);
localparam [31:0] HALF_DIV_M1 = HALF_DIV - 1;

localparam integer POLL_GAP_I = (POLL_HZ > 0) ? (CLK_HZ / POLL_HZ) : 1;
localparam integer POLL_GAP = (POLL_GAP_I < 1) ? 1 : POLL_GAP_I;
localparam integer POLL_GAP_W = (POLL_GAP <= 1) ? 1 : clog2(POLL_GAP);
localparam [31:0] POLL_GAP_M1 = POLL_GAP - 1;

localparam integer DESYNC_TIMEOUT_I = HALF_DIV * 16;
localparam integer DESYNC_TIMEOUT = (DESYNC_TIMEOUT_I < 8) ? 8 : DESYNC_TIMEOUT_I;
localparam integer DESYNC_W = (DESYNC_TIMEOUT <= 1) ? 1 : clog2(DESYNC_TIMEOUT);
localparam [31:0] DESYNC_M1 = DESYNC_TIMEOUT - 1;

localparam integer PEER_TIMEOUT_I = CLK_HZ / 2;
localparam integer PEER_TIMEOUT = (PEER_TIMEOUT_I < 1) ? 1 : PEER_TIMEOUT_I;
localparam integer PEER_W = (PEER_TIMEOUT <= 1) ? 1 : clog2(PEER_TIMEOUT + 1);
localparam [31:0] PEER_TIMEOUT_32 = PEER_TIMEOUT;

localparam [31:0] FIFO_DEPTH_32 = FIFO_DEPTH;
localparam [FIFO_AW:0] FIFO_DEPTH_COUNT = FIFO_DEPTH_32[FIFO_AW:0];
localparam [15:0] FIFO_DEPTH_16 = FIFO_DEPTH_32[15:0];

reg ctrl_enable;
reg ctrl_master;
reg ctrl_poll;

reg err_rx_crc;
reg err_rx_overflow;
reg err_tx_overflow;
reg err_desync;

reg [31:0] tx_fifo [0:FIFO_DEPTH-1];
reg [FIFO_AW-1:0] tx_wr_ptr;
reg [FIFO_AW-1:0] tx_rd_ptr;
reg [FIFO_AW:0]   tx_count;

reg [31:0] rx_fifo [0:FIFO_DEPTH-1];
reg [FIFO_AW-1:0] rx_wr_ptr;
reg [FIFO_AW-1:0] rx_rd_ptr;
reg [FIFO_AW:0]   rx_count;

reg [PEER_W-1:0] peer_timer;

reg [2:0] sck_sync;
wire sck_rise = (sck_sync[2:1] == 2'b01);
wire sck_fall = (sck_sync[2:1] == 2'b10);

reg slot_active;
reg slot_phase_high; // master phase: 0=low, 1=high
reg [5:0] slot_bit_idx; // 32..0
reg [32:0] tx_slot_shift;
reg [32:0] rx_slot_shift;

reg [HALF_DIV_W-1:0] clk_div_ctr;
reg [POLL_GAP_W-1:0] poll_gap_ctr;
reg [DESYNC_W-1:0] desync_ctr;

wire tx_empty = (tx_count == 0);
wire tx_full  = (tx_count == FIFO_DEPTH_COUNT);
wire rx_empty = (rx_count == 0);
wire rx_full  = (rx_count == FIFO_DEPTH_COUNT);

wire [15:0] tx_space_words = FIFO_DEPTH_16 - {{(16-(FIFO_AW+1)){1'b0}}, tx_count};
wire [15:0] rx_count_words = {{(16-(FIFO_AW+1)){1'b0}}, rx_count};

wire peer_present = (peer_timer != 0);

wire [31:0] status_word = {
    24'd0,
    err_desync,
    err_tx_overflow,
    err_rx_overflow,
    err_rx_crc,
    rx_empty,
    tx_full,
    peer_present,
    ctrl_enable
};

assign link_so_oe  = ctrl_enable;
assign link_sck_oe = ctrl_enable & ctrl_master;
assign link_sd_o   = 1'b0;
assign link_sd_oe  = 1'b0;

always @(*) begin
    reg_rdata = 32'd0;
    case (reg_addr)
        ADDR_ID:       reg_rdata = LINK_ID_CONST;
        ADDR_VER:      reg_rdata = LINK_VER_CONST;
        ADDR_STATUS:   reg_rdata = status_word;
        ADDR_CTRL:     reg_rdata = {26'd0, ctrl_poll, ctrl_master, 3'd0, ctrl_enable};
        ADDR_RX_DATA:  reg_rdata = rx_empty ? 32'd0 : rx_fifo[rx_rd_ptr];
        ADDR_TX_SPACE: reg_rdata = {16'd0, tx_space_words};
        ADDR_RX_COUNT: reg_rdata = {16'd0, rx_count_words};
        default:       reg_rdata = 32'd0;
    endcase
end

always @(posedge clk or negedge reset_n) begin : p_link
    reg tx_cpu_push;
    reg tx_slot_pop;
    reg rx_cpu_pop;
    reg rx_slot_push;
    reg [31:0] tx_cpu_data;
    reg [31:0] rx_slot_data;
    reg ctrl_soft_reset;
    reg ctrl_flush_tx;
    reg ctrl_flush_rx;
    reg ctrl_clear_err;
    reg role_change;
    reg rx_can_push;
    reg start_master_slot;

    if (!reset_n) begin
        ctrl_enable <= 1'b0;
        ctrl_master <= 1'b0;
        ctrl_poll <= 1'b0;

        err_rx_crc <= 1'b0;
        err_rx_overflow <= 1'b0;
        err_tx_overflow <= 1'b0;
        err_desync <= 1'b0;

        tx_wr_ptr <= {FIFO_AW{1'b0}};
        tx_rd_ptr <= {FIFO_AW{1'b0}};
        tx_count <= {(FIFO_AW+1){1'b0}};

        rx_wr_ptr <= {FIFO_AW{1'b0}};
        rx_rd_ptr <= {FIFO_AW{1'b0}};
        rx_count <= {(FIFO_AW+1){1'b0}};

        peer_timer <= {PEER_W{1'b0}};

        sck_sync <= 3'b000;
        slot_active <= 1'b0;
        slot_phase_high <= 1'b0;
        slot_bit_idx <= 6'd0;
        tx_slot_shift <= 33'd0;
        rx_slot_shift <= 33'd0;

        clk_div_ctr <= {HALF_DIV_W{1'b0}};
        poll_gap_ctr <= {POLL_GAP_W{1'b0}};
        desync_ctr <= {DESYNC_W{1'b0}};

        link_so_o <= 1'b0;
        link_sck_o <= 1'b0;
    end else begin
        // Defaults for this cycle's FIFO operations.
        tx_cpu_push = 1'b0;
        tx_slot_pop = 1'b0;
        rx_cpu_pop = 1'b0;
        rx_slot_push = 1'b0;
        tx_cpu_data = 32'd0;
        rx_slot_data = 32'd0;

        ctrl_soft_reset = 1'b0;
        ctrl_flush_tx = 1'b0;
        ctrl_flush_rx = 1'b0;
        ctrl_clear_err = 1'b0;
        role_change = 1'b0;
        start_master_slot = 1'b0;

        // Synchronize incoming SCK for slave edge detection.
        sck_sync <= {sck_sync[1:0], link_sck_i};

        // Peer activity timeout.
        if (peer_timer != 0)
            peer_timer <= peer_timer - 1'b1;

        // CPU register interface side effects.
        if (reg_wr && reg_addr == ADDR_CTRL) begin
            ctrl_soft_reset = reg_wdata[1];
            ctrl_flush_rx = reg_wdata[3];
            ctrl_flush_tx = reg_wdata[4];
            ctrl_clear_err = reg_wdata[2];
            role_change = (ctrl_master != reg_wdata[5]);

            if (reg_wdata[1]) begin
                ctrl_enable <= 1'b0;
                ctrl_master <= 1'b0;
                ctrl_poll <= 1'b0;
            end else begin
                ctrl_enable <= reg_wdata[0];
                ctrl_master <= reg_wdata[5];
                ctrl_poll <= reg_wdata[6];
            end
        end

        if (reg_wr && reg_addr == ADDR_TX_DATA) begin
            if (!tx_full && !(ctrl_soft_reset || ctrl_flush_tx)) begin
                tx_cpu_push = 1'b1;
                tx_cpu_data = reg_wdata;
            end else if (!ctrl_soft_reset && !ctrl_flush_tx) begin
                err_tx_overflow <= 1'b1;
            end
        end

        if (reg_rd && reg_addr == ADDR_RX_DATA && !rx_empty && !(ctrl_soft_reset || ctrl_flush_rx))
            rx_cpu_pop = 1'b1;

        if (ctrl_soft_reset) begin
            err_rx_crc <= 1'b0;
            err_rx_overflow <= 1'b0;
            err_tx_overflow <= 1'b0;
            err_desync <= 1'b0;

            tx_wr_ptr <= {FIFO_AW{1'b0}};
            tx_rd_ptr <= {FIFO_AW{1'b0}};
            tx_count <= {(FIFO_AW+1){1'b0}};
            rx_wr_ptr <= {FIFO_AW{1'b0}};
            rx_rd_ptr <= {FIFO_AW{1'b0}};
            rx_count <= {(FIFO_AW+1){1'b0}};

            peer_timer <= {PEER_W{1'b0}};

            slot_active <= 1'b0;
            slot_phase_high <= 1'b0;
            slot_bit_idx <= 6'd0;
            tx_slot_shift <= 33'd0;
            rx_slot_shift <= 33'd0;
            clk_div_ctr <= {HALF_DIV_W{1'b0}};
            poll_gap_ctr <= {POLL_GAP_W{1'b0}};
            desync_ctr <= {DESYNC_W{1'b0}};

            link_so_o <= 1'b0;
            link_sck_o <= 1'b0;
        end else begin
            if (ctrl_clear_err) begin
                err_rx_crc <= 1'b0;
                err_rx_overflow <= 1'b0;
                err_tx_overflow <= 1'b0;
                err_desync <= 1'b0;
            end

            if (ctrl_flush_tx) begin
                tx_wr_ptr <= {FIFO_AW{1'b0}};
                tx_rd_ptr <= {FIFO_AW{1'b0}};
                tx_count <= {(FIFO_AW+1){1'b0}};
            end
            if (ctrl_flush_rx) begin
                rx_wr_ptr <= {FIFO_AW{1'b0}};
                rx_rd_ptr <= {FIFO_AW{1'b0}};
                rx_count <= {(FIFO_AW+1){1'b0}};
            end

            if (!ctrl_enable || role_change || ctrl_flush_tx || ctrl_flush_rx) begin
                slot_active <= 1'b0;
                slot_phase_high <= 1'b0;
                slot_bit_idx <= 6'd0;
                tx_slot_shift <= 33'd0;
                rx_slot_shift <= 33'd0;
                clk_div_ctr <= {HALF_DIV_W{1'b0}};
                desync_ctr <= {DESYNC_W{1'b0}};
                link_sck_o <= 1'b0;
                if (!ctrl_enable)
                    link_so_o <= 1'b0;
            end

            if (ctrl_enable) begin
                if (ctrl_master) begin
                    // Master: generate SCK and transfer slots.
                    if (!slot_active) begin
                        if (!tx_empty) begin
                            start_master_slot = 1'b1;
                            tx_slot_shift <= {1'b1, tx_fifo[tx_rd_ptr]};
                            tx_slot_pop = 1'b1;
                            link_so_o <= 1'b1;
                        end else if (ctrl_poll && !rx_full) begin
                            if (poll_gap_ctr == 0) begin
                                start_master_slot = 1'b1;
                                tx_slot_shift <= {1'b0, 32'd0};
                                link_so_o <= 1'b0;
                                poll_gap_ctr <= POLL_GAP_M1[POLL_GAP_W-1:0];
                            end else begin
                                poll_gap_ctr <= poll_gap_ctr - 1'b1;
                            end
                        end

                        if (start_master_slot) begin
                            slot_active <= 1'b1;
                            slot_phase_high <= 1'b0;
                            slot_bit_idx <= 6'd32;
                            rx_slot_shift <= 33'd0;
                            clk_div_ctr <= {HALF_DIV_W{1'b0}};
                            link_sck_o <= 1'b0;
                        end
                    end else begin
                        if (clk_div_ctr == HALF_DIV_M1[HALF_DIV_W-1:0]) begin
                            clk_div_ctr <= {HALF_DIV_W{1'b0}};
                            if (!slot_phase_high) begin
                                // Rising edge: sample SI.
                                slot_phase_high <= 1'b1;
                                link_sck_o <= 1'b1;
                                rx_slot_shift[slot_bit_idx] <= link_si_i;
                            end else begin
                                // Falling edge: advance bit/output.
                                slot_phase_high <= 1'b0;
                                link_sck_o <= 1'b0;
                                if (slot_bit_idx == 0) begin
                                    slot_active <= 1'b0;
                                    link_so_o <= 1'b0;

                                    if (rx_slot_shift[32]) begin
                                        rx_can_push = (!rx_full) || rx_cpu_pop;
                                        if (rx_can_push) begin
                                            rx_slot_push = 1'b1;
                                            rx_slot_data = rx_slot_shift[31:0];
                                            peer_timer <= PEER_TIMEOUT_32[PEER_W-1:0];
                                        end else begin
                                            err_rx_overflow <= 1'b1;
                                        end
                                    end
                                end else begin
                                    slot_bit_idx <= slot_bit_idx - 1'b1;
                                    link_so_o <= tx_slot_shift[slot_bit_idx - 1'b1];
                                end
                            end
                        end else begin
                            clk_div_ctr <= clk_div_ctr + 1'b1;
                        end
                    end
                end else begin
                    // Slave: consume externally-driven SCK edges.
                    link_sck_o <= 1'b0;

                    if (!slot_active) begin
                        if (!tx_empty) begin
                            tx_slot_shift <= {1'b1, tx_fifo[tx_rd_ptr]};
                            link_so_o <= 1'b1;
                        end else begin
                            tx_slot_shift <= {1'b0, 32'd0};
                            link_so_o <= 1'b0;
                        end
                    end

                    if (slot_active) begin
                        if (desync_ctr == DESYNC_M1[DESYNC_W-1:0]) begin
                            slot_active <= 1'b0;
                            err_desync <= 1'b1;
                            link_so_o <= 1'b0;
                        end else begin
                            desync_ctr <= desync_ctr + 1'b1;
                        end
                    end

                    if (sck_rise) begin
                        desync_ctr <= {DESYNC_W{1'b0}};
                        if (!slot_active) begin
                            slot_active <= 1'b1;
                            slot_bit_idx <= 6'd32;
                            rx_slot_shift <= 33'd0;
                            rx_slot_shift[32] <= link_si_i;
                            if (!tx_empty)
                                tx_slot_pop = 1'b1;
                        end else begin
                            rx_slot_shift[slot_bit_idx] <= link_si_i;
                        end
                    end

                    if (sck_fall && slot_active) begin
                        desync_ctr <= {DESYNC_W{1'b0}};
                        if (slot_bit_idx == 0) begin
                            slot_active <= 1'b0;
                            if (rx_slot_shift[32]) begin
                                rx_can_push = (!rx_full) || rx_cpu_pop;
                                if (rx_can_push) begin
                                    rx_slot_push = 1'b1;
                                    rx_slot_data = rx_slot_shift[31:0];
                                    peer_timer <= PEER_TIMEOUT_32[PEER_W-1:0];
                                end else begin
                                    err_rx_overflow <= 1'b1;
                                end
                            end

                            // Preload valid bit for the next slot.
                            if (!tx_empty && !tx_slot_pop)
                                link_so_o <= 1'b1;
                            else
                                link_so_o <= 1'b0;
                        end else begin
                            slot_bit_idx <= slot_bit_idx - 1'b1;
                            link_so_o <= tx_slot_shift[slot_bit_idx - 1'b1];
                        end
                    end
                end
            end else begin
                link_sck_o <= 1'b0;
                link_so_o <= 1'b0;
            end

            // TX FIFO write/pop.
            if (tx_cpu_push)
                tx_fifo[tx_wr_ptr] <= tx_cpu_data;

            if (tx_cpu_push)
                tx_wr_ptr <= tx_wr_ptr + 1'b1;
            if (tx_slot_pop)
                tx_rd_ptr <= tx_rd_ptr + 1'b1;
            case ({tx_cpu_push, tx_slot_pop})
                2'b10: tx_count <= tx_count + 1'b1;
                2'b01: tx_count <= tx_count - 1'b1;
                default: ;
            endcase

            // RX FIFO push/pop.
            if (rx_slot_push)
                rx_fifo[rx_wr_ptr] <= rx_slot_data;

            if (rx_slot_push)
                rx_wr_ptr <= rx_wr_ptr + 1'b1;
            if (rx_cpu_pop)
                rx_rd_ptr <= rx_rd_ptr + 1'b1;
            case ({rx_slot_push, rx_cpu_pop})
                2'b10: rx_count <= rx_count + 1'b1;
                2'b01: rx_count <= rx_count - 1'b1;
                default: ;
            endcase
        end
    end
end

// Keep SD input referenced (currently unused in protocol).
wire _unused_ok = &{1'b0, link_sd_i};

endmodule
