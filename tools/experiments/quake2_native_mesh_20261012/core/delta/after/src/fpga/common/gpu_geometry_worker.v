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
    parameter QUEUE_BITS = ADDR_BITS,
    parameter INCLUDE_NATIVE_MESH = 0
) (
    input wire clk, reset_n, enable, soft_reset, ring_reset,
    input wire [ADDR_BITS-1:0] input_wrptr,
    output reg [ADDR_BITS-1:0] input_rdptr,
    output wire [ADDR_BITS-1:0] input_addr,
    input wire [31:0] input_data,
    input wire [ADDR_BITS-1:0] output_rdptr, output_addr,
    output reg [31:0] output_data,
    output reg [ADDR_BITS-1:0] output_wrptr,
    output wire busy,
    input wire mesh_reset_n,
    output wire mesh_arvalid,
    output wire [31:0] mesh_araddr,
    output wire [7:0] mesh_arlen,
    input wire mesh_arready, mesh_rvalid, mesh_rlast,
    input wire [31:0] mesh_rdata
);
localparam HEADER=0, SPACE=1, COPY=2, LOAD=3, MAC_ADDR=4,
    MAC_LOAD=5, MAC_WAIT=6, MAC_ADD=7, ROW=8, DIV_INIT=9, DIV_RUN=10,
    RX_START=11, RX_WAIT=12, RX_CAPTURE=13, SX_START=14, SX_WAIT=15,
    SX_ADD=16, SX_CLAMP=17, RY_START=18, RY_WAIT=19, RY_CAPTURE=20,
    SY_START=21, SY_WAIT=22, SY_ADD=23, SY_CLAMP=24, EMIT=25, COMMIT=26,
    CULL_A=27, CULL_B=28, CULL_C=29, CULL_D=30, CULL_E=31, CULL_F=32,
    CULL_G=33, CULL_H=34, CULL_I=35, CULL_J=36, TRI_HEADER=37, TRI_WORD=38,
    DECODE=39, N_START=40, N_READ=41, N_WAIT=42, N_CAPTURE=43,
    N_NEXT_VERTEX=44, N_MAC_ADDR=45, N_MAC_LOAD=46, N_MAC_LAUNCH=47,
    N_MAC_WAIT=48, N_MAC_ADD=49, N_MAC_OFFSET=50, N_MAC_ROW=51,
    N_PALETTE=52, N_PALETTE_WAIT=53, N_PALETTE_CAPTURE=54,
    N_NEXT_TRI=55, N_FINISH_MESH=56, N_OUTPUT_SPACE=57;
localparam NP_HEADER=0, NP_SOURCE=1, NP_U=2, NP_V=3,
    NP_OLD=4, NP_CURRENT=5, NP_TRI=6;
localparam PASS=0, OBJECT=1, RAW=2, CLIP=3, CULL=4, NATIVE=5;
reg [6:0] state;
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
// Native jobs keep immutable frame/mesh data outside the input ring. One
// extra M10K holds coefficients, palette and three independent eight-word
// fetch lines. Fetch ownership survives media reset until the physical burst
// drains. No extra multiplier or reciprocal unit is introduced.
(* ramstyle = "M10K" *) reg [31:0] native_mem [0:255];
reg signed [31:0] native_xc, native_yc, native_xs, native_ys, native_nearclip;
wire signed [31:0] active_xc = INCLUDE_NATIVE_MESH && mode == NATIVE ? native_xc : xc;
wire signed [31:0] active_yc = INCLUDE_NATIVE_MESH && mode == NATIVE ? native_yc : yc;
wire signed [31:0] active_xs = INCLUDE_NATIVE_MESH && mode == NATIVE ? native_xs : xs;
wire signed [31:0] active_ys = INCLUDE_NATIVE_MESH && mode == NATIVE ? native_ys : ys;
wire signed [31:0] active_nearclip = INCLUDE_NATIVE_MESH && mode == NATIVE ? native_nearclip : nearclip;
reg [7:0] native_addr;
reg [31:0] native_q;
reg [7:0] native_load_index;
reg [31:0] native_old_base, native_current_base, native_stream, native_end;
reg [31:0] native_source_count, native_meshes, native_flags;
reg [5:0] native_vertices, native_vertex;
reg [6:0] native_triangles, native_tri;
reg [31:0] native_old_word, native_current_word;
reg [2:0] native_column, native_purpose;
reg [6:0] native_resume;
reg [31:0] native_read_addr;
reg [1:0] native_read_bank;
reg [2:0] native_cache_valid;
reg [18:0] native_cache_tag [0:2];
wire native_hit = native_read_bank == 0
    ? native_cache_valid[0] && native_cache_tag[0] == native_read_addr[23:5]
    : native_read_bank == 1
    ? native_cache_valid[1] && native_cache_tag[1] == native_read_addr[23:5]
    : native_cache_valid[2] && native_cache_tag[2] == native_read_addr[23:5];
