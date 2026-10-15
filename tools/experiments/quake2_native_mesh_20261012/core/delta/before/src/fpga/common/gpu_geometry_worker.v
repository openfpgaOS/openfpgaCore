// Independent os30 geometry command processor. The input ring remains CPU
// owned; a private output ring lets transforms run ahead of rasterization.
// Output words are published after storage; the rasterizer can stall on an
// incomplete payload. This allows bounded buffering even for long commands.
// 0x50 binds the local matrix/viewport, 0x59 transforms packed positions,
// 0x56 projects clip positions. Both loads produce internal 0x5C cache writes.
// 0x5B rejects backfaces before raster setup using the same screen determinant.
// Arithmetic deliberately matches gpu_core's existing fixed-point pipeline.
`default_nettype none
module gpu_geometry_worker #(
    parameter ADDR_BITS = 12,
    parameter QUEUE_BITS = ADDR_BITS
) (
    input wire clk, reset_n, enable, soft_reset, ring_reset,
    input wire [ADDR_BITS-1:0] input_wrptr,
    output reg [ADDR_BITS-1:0] input_rdptr,
    output wire [ADDR_BITS-1:0] input_addr,
    input wire [31:0] input_data,
    input wire [ADDR_BITS-1:0] output_rdptr, output_addr,
    output reg [31:0] output_data,
    output reg [ADDR_BITS-1:0] output_wrptr,
    output wire busy
);
localparam HEADER=0, SPACE=1, COPY=2, LOAD=3, MAC_ADDR=4,
    MAC_LOAD=5, MAC_WAIT=6, MAC_ADD=7, ROW=8, DIV_INIT=9, DIV_RUN=10,
    RX_START=11, RX_WAIT=12, RX_CAPTURE=13, SX_START=14, SX_WAIT=15,
    SX_ADD=16, SX_CLAMP=17, RY_START=18, RY_WAIT=19, RY_CAPTURE=20,
    SY_START=21, SY_WAIT=22, SY_ADD=23, SY_CLAMP=24, EMIT=25, COMMIT=26,
    CULL_A=27, CULL_B=28, CULL_C=29, CULL_D=30, CULL_E=31, CULL_F=32,
    CULL_G=33, CULL_H=34, CULL_I=35, CULL_J=36, TRI_HEADER=37, TRI_WORD=38,
    DECODE=39;
localparam PASS=0, OBJECT=1, RAW=2, CLIP=3, CULL=4;
reg [5:0] state;
reg [2:0] mode;
reg [31:0] header;
reg [12:0] remaining, needed;
reg [5:0] index;
reg [ADDR_BITS-1:0] write_ptr;
(* ramstyle = "M10K" *) reg [31:0] queue [0:(1<<QUEUE_BITS)-1];
(* ramstyle = "M10K" *) reg signed [31:0] matrix [0:15];
reg signed [31:0] matrix_q;
reg [1:0] row, column;
reg signed [31:0] xc, yc, xs, ys, nearclip;
reg signed [31:0] vx, vy, vz, cx, cy, cw;
reg [4:0] slot;
reg [15:0] rgb;
reg [31:0] u, v, depth, zi;
reg signed [31:0] mul_a, mul_b, ratio;
(* multstyle = "dsp" *) reg signed [63:0] product;
// Matrix output keeps only sum[47:16]. A 48-bit modular accumulator is
// therefore exact even when the full sum needs more bits. Winding needs 35.
reg signed [47:0] accumulator;
// A signed 32x32 product shifted by 12 plus a 32-bit viewport shifted by 4
// fits in 52 bits; retain all of them before saturating the screen position.
reg signed [51:0] projected;
reg signed [15:0] sx, sy;
reg [38:0] quotient;
reg [31:0] divisor, remainder;
reg [5:0] iterations;
reg surface_valid;
reg [31:0] triangle, point0, point_q;
(* ramstyle = "M10K" *) reg [31:0] points [0:31];
reg [4:0] point_addr;
reg signed [16:0] dx1, dy1;
wire signed [16:0] dx2 = $signed({point_q[15],point_q[15:0]})
                             - $signed({point0[15],point0[15:0]});
wire signed [16:0] dy2 = $signed({point_q[31],point_q[31:16]})
                             - $signed({point0[31],point0[31:16]});
wire signed [34:0] determinant = $signed(accumulator[34:0]) - $signed(product[34:0]);
wire [32:0] trial = {remainder, quotient[38]};
wire subtract = trial >= {1'b0, divisor};
wire [38:0] next_quotient = {quotient[37:0], subtract};
wire [31:0] next_remainder = subtract ? trial[31:0]-divisor : trial[31:0];
wire input_empty = input_rdptr == input_wrptr;
wire [ADDR_BITS-1:0] used_words = write_ptr - output_rdptr;
wire [ADDR_BITS:0] free_words = (1 << QUEUE_BITS)-1 - {1'b0,used_words};
wire take_input = enable && !input_empty
    && ((state == HEADER) || (state == LOAD)
        || (state == COPY && (mode != PASS || free_words != 0)));
// Look ahead on consumption so the synchronous RAM delivers the next word
// on the following cycle. Empty cycles continue refreshing the current head.
assign input_addr = input_rdptr + {{(ADDR_BITS-1){1'b0}},take_input};
assign busy = !input_empty || state != HEADER;
// Explicit finite decode: Quartus implements even an 8-bit "% 5" here as
// a long subtract chain. These are the only valid 1..32 vertex payloads.
function valid_raw;
    input [12:0] count;
    begin
        case (count)
            5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,
            85,90,95,100,105,110,115,120,125,130,135,140,145,150,155,160:
                valid_raw=1'b1;
            default: valid_raw=1'b0;
        endcase
    end
endfunction
function signed [15:0] sat16;
    input signed [63:0] value;
    begin
        sat16 = value < -64'sd32768 ? -16'sd32768
              : value > 64'sd32767 ? 16'sd32767 : value[15:0];
    end
endfunction

reg emit;
reg [31:0] emit_data;
// Only the write-enable path needs queue/producer availability. Keeping
// those conditions out of the data mux removes a ring-pointer -> queue-data
// timing path; data while emit=0 is unobservable and never written.
always @(*) begin
    emit = 1'b0;
    if (enable) case (state)
        SPACE: emit = free_words >= needed && mode == PASS;
        COPY: emit = !input_empty && mode == PASS && free_words != 0;
        EMIT, TRI_HEADER, TRI_WORD: emit = 1'b1;
        default: ;
    endcase
end
always @(*) begin
    emit_data = 32'd0;
    case (state)
        SPACE: emit_data = header;
        COPY: emit_data = input_data;
        EMIT: begin
            case (index)
                0: emit_data = 32'h5c000006;
                1: emit_data = {rgb, 11'd0, slot};
                2: emit_data = {sy, sx};
                3: emit_data = zi;
                4: emit_data = u;
                5: emit_data = v;
                6: emit_data = depth;
                default: emit_data = 0;
            endcase
        end
        TRI_HEADER: emit_data = 32'h54000001;
        TRI_WORD: emit_data = triangle;
        default: ;
    endcase
end
always @(posedge clk) begin
    output_data <= queue[output_addr[QUEUE_BITS-1:0]];
    if (emit && reset_n && !soft_reset && !ring_reset)
        queue[write_ptr[QUEUE_BITS-1:0]] <= emit_data;
    matrix_q <= matrix[{row,column}];
    point_q <= points[point_addr];
    if (emit && state == EMIT && index == 2 && surface_valid)
        points[slot] <= {sy,sx};
    if (take_input && state == COPY && mode == OBJECT && index < 12)
        matrix[index[3:0]] <= input_data;
    product <= mul_a * mul_b;
end
always @(posedge clk) begin
    if (!reset_n || soft_reset || ring_reset) begin
        input_rdptr <= (reset_n && soft_reset && !ring_reset) ? input_wrptr : 0;
        write_ptr <= 0; output_wrptr <= 0; state <= HEADER;
        mode <= PASS; index <= 0; remaining <= 0; needed <= 0;
        if (!reset_n || soft_reset) surface_valid <= 0;
        // A viewport must normally be bound before any vertex is submitted.
        if (!reset_n) begin xc <= 0; yc <= 0; xs <= 0; ys <= 0; nearclip <= 0; end
    end else if (enable) begin
        if (take_input) input_rdptr <= input_rdptr + 1'b1;
        if (emit) begin write_ptr <= write_ptr + 1'b1; output_wrptr <= write_ptr + 1'b1; end
        case (state)
            HEADER: if (!input_empty) begin
                header <= input_data;
                remaining <= input_data[12:0]; index <= 0; state <= DECODE;
            end
            DECODE: begin
                state <= SPACE;
                mode <= PASS; needed <= 1;
                if (header[31:24] == 8'h4a && (header[12:0] == 16 || header[12:0] == 17))
                    surface_valid <= 1'b1;
                if ((header[31:24] == 8'h48 && header[12:0] >= 11)
                    || (header[31:24] == 8'h4c && header[12:0] >= 9 && header[12:0] <= 24)
                    || (header[31:24] == 8'h49 && header[12:0] == 36))
                    surface_valid <= 1'b0;
                if (header[31:24] == 8'h50 && header[12:0] == 26) begin
                    mode <= OBJECT; needed <= 0;
                end else if (header[31:24] == 8'h59 && valid_raw(header[12:0])) begin
                    mode <= RAW;
                    // Publish each completed vertex separately. This permits
                    // 32-vertex batches without reserving an expanded batch
                    // in the output ring or waiting for all vertices to finish.
                    needed <= 7;
                end else if (header[31:24] == 8'h56 && header[12:0] == 8) begin
                    mode <= CLIP; needed <= 7;
                end else if (header[31:24] == 8'h5b && header[12:0] == 1) begin
                    mode <= CULL; needed <= 2;
                end
            end
            SPACE: if (free_words >= needed)
                state <= remaining == 0 ? COMMIT
                       : (mode == RAW || mode == CLIP || mode == CULL) ? LOAD : COPY;
            COPY: if (take_input) begin
                if (mode == OBJECT) case (index)
                    20: xc <= input_data;
                    21: yc <= input_data;
                    22: xs <= input_data;
                    23: ys <= input_data;
                    24: nearclip <= input_data;
                    default: ;
                endcase
                remaining <= remaining - 1'b1; index <= index + 1'b1;
                if (remaining == 1) state <= COMMIT;
            end
            LOAD: if (!input_empty) begin
                remaining <= remaining - 1'b1; index <= index + 1'b1;
                if (mode == CULL) begin
                    triangle <= input_data; point_addr <= input_data[4:0];
                    state <= (!surface_valid || input_data[16:15] == 3) ? COMMIT
                           : input_data[16:15] == 0 ? TRI_HEADER : CULL_A;
                end else if (mode == RAW) begin
                    case (index)
                        0: slot <= input_data[4:0];
                        1: begin vx <= {{16{input_data[15]}},input_data[15:0]};
                                 vy <= {{16{input_data[31]}},input_data[31:16]}; end
                        2: begin vz <= {{16{input_data[15]}},input_data[15:0]};
                                 rgb <= input_data[31:16]; end
                        3: u <= input_data;
                        4: begin v <= input_data; row <= 0; column <= 0; state <= MAC_ADDR; end
                        default: ;
                    endcase
                end else begin
                    case (index)
                        0: slot <= input_data[4:0];
                        1: cx <= input_data;
                        2: cy <= input_data;
                        3: cw <= input_data;
                        4: u <= input_data;
                        5: v <= input_data;
                        6: rgb <= input_data[15:0];
                        7: begin depth <= input_data; state <= DIV_INIT; end
                        default: ;
                    endcase
                end
            end
            MAC_ADDR: state <= MAC_LOAD;
            MAC_LOAD: begin
                if (column == 3) state <= ROW;
                else begin
                    mul_a <= matrix_q;
                    mul_b <= column == 0 ? vx : column == 1 ? vy : vz;
                    state <= MAC_WAIT;
                end
            end
            MAC_WAIT: state <= MAC_ADD;
            MAC_ADD: begin
                accumulator <= column == 0 ? product : accumulator + product;
                column <= column + 1'b1; state <= MAC_ADDR;
            end
            ROW: begin
                case (row)
                    0: cx <= (accumulator >>> 16) + matrix_q;
                    1: cy <= (accumulator >>> 16) + matrix_q;
                    2: cw <= (accumulator >>> 16) + matrix_q;
                    default: ;
                endcase
                row <= row+1'b1; column <= 0;
                state <= row == 2 ? DIV_INIT : MAC_ADDR;
            end
            DIV_INIT: begin
                divisor <= cw < nearclip ? nearclip : cw;
                remainder <= 0; quotient <= 39'h4000000000;
                iterations <= mode == RAW ? 39 : 33; state <= DIV_RUN;
            end
            DIV_RUN: begin
                quotient <= next_quotient; remainder <= next_remainder;
                iterations <= iterations - 1'b1;
                if (iterations == 1) begin
                    zi <= mode == RAW ? {6'd0,next_quotient[31:6]} : next_quotient[31:0];
                    if (mode == RAW) depth <= next_quotient[31:0];
                    state <= RX_START;
                end
            end
            RX_START: begin mul_a <= cx; mul_b <= zi; state <= RX_WAIT; end
            RX_WAIT: state <= RX_CAPTURE;
            RX_CAPTURE: begin ratio <= product[47:16]; state <= SX_START; end
            SX_START: begin mul_a <= xs; mul_b <= ratio; state <= SX_WAIT; end
            SX_WAIT: state <= SX_ADD;
            SX_ADD: begin projected <= ($signed({{32{xc[31]}},xc}) <<< 4)
                                    + (product >>> 12); state <= SX_CLAMP; end
            SX_CLAMP: begin sx <= sat16(projected); state <= RY_START; end
            RY_START: begin mul_a <= cy; mul_b <= zi; state <= RY_WAIT; end
            RY_WAIT: state <= RY_CAPTURE;
            RY_CAPTURE: begin ratio <= product[47:16]; state <= SY_START; end
            SY_START: begin mul_a <= ys; mul_b <= ratio; state <= SY_WAIT; end
            SY_WAIT: state <= SY_ADD;
            SY_ADD: begin projected <= $signed({{32{yc[31]}},yc})
                                    - (product >>> 16); state <= SY_CLAMP; end
            SY_CLAMP: begin sy <= sat16(projected); index <= 0; state <= EMIT; end
            EMIT: begin
                index <= index + 1'b1;
                if (index == 6) begin
                    index <= 0;
                    state <= COMMIT;
                end
            end
            CULL_A: state <= CULL_B;
            CULL_B: begin point0 <= point_q; point_addr <= triangle[9:5]; state <= CULL_C; end
            CULL_C: state <= CULL_D;
            CULL_D: begin dx1 <= dx2; dy1 <= dy2; point_addr <= triangle[14:10]; state <= CULL_E; end
            CULL_E: state <= CULL_F;
            CULL_F: begin mul_a <= dx1; mul_b <= dy2; vx <= dx2; state <= CULL_G; end
            CULL_G: state <= CULL_H;
            CULL_H: begin accumulator <= product; mul_a <= vx; mul_b <= dy1; state <= CULL_I; end
            CULL_I: state <= CULL_J;
            CULL_J: state <= (determinant == 0
                            || (triangle[15] && determinant[34])
                            || (triangle[16] && !determinant[34])) ? COMMIT : TRI_HEADER;
            TRI_HEADER: state <= TRI_WORD;
            TRI_WORD: state <= COMMIT;
            COMMIT: begin
                output_wrptr <= write_ptr;
                state <= mode == RAW && remaining != 0 ? SPACE : HEADER;
            end
            default: state <= HEADER;
        endcase
    end
end
endmodule
`default_nettype wire
