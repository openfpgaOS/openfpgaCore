//
// SNAC Shift Register / SPI Master
// Minimal hardware for jitter-free serial transfers to SNAC controllers.
// Replaces all per-protocol FSMs (~445 ALMs) with a generic ~35 ALM shifter.
//
// Supports two modes via `mode`:
//   00 = Config A (NES/SNES/DB15): CLK on pin_out[0], LATCH on pin_out[1],
//        data sampled from miso_a (bank0[4]/IO3)
//   01 = Config B (PSX):          CLK on pin_out[6], CMD shifted out on pin_out[7],
//        data sampled from miso_b (bank0[7]/IN4)
//
// Operation: write TX data + config, assert `start`.  Hardware shifts `bit_count`
// bits at the configured clock rate, then asserts `done` for one cycle.
// CPU polls `busy` or uses the done pulse.
//

`default_nettype none

module snac_shifter (
    input  wire        clk,
    input  wire        reset_n,

    // Control interface (directly from register logic)
    input  wire        start,       // pulse to begin transfer
    input  wire [4:0]  bit_count,   // bits-1 (0 = 1 bit, 31 = 32 bits)
    input  wire        latch_en,    // pulse LATCH/OUT2 before shifting
    input  wire [1:0]  mode,        // 00=Config A, 01=Config B
    input  wire [15:0] clk_div,     // half-period = (clk_div+1) clocks

    // Data
    input  wire [31:0] tx_data,     // MOSI shift-out register (loaded on start)
    output reg  [31:0] rx_data,     // MISO shift-in register

    // Status
    output wire        busy,
    output reg         done,        // single-cycle pulse when complete

    // Pin interface — directly drives/samples SNAC GPIO pins
    // Active only when busy; otherwise GPIO register drives pins.
    output reg         shift_clk,   // goes to pin_out[0] (CfgA) or pin_out[2] (CfgB)
    output reg         shift_mosi,  // goes to pin_out[7] (CfgB CMD); unused in CfgA
    output reg         shift_latch, // goes to pin_out[1] (CfgA LATCH)
    input  wire        miso_a,      // from pin_in[2]  (CfgA: IO3/bank0[4])
    input  wire        miso_b       // from pin_in[5]  (CfgB: IN4/bank0[7])
);

// FSM states
localparam S_IDLE       = 3'd0;
localparam S_LATCH_HIGH = 3'd1;
localparam S_LATCH_LOW  = 3'd2;
localparam S_CLK_LOW    = 3'd3;
localparam S_CLK_HIGH   = 3'd4;
localparam S_DONE       = 3'd5;

reg [2:0]  state;
reg [15:0] div_cnt;     // clock divider counter
reg [4:0]  bits_left;   // bits remaining
reg [4:0]  total_bits;  // original bits-1, used to left-align RX data
reg [31:0] tx_sr;       // TX shift register
reg        miso_sel;    // 0=miso_a, 1=miso_b

assign busy = (state != S_IDLE);

wire miso = miso_sel ? miso_b : miso_a;
wire clk_idle = miso_sel; // Config B / PSX idles clock high.

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state       <= S_IDLE;
        div_cnt     <= 0;
        bits_left   <= 0;
        total_bits   <= 0;
        tx_sr       <= 0;
        rx_data     <= 0;
        shift_clk   <= 0;
        shift_mosi  <= 0;
        shift_latch <= 0;
        miso_sel    <= 0;
        done        <= 0;
    end else begin
        done <= 0;

        case (state)
        S_IDLE: begin
            shift_clk  <= mode[0];
            shift_latch <= 0;
            if (start) begin
                tx_sr     <= tx_data;
                rx_data   <= 0;
                bits_left <= bit_count;
                total_bits <= bit_count;
                miso_sel  <= mode[0];
                // Drive MOSI with MSB immediately (for PSX CMD)
                shift_mosi <= tx_data[31];
                if (latch_en) begin
                    // Pulse latch high first
                    shift_latch <= 1;
                    div_cnt     <= clk_div;
                    state       <= S_LATCH_HIGH;
                end else begin
                    // Go straight to shifting
                    shift_clk <= 0;
                    div_cnt   <= clk_div;
                    state     <= S_CLK_LOW;
                end
            end
        end

        S_LATCH_HIGH: begin
            // Wait for half-period with latch high
            if (div_cnt == 0) begin
                shift_latch <= 0;
                div_cnt     <= clk_div;
                state       <= S_LATCH_LOW;
            end else begin
                div_cnt <= div_cnt - 16'd1;
            end
        end

        S_LATCH_LOW: begin
            // Wait for half-period with latch low, then start clocking
            if (div_cnt == 0) begin
                div_cnt <= clk_div;
                state   <= S_CLK_LOW;
            end else begin
                div_cnt <= div_cnt - 16'd1;
            end
        end

        S_CLK_LOW: begin
            // CLK is low — wait half-period, sample MISO on rising edge
            shift_clk <= 0;
            if (div_cnt == 0) begin
                // Rising edge: sample MISO, shift into rx_data
                rx_data   <= {rx_data[30:0], miso};
                shift_clk <= 1;
                // Shift TX: next bit out
                tx_sr      <= {tx_sr[30:0], 1'b0};
                shift_mosi <= tx_sr[30]; // next bit (after shift)
                div_cnt    <= clk_div;
                state      <= S_CLK_HIGH;
            end else begin
                div_cnt <= div_cnt - 16'd1;
            end
        end

        S_CLK_HIGH: begin
            // CLK is high — wait half-period
            shift_clk <= 1;
            if (div_cnt == 0) begin
                if (bits_left == 0) begin
                    shift_clk <= clk_idle;
                    rx_data   <= rx_data << (5'd31 - total_bits);
                    state     <= S_DONE;
                end else begin
                    bits_left <= bits_left - 5'd1;
                    div_cnt   <= clk_div;
                    shift_clk <= 0;
                    state     <= S_CLK_LOW;
                end
            end else begin
                div_cnt <= div_cnt - 16'd1;
            end
        end

        S_DONE: begin
            done  <= 1;
            shift_clk <= clk_idle;
            state <= S_IDLE;
        end

        default: state <= S_IDLE;
        endcase
    end
end

endmodule