reg [1:0] native_tx_state, native_tx_bank;
reg [18:0] native_tx_tag;
reg [3:0] native_tx_word;
reg native_tx_discard;
wire native_client_reset = !reset_n || soft_reset || ring_reset;
wire native_fetch_start = INCLUDE_NATIVE_MESH && enable && !native_client_reset
    && state == N_READ && !native_hit && native_tx_state == 0;
assign mesh_arvalid = INCLUDE_NATIVE_MESH && native_tx_state == 1;
assign mesh_araddr = {8'd0,native_tx_tag,5'd0};
assign mesh_arlen = INCLUDE_NATIVE_MESH ? 8'd7 : 8'd0;
wire native_fill_write = INCLUDE_NATIVE_MESH && native_tx_state == 2
    && mesh_rvalid && !native_tx_discard && !native_client_reset
    && native_tx_word < 8;
wire [7:0] native_fill_addr = 8'd160 + {3'd0,native_tx_bank,3'd0}
                                      + {4'd0,native_tx_word};
// Frames cannot cross the physical chip boundary. Restrict counts before
// shifting; valid source vertices are four-byte native MD2 records.
wire native_job_valid = surface_valid && native_flags <= 2 && native_nearclip > 0
    && native_source_count != 0 && native_source_count <= 4096
    && native_meshes != 0 && native_meshes <= 4096
    && native_old_base[31:24] == 0 && native_current_base[31:24] == 0
    && native_stream[31:24] == 0 && native_end <= 32'h01000000
    && native_stream < native_end
    && native_old_base[1:0] == 0 && native_current_base[1:0] == 0
    && native_stream[1:0] == 0 && native_end[1:0] == 0
    && ({3'b0,native_old_base} + {1'b0,native_source_count,2'b0}) <= 35'h1000000
    && ({3'b0,native_current_base} + {1'b0,native_source_count,2'b0}) <= 35'h1000000;
always @(posedge clk) begin
    native_q <= native_mem[native_addr];
    if (native_fill_write) native_mem[native_fill_addr] <= mesh_rdata;
    else if (INCLUDE_NATIVE_MESH && take_input && state == COPY && mode == NATIVE) begin
        if (native_load_index >= 7 && native_load_index < 31)
            native_mem[native_load_index-7] <= input_data;
        else if (native_load_index >= 36)
            native_mem[native_load_index-4] <= input_data;
    end
end
always @(posedge clk) begin
    if (!mesh_reset_n || !INCLUDE_NATIVE_MESH) begin
        native_tx_state <= 0; native_tx_discard <= 0;
        native_tx_bank <= 0; native_tx_tag <= 0; native_tx_word <= 0;
        native_cache_valid <= 0;
    end else begin
        if (native_client_reset || (enable && state == N_START))
            native_cache_valid <= 0;
        if (native_client_reset && native_tx_state != 0)
            native_tx_discard <= 1;
        case (native_tx_state)
        0: if (native_fetch_start) begin
            native_tx_state <= 1; native_tx_bank <= native_read_bank;
            native_tx_tag <= native_read_addr[23:5]; native_tx_word <= 0;
            native_tx_discard <= 0;
        end
        1: if (mesh_arready) native_tx_state <= 2;
        2: if (mesh_rvalid) begin
            native_tx_word <= native_tx_word + 1'b1;
            if (mesh_rlast) begin
                native_tx_state <= 0;
                if (!native_tx_discard && !native_client_reset && native_tx_word == 7) begin
                    native_cache_valid[native_tx_bank] <= 1;
                    native_cache_tag[native_tx_bank] <= native_tx_tag;
                end
            end
        end
        default: native_tx_state <= 0;
        endcase
    end
end

wire input_empty = input_rdptr == input_wrptr;
wire [ADDR_BITS-1:0] used_words = write_ptr - output_rdptr;
wire [ADDR_BITS:0] free_words = (1 << QUEUE_BITS)-1 - {1'b0,used_words};
wire take_input = enable && !input_empty
    && ((state == HEADER) || (state == LOAD)
        || (state == COPY && (mode != PASS || free_words != 0)));
// Look ahead on consumption so the synchronous RAM delivers the next word
// on the following cycle. Empty cycles continue refreshing the current head.
assign input_addr = input_rdptr + {{(ADDR_BITS-1){1'b0}},take_input};
assign busy = !input_empty || state != HEADER
    || (INCLUDE_NATIVE_MESH && native_tx_state != 0);
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
        native_load_index <= 0; native_resume <= N_START;
        native_addr <= 0; native_read_addr <= 0; native_read_bank <= 0;
        native_purpose <= NP_HEADER; native_vertex <= 0; native_tri <= 0;
        native_column <= 0;
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
                if (INCLUDE_NATIVE_MESH && header[31:24] == 8'h60 && header[12:0] == 164) begin
                    mode <= NATIVE; needed <= 0; native_load_index <= 0;
                    native_resume <= N_START;
                end else if (header[31:24] == 8'h50 && header[12:0] == 26) begin
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
                if (INCLUDE_NATIVE_MESH && mode == NATIVE) begin
                    native_load_index <= native_load_index + 1'b1;
                    case (native_load_index)
                    0: native_old_base <= input_data;
                    1: native_current_base <= input_data;
                    2: native_stream <= input_data;
                    3: native_meshes <= input_data;
                    4: native_flags <= input_data;
                    5: native_source_count <= input_data;
                    6: native_end <= input_data;
                    31: native_xc <= input_data;
                    32: native_yc <= input_data;
                    33: native_xs <= input_data;
                    34: native_ys <= input_data;
                    35: native_nearclip <= input_data;
                    default: ;
                    endcase
                end
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
                divisor <= cw < active_nearclip ? active_nearclip : cw;
                remainder <= 0; quotient <= 39'h4000000000;
                iterations <= (mode == RAW || mode == NATIVE) ? 39 : 33; state <= DIV_RUN;
            end
            DIV_RUN: begin
                quotient <= next_quotient; remainder <= next_remainder;
                iterations <= iterations - 1'b1;
                if (iterations == 1) begin
                    zi <= (mode == RAW || mode == NATIVE) ? {6'd0,next_quotient[31:6]} : next_quotient[31:0];
                    if (mode == RAW || mode == NATIVE) depth <= next_quotient[31:0];
                    state <= RX_START;
                end
            end
            RX_START: begin mul_a <= cx; mul_b <= zi; state <= RX_WAIT; end
            RX_WAIT: state <= RX_CAPTURE;
            RX_CAPTURE: begin ratio <= product[47:16]; state <= SX_START; end
            SX_START: begin mul_a <= active_xs; mul_b <= ratio; state <= SX_WAIT; end
            SX_WAIT: state <= SX_ADD;
            SX_ADD: begin projected <= ($signed({{32{active_xc[31]}},active_xc}) <<< 4)
                                    + (product >>> 12); state <= SX_CLAMP; end
            SX_CLAMP: begin sx <= sat16(projected); state <= RY_START; end
            RY_START: begin mul_a <= cy; mul_b <= zi; state <= RY_WAIT; end
            RY_WAIT: state <= RY_CAPTURE;
            RY_CAPTURE: begin ratio <= product[47:16]; state <= SY_START; end
            SY_START: begin mul_a <= active_ys; mul_b <= ratio; state <= SY_WAIT; end
            SY_WAIT: state <= SY_ADD;
            SY_ADD: begin projected <= $signed({{32{active_yc[31]}},active_yc})
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
                state <= (INCLUDE_NATIVE_MESH && mode == NATIVE) ? native_resume
                       : mode == RAW && remaining != 0 ? SPACE : HEADER;
            end
            N_START: if (INCLUDE_NATIVE_MESH) begin
                if (native_job_valid) begin
                    native_read_addr <= native_stream; native_read_bank <= 0;
                    native_purpose <= NP_HEADER; state <= N_READ;
                end else state <= HEADER;
            end
            N_READ: if (INCLUDE_NATIVE_MESH && native_hit && native_tx_state == 0) begin
                native_addr <= 8'd160 + {3'd0,native_read_bank,3'd0}
                                      + {5'd0,native_read_addr[4:2]};
                state <= N_WAIT;
            end
            N_WAIT: state <= N_CAPTURE;
            N_CAPTURE: begin
                case (native_purpose)
                NP_HEADER: begin
                    // Validate the complete meshlet before any of its reads.
                    if (native_q[31:24] != 0 || native_q[7:0] == 0 || native_q[7:0] > 32
                        || native_q[23:8] == 0 || native_q[23:8] > 64
                        || ({1'b0,native_stream} + 33'd4
                            + ({25'd0,native_q[7:0]} * 12)
                            + ({17'd0,native_q[23:8]} << 2)) > {1'b0,native_end})
                        state <= HEADER;
                    else begin
                        native_vertices <= native_q[5:0];
                        native_triangles <= native_q[14:8];
                        native_vertex <= 0; native_tri <= 0;
                        native_stream <= native_stream + 4;
                        native_read_addr <= native_stream + 4;
                        native_purpose <= NP_SOURCE; state <= N_READ;
                    end
                end
                NP_SOURCE: begin
                    if (native_q >= native_source_count) state <= HEADER;
                    else begin
                        // Retain the current source word address until U/V load.
                        native_current_word <= native_q;
                        native_stream <= native_stream + 4;
                        native_read_addr <= native_stream + 4;
                        native_purpose <= NP_U; state <= N_READ;
                    end
                end
                NP_U: begin
                    u <= native_q; native_stream <= native_stream + 4;
                    native_read_addr <= native_stream + 4;
                    native_purpose <= NP_V; state <= N_READ;
                end
                NP_V: begin
                    v <= native_q; native_stream <= native_stream + 4;
                    native_read_addr <= native_old_base + (native_current_word << 2);
                    native_read_bank <= 1; native_purpose <= NP_OLD; state <= N_READ;
                end
                NP_OLD: begin
                    native_old_word <= native_q;
                    native_read_addr <= native_current_base + (native_current_word << 2);
                    native_read_bank <= 2; native_purpose <= NP_CURRENT; state <= N_READ;
                end
                NP_CURRENT: begin
                    native_current_word <= native_q;
                    row <= 0; native_column <= 0; state <= N_MAC_ADDR;
                end
                NP_TRI: begin
                    if (native_q[31:15] != 0 || {1'b0,native_q[4:0]} >= native_vertices
                        || {1'b0,native_q[9:5]} >= native_vertices
                        || {1'b0,native_q[14:10]} >= native_vertices) state <= HEADER;
                    else begin
                        triangle <= native_q | (native_flags << 15);
                        point_addr <= native_q[4:0];
                        native_stream <= native_stream + 4;
                        native_resume <= N_NEXT_TRI;
                        state <= native_flags == 0 ? TRI_HEADER : CULL_A;
                    end
                end
                default: state <= HEADER;
                endcase
            end
            N_MAC_ADDR: begin
                native_addr <= {3'd0,row,3'd0} + {5'd0,native_column};
                state <= N_MAC_LOAD;
            end
            N_MAC_LOAD: state <= N_MAC_LAUNCH;
            N_MAC_LAUNCH: begin
                mul_a <= native_q;
                case (native_column)
                0: mul_b <= {24'd0,native_old_word[7:0]};
                1: mul_b <= {24'd0,native_old_word[15:8]};
                2: mul_b <= {24'd0,native_old_word[23:16]};
                3: mul_b <= {24'd0,native_current_word[7:0]};
                4: mul_b <= {24'd0,native_current_word[15:8]};
                default: mul_b <= {24'd0,native_current_word[23:16]};
                endcase
                state <= N_MAC_WAIT;
            end
            N_MAC_WAIT: state <= N_MAC_ADD;
            N_MAC_ADD: begin
                accumulator <= native_column == 0 ? product[47:0] : accumulator + product[47:0];
                native_column <= native_column + 1'b1;
                if (native_column == 5) begin
                    native_addr <= {3'd0,row,3'd0} + 8'd6; state <= N_MAC_OFFSET;
                end else state <= N_MAC_ADDR;
            end
            N_MAC_OFFSET: state <= N_MAC_ROW;
            N_MAC_ROW: begin
                case (row)
                0: cx <= (accumulator >>> 16) + $signed(native_q);
                1: cy <= (accumulator >>> 16) + $signed(native_q);
                2: cw <= (accumulator >>> 16) + $signed(native_q);
                default: ;
                endcase
                row <= row + 1'b1; native_column <= 0;
                state <= row == 2 ? N_PALETTE : N_MAC_ADDR;
            end
            N_PALETTE: begin
                native_addr <= 8'd32 + {1'b0,native_current_word[31:25]};
                state <= N_PALETTE_WAIT;
            end
            N_PALETTE_WAIT: state <= N_PALETTE_CAPTURE;
            N_PALETTE_CAPTURE: begin
                rgb <= native_current_word[24] ? native_q[31:16] : native_q[15:0];
                slot <= native_vertex[4:0]; native_resume <= N_NEXT_VERTEX;
                state <= N_OUTPUT_SPACE;
            end
            N_OUTPUT_SPACE: if (free_words >= 7) state <= DIV_INIT;
            N_NEXT_VERTEX: begin
                native_vertex <= native_vertex + 1'b1;
                native_read_addr <= native_stream; native_read_bank <= 0;
                if (native_vertex + 1'b1 == native_vertices) begin
                    native_purpose <= NP_TRI;
                    // Reserve two output words before reading the index.
                    state <= N_NEXT_TRI;
                    native_tri <= 0;
                end else begin native_purpose <= NP_SOURCE; state <= N_READ; end
            end
            N_NEXT_TRI: begin
                if (native_tri == native_triangles) state <= N_FINISH_MESH;
                else if (free_words >= 2) begin
                    native_read_addr <= native_stream; native_read_bank <= 0;
                    native_purpose <= NP_TRI; native_tri <= native_tri + 1'b1;
                    state <= N_READ;
                end
            end
            N_FINISH_MESH: begin
                native_meshes <= native_meshes - 1'b1;
                if (native_meshes == 1) state <= HEADER;
                else if (native_stream >= native_end) state <= HEADER;
                else begin
                    native_read_addr <= native_stream; native_read_bank <= 0;
                    native_purpose <= NP_HEADER; state <= N_READ;
                end
            end
            default: state <= HEADER;
        endcase
    end
end
endmodule
`default_nettype wire
