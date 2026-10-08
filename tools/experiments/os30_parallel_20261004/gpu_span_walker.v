// Private prototype: consume already prepared edges, retaining the original
// center/corner sampling, modulo-2^32 DDA, scissor and span handshake rules.
// The geometry producer owns sorting, full 33-bit slopes and clip presteps.
// Packet words: xl, xr, long/top/bottom steps, bottom reseed x,
// {y_mid,y_cur}, {13'b0,subpixel,long_left,bottom,y_end}, {clip_x1,clip_x0}.
// Steps may drop bit 32 only AFTER full-width fractional presteps are formed.
`default_nettype none
module gpu_span_walker (
    input wire clk, reset_n, abort, start,
    input wire load,
    input wire [3:0] load_index,
    input wire [31:0] load_data,
    output reg busy, rec_valid,
    output reg signed [15:0] rec_u, rec_v,
    output reg [15:0] rec_count,
    input wire rec_ready
);
localparam IDLE=0, INIT=1, EMIT_A=2, EMIT_B=3, WAIT=4, STEP=5, DONE=6;
reg [2:0] state;
reg signed [31:0] xl, xr, slope_long, slope_top, slope_bot, bottom_seed;
reg signed [15:0] y_cur, y_mid, y_end, clip_x0, clip_x1;
reg subpix_y, long_left, in_bottom_half;
reg signed [15:0] span_u0_r, span_u1_r;
reg step_swap, step_end;
wire ceil_inc_l = subpix_y ? (xl[15] && |xl[14:0]) : |xl[15:0];
wire ceil_inc_r = subpix_y ? (xr[15] && |xr[14:0]) : |xr[15:0];
wire signed [15:0] ceil_xl = xl[31:16] + {15'd0,ceil_inc_l};
wire signed [15:0] ceil_xr = xr[31:16] + {15'd0,ceil_inc_r};
wire signed [16:0] span_w = {span_u1_r[15],span_u1_r} - {span_u0_r[15],span_u0_r};
wire [31:0] short_slope = in_bottom_half ? slope_bot : slope_top;
wire [31:0] next_xl = step_swap && !long_left ? bottom_seed
    : xl + (long_left ? slope_long : short_slope);
wire [31:0] next_xr = step_swap && long_left ? bottom_seed
    : xr + (long_left ? short_slope : slope_long);

always @(posedge clk) begin
    step_swap <= !in_bottom_half && (y_cur + 16'sd1 >= y_mid);
    step_end <= (y_cur + 16'sd1 >= y_end);
    if (!reset_n || abort) begin
        state <= IDLE; busy <= 0; rec_valid <= 0;
    end else begin
        // A packet is loaded only while the preceding triangle has retired.
        if (load && !busy) case (load_index)
            0: xl <= load_data;
            1: xr <= load_data;
            2: slope_long <= load_data;
            3: slope_top <= load_data;
            4: slope_bot <= load_data;
            5: bottom_seed <= load_data;
            6: begin y_cur <= load_data[15:0]; y_mid <= load_data[31:16]; end
            7: begin y_end <= load_data[15:0]; in_bottom_half <= load_data[16];
                     long_left <= load_data[17]; subpix_y <= load_data[18]; end
            8: begin clip_x0 <= load_data[15:0]; clip_x1 <= load_data[31:16]; end
            default: ;
        endcase
        case (state)
            IDLE: if (start) begin busy <= 1; state <= INIT; end
            INIT: state <= (y_cur < y_end && clip_x0 < clip_x1) ? EMIT_A : DONE;
            EMIT_A: begin
                span_u0_r <= ceil_xl < clip_x0 ? clip_x0 : ceil_xl;
                span_u1_r <= ceil_xr > clip_x1 ? clip_x1 : ceil_xr;
                state <= EMIT_B;
            end
            EMIT_B: if (span_w > 0) begin
                rec_u <= span_u0_r; rec_v <= y_cur; rec_count <= span_w[15:0];
                rec_valid <= 1; state <= WAIT;
            end else state <= STEP;
            WAIT: if (rec_ready) begin rec_valid <= 0; state <= STEP; end
            STEP: if (step_end) state <= DONE;
            else begin
                y_cur <= y_cur + 16'sd1;
                if (step_swap) in_bottom_half <= 1;
                xl <= next_xl; xr <= next_xr; state <= EMIT_A;
            end
            DONE: begin busy <= 0; state <= IDLE; end
            default: state <= IDLE;
        endcase
    end
end
endmodule
`default_nettype wire
