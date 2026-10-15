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
    parameter INCLUDE_NATIVE_MESH = 0,
    parameter INCLUDE_PARTICLES = 0
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
    N_NEXT_TRI=55, N_FINISH_MESH=56, N_OUTPUT_SPACE=57,
    N_WAIT_Q=58, N_MAC_WAIT_Q=59, N_MAC_OFFSET_Q=60, N_PALETTE_WAIT_Q=61,
    N_HEADER_BOUND=62, N_HEADER_CHECK=63,
    P_CONTEXT_ADDR=64, P_CONTEXT_WAIT=65, P_CONTEXT_CAPTURE=66,
    P_START=67, P_ALPHA_SPACE=68, P_ALPHA_HEADER=69, P_ALPHA_WORD=70,
    P_VERTEX=71, P_BASIS_ADDR=72, P_BASIS_WAIT=73, P_BASIS_CAPTURE=74,
    P_MUL_WAIT=75, P_MUL_CAPTURE=76, P_PROJECT_SPACE=77,
    P_NEXT_VERTEX=78, P_TRI_SPACE=79, P_FINISH=80, P_BASIS_CAPTURE_Q=81, P_MUL_CAPTURE_Q=82;
localparam NP_HEADER=0, NP_SOURCE=1, NP_U=2, NP_V=3,
    NP_OLD=4, NP_CURRENT=5, NP_TRI=6;
localparam PASS=0, OBJECT=1, RAW=2, CLIP=3, CULL=4, NATIVE=5,
    PARTICLE_CONTEXT=6, PARTICLE=7;
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
reg signed [63:0] projected;
reg signed [15:0] sx, sy;
localparam QUOTIENT_BITS = INCLUDE_PARTICLES ? 47 : 39;
reg [QUOTIENT_BITS-1:0] quotient;
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
wire [32:0] trial = {remainder, quotient[QUOTIENT_BITS-1]};
wire subtract = trial >= {1'b0, divisor};
wire [QUOTIENT_BITS-1:0] next_quotient = {quotient[QUOTIENT_BITS-2:0], subtract};
wire [31:0] next_remainder = subtract ? trial[31:0]-divisor : trial[31:0];
// Native jobs keep immutable frame/mesh data outside the input ring. One
// extra M10K holds coefficients, palette and three independent eight-word
// fetch lines. Fetch ownership survives media reset until the physical burst
// drains. No extra multiplier or reciprocal unit is introduced.
// Writes occur only during descriptor COPY or a cache-miss fill. No RAM
// output is consumed then; every consumer schedules a fresh read after the
// final write and waits through both read registers. Collision forwarding
// would add logic without changing any observable value.
(* ramstyle = "M10K, no_rw_check" *) reg [31:0] native_mem [0:255];
reg signed [31:0] native_xc, native_yc, native_xs, native_ys, native_nearclip;
wire signed [31:0] active_xc = ((INCLUDE_NATIVE_MESH && mode == NATIVE) || (INCLUDE_PARTICLES && mode == PARTICLE)) ? native_xc : xc;
wire signed [31:0] active_yc = ((INCLUDE_NATIVE_MESH && mode == NATIVE) || (INCLUDE_PARTICLES && mode == PARTICLE)) ? native_yc : yc;
wire signed [31:0] active_xs = ((INCLUDE_NATIVE_MESH && mode == NATIVE) || (INCLUDE_PARTICLES && mode == PARTICLE)) ? native_xs : xs;
wire signed [31:0] active_ys = ((INCLUDE_NATIVE_MESH && mode == NATIVE) || (INCLUDE_PARTICLES && mode == PARTICLE)) ? native_ys : ys;
wire signed [31:0] active_nearclip = ((INCLUDE_NATIVE_MESH && mode == NATIVE) || (INCLUDE_PARTICLES && mode == PARTICLE)) ? native_nearclip : nearclip;
// Billboard context shares the native RAM's otherwise unused final words.
// Its full matrix-independent camera basis is retained across native jobs.
// The initial ABI admits true camera-facing planes only (basis Z == zero).
// Vertices retain Q16 camera coordinates; one 47-bit reciprocal serves all
// three vertices. Attribute ZI is normalized independently of tested depth.
reg particle_context_valid;
// Particle jobs and native mesh jobs are mutually exclusive. The native
// source-word staging registers are idle during particles and are refreshed
// before every later native vertex; reuse them for scale/alpha staging.
wire signed [31:0] particle_scale = $signed(native_old_word);
wire [7:0] particle_alpha = native_current_word[7:0];
reg [31:0] particle_clip_x, particle_clip_y;
// A plane is outside precisely when all three vertices share an outcode
// bit. Four accumulated bits replace four 16-bit min/max registers. Signed
// 16-bit integer clip edges shifted by four fit in exactly signed20 bits.
reg [3:0] particle_outcode;
wire [3:0] particle_vertex_outcode = {
    $signed({{4{sy[15]}},sy}) >= $signed({particle_clip_y[31:16],4'd0}),
    $signed({{4{sy[15]}},sy}) <  $signed({particle_clip_y[15:0],4'd0}),
    $signed({{4{sx[15]}},sx}) >= $signed({particle_clip_x[31:16],4'd0}),
    $signed({{4{sx[15]}},sx}) <  $signed({particle_clip_x[15:0],4'd0})};
wire particle_outside = |particle_outcode;
reg [7:0] native_addr;
reg [31:0] native_ram_q;
(* preserve *) reg [31:0] native_q;
reg native_header_valid;
reg [9:0] native_mesh_bytes;
reg [24:0] native_mesh_limit;
reg [7:0] native_load_index;
// Reject discarded descriptor bits while loading, then retain only the
// exact accepted domain: aligned 16MiB addresses, counts1..4096, flags0..2.
// Stream/end keep bit24 so the one-past-chip sentinel never wraps to zero.
reg native_descriptor_valid;
reg [23:0] native_old_base, native_current_base;
reg [24:0] native_stream, native_end;
reg [12:0] native_source_count, native_meshes;
reg [1:0] native_flags;
reg [5:0] native_vertices, native_vertex;
reg [6:0] native_triangles, native_tri;
reg [31:0] native_old_word, native_current_word;
reg [3:0] native_column;
reg [2:0] native_purpose;
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
wire native_job_valid = surface_valid && native_descriptor_valid && native_nearclip > 0
    && native_stream < native_end
    && ({1'b0,native_old_base} + {10'd0,native_source_count,2'b0}) <= 25'h1000000
    && ({1'b0,native_current_base} + {10'd0,native_source_count,2'b0}) <= 25'h1000000;
always @(posedge clk) begin
    // M10K output registration isolates memory clock skew from the decode,
    // address arithmetic and DSP operand paths. FSM waits cover both stages.
    // Native MAC issues one coefficient each enabled cycle. Pause both
    // read registers with its issue counter so enable holds preserve alignment;
    // fill writes below remain live and drain independently while paused.
    if (enable) begin
        native_ram_q <= native_mem[native_addr];
        native_q <= native_ram_q;
    end
    if (native_fill_write) native_mem[native_fill_addr] <= mesh_rdata;
    else if (INCLUDE_PARTICLES && take_input && state == COPY && mode == PARTICLE_CONTEXT)
        native_mem[8'd184 + {2'd0,index}] <= input_data;
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
// At most 64 billboard records per command; stream expansion reserves each
// short output record independently, never the full expanded payload.
function valid_particles;
    input [12:0] count;
    begin
        case (count)
            5,10,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90,95,100,105,110,115,120,125,130,135,140,145,150,155,160,165,170,175,180,185,190,195,200,205,210,215,220,225,230,235,240,245,250,255,260,265,270,275,280,285,290,295,300,305,310,315,320: valid_particles=1'b1;
            default: valid_particles=1'b0;
        endcase
    end
endfunction
function signed [15:0] sat16;
    input signed [63:0] value;
    begin
        sat16 = value[63:15] == {49{value[15]}} ? value[15:0]
              : value[63] ? -16'sd32768 : 16'sd32767;
    end
endfunction

// Signed round-to-nearest, ties-to-even. Arithmetic shifts produce floor
// for negative values, so the unsigned fractional field gives the same tie
// decision on both sides of zero. Screen saturation happens after rounding.
function signed [47:0] particle_round16;
    input signed [63:0] value;
    reg signed [47:0] floor_value;
    begin
        floor_value = value[63:16];
        particle_round16 = floor_value;
        if ((value[15:0] > 16'h8000) || (value[15:0] == 16'h8000 && value[16]))
            particle_round16 = floor_value + 48'sd1;
    end
endfunction
function signed [15:0] particle_screen;
    input signed [63:0] value;
    reg signed [32:0] rounded;
    begin
        rounded = $signed({value[63],value[63:32]});
        if ((value[31:0] > 32'h80000000) || (value[31:0] == 32'h80000000 && value[32]))
            rounded = rounded + 33'sd1;
        particle_screen = rounded[32:15] == {18{rounded[15]}} ? rounded[15:0]
                        : rounded[32] ? -16'sd32768 : 16'sd32767;
    end
endfunction

// The rounded Q16 product of two signed32 inputs is in [-2^46,2^46].
// Its sum with either signed32 center therefore fits signed48 exactly. The
// existing accumulator is idle here; the preexisting capture wait registers
// rounding there before this one shared offset adder. No cycle is added.
wire signed [31:0] particle_center = row == 0 ? vx : vy;
wire signed [47:0] particle_offset = accumulator
    + $signed({{16{particle_center[31]}},particle_center});
wire particle_offset_overflow = particle_offset[47:31] != {17{particle_offset[31]}};

// Explicitly share one projection adder for legacy/particle X and Y.
// All shifts and signed extensions are exact; the 64-bit sum is required by
// the unrestricted signed32 viewport/context ABI before final saturation.
wire projection_y = state == SY_ADD;
wire projection_particle = INCLUDE_PARTICLES && mode == PARTICLE;
wire signed [31:0] projection_center = projection_y ? active_yc : active_xc;
wire signed [63:0] projection_center_wide = {{32{projection_center[31]}},projection_center};
wire signed [63:0] projection_left = projection_particle
    ? projection_center_wide <<< 20
    : projection_y ? projection_center_wide : projection_center_wide <<< 4;
wire signed [63:0] projection_right = projection_particle ? product
    : projection_y ? product >>> 16 : product >>> 12;
wire signed [63:0] projection_sum = projection_left
    + (projection_right ^ {64{projection_y}}) + projection_y;

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
        EMIT, TRI_HEADER, TRI_WORD, P_ALPHA_HEADER, P_ALPHA_WORD: emit = 1'b1;
        default: ;
    endcase
end
always @(*) begin
    emit_data = 32'd0;
    case (state)
        SPACE: emit_data = header;
        COPY: emit_data = input_data;
        EMIT: begin
            // The sole entry SY_CLAMP sets index=0; EMIT leaves at 6.
            // Its upper bits are therefore always zero while writing this
            // payload. Exclude the shared COPY/LOAD counter's upper decode
            // from the queue data mux (the measured v2 timing bottleneck).
            case (index[2:0])
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
        P_ALPHA_HEADER: emit_data = 32'h63000001;
        P_ALPHA_WORD: emit_data = {24'd0,particle_alpha};
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
    if (INCLUDE_PARTICLES && take_input && state == COPY && mode == PASS
        && header[31:24] == 8'h4a && (header[12:0] == 16 || header[12:0] == 17)) begin
        if (index == 14) particle_clip_x <= input_data;
        if (index == 15) particle_clip_y <= input_data;
    end
    if (take_input && state == COPY && mode == OBJECT && index < 12)
        matrix[index[3:0]] <= input_data;
    // A native MAC has several products in flight; its pipeline freezes with
    // the native issue counter. Legacy scheduling remains unchanged.
    if (((!INCLUDE_NATIVE_MESH || mode != NATIVE) && (!INCLUDE_PARTICLES || mode != PARTICLE)) || enable)
        product <= mul_a * mul_b;
end
always @(posedge clk) begin
    if (!reset_n || soft_reset || ring_reset) begin
        input_rdptr <= (reset_n && soft_reset && !ring_reset) ? input_wrptr : 0;
        write_ptr <= 0; output_wrptr <= 0; state <= HEADER;
        mode <= PASS; index <= 0; remaining <= 0; needed <= 0;
        native_load_index <= 0; native_resume <= N_START;
        native_descriptor_valid <= 0;
        native_addr <= 0; native_read_addr <= 0; native_read_bank <= 0;
        native_purpose <= NP_HEADER; native_vertex <= 0; native_tri <= 0;
        native_column <= 0;
        particle_context_valid <= 0;
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
                if (INCLUDE_PARTICLES && header[31:24] == 8'h61 && header[12:0] == 16) begin
                    mode <= PARTICLE_CONTEXT; needed <= 0; particle_context_valid <= 1;
                end else if (INCLUDE_PARTICLES && header[31:24] == 8'h62 && valid_particles(header[12:0])) begin
                    mode <= PARTICLE; needed <= 0; native_load_index <= 6;
                end else if (INCLUDE_NATIVE_MESH && header[31:24] == 8'h60 && header[12:0] == 164) begin
                    mode <= NATIVE; needed <= 0; native_load_index <= 0;
                    native_descriptor_valid <= 1;
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
                       : (INCLUDE_PARTICLES && mode == PARTICLE) ? P_CONTEXT_ADDR
                       : (mode == RAW || mode == CLIP || mode == CULL) ? LOAD : COPY;
            COPY: if (take_input) begin
                if (INCLUDE_NATIVE_MESH && mode == NATIVE) begin
                    native_load_index <= native_load_index + 1'b1;
                    case (native_load_index)
                    0: begin
                        native_old_base <= {input_data[23:2],2'b0};
                        if (input_data[31:24] != 0 || input_data[1:0] != 0)
                            native_descriptor_valid <= 0;
                    end
                    1: begin
                        native_current_base <= {input_data[23:2],2'b0};
                        if (input_data[31:24] != 0 || input_data[1:0] != 0)
                            native_descriptor_valid <= 0;
                    end
                    2: begin
                        native_stream <= {1'b0,input_data[23:2],2'b0};
                        if (input_data[31:24] != 0 || input_data[1:0] != 0)
                            native_descriptor_valid <= 0;
                    end
                    3: begin
                        native_meshes <= input_data[12:0];
                        if (input_data == 0 || input_data > 4096)
                            native_descriptor_valid <= 0;
                    end
                    4: begin
                        native_flags <= input_data[1:0];
                        if (input_data > 2) native_descriptor_valid <= 0;
                    end
                    5: begin
                        native_source_count <= input_data[12:0];
                        if (input_data == 0 || input_data > 4096)
                            native_descriptor_valid <= 0;
                    end
                    6: begin
                        native_end <= {input_data[24:2],2'b0};
                        if (input_data > 32'h01000000 || input_data[1:0] != 0)
                            native_descriptor_valid <= 0;
                    end
                    31: native_xc <= input_data;
                    32: native_yc <= input_data;
                    33: native_xs <= input_data;
                    34: native_ys <= input_data;
                    35: native_nearclip <= input_data;
                    default: ;
                    endcase
                end
                if (INCLUDE_PARTICLES && mode == PARTICLE_CONTEXT) begin
                    if ((index == 2 || index == 5) && input_data != 0) particle_context_valid <= 0;
                    if (index == 10 && ($signed(input_data) <= 0)) particle_context_valid <= 0;
                    if ((index == 8 || index == 9) && $signed(input_data) <= 0) particle_context_valid <= 0;
                    if (index == 15 && input_data > 2) particle_context_valid <= 0;
                    if (index >= 11 && index <= 14 && input_data >= 32'h00800000) particle_context_valid <= 0;
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
                if (INCLUDE_PARTICLES && mode == PARTICLE) begin
                    case (index)
                    0: vx <= input_data;
                    1: vy <= input_data;
                    2: vz <= input_data;
                    3: native_old_word <= input_data;
                    4: begin
                        rgb <= input_data[15:0]; native_current_word[7:0] <= input_data[23:16];
                        index <= 0; slot <= 0;
                        state <= input_data[31:24] != 0 ? P_FINISH : P_START;
                    end
                    default: ;
                    endcase
                end else if (mode == CULL) begin
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
                remainder <= 0;
                if (INCLUDE_PARTICLES && mode == PARTICLE) begin
                    quotient <= 47'h400000000000; iterations <= 47;
                end else begin
                    // Align the old numerator at the widened divider's MSB.
                    // Iteration counts remain unchanged for every legacy ABI.
                    quotient <= {{(QUOTIENT_BITS-39){1'b0}},39'h4000000000} << (QUOTIENT_BITS-39);
                    iterations <= (mode == RAW || mode == NATIVE) ? 39 : 33;
                end
                state <= DIV_RUN;
            end
            DIV_RUN: begin
                quotient <= next_quotient; remainder <= next_remainder;
                iterations <= iterations - 1'b1;
                if (iterations == 1) begin
                    zi <= (INCLUDE_PARTICLES && mode == PARTICLE) ? 32'h01000000
                        : (mode == RAW || mode == NATIVE) ? {6'd0,next_quotient[31:6]} : next_quotient[31:0];
                    if (mode == RAW || mode == NATIVE || (INCLUDE_PARTICLES && mode == PARTICLE)) depth <= next_quotient[31:0];
                    state <= RX_START;
                end
            end
            RX_START: begin
                mul_a <= cx; mul_b <= projection_particle ? depth : zi; state <= RX_WAIT;
                // Native RAM is idle throughout projection. Pipeline its two
                // UV reads under the existing multiplier waits, so endpoints
                // need no extra registers or added vertex cycles.
                if (projection_particle) native_addr <= slot == 1 ? 8'd196 : 8'd195;
            end
            RX_WAIT: begin
                if (projection_particle) native_addr <= slot == 2 ? 8'd198 : 8'd197;
                state <= RX_CAPTURE;
            end
            RX_CAPTURE: begin ratio <= (INCLUDE_PARTICLES && mode == PARTICLE) ? product[57:26] : product[47:16]; state <= SX_START; end
            SX_START: begin
                mul_a <= active_xs; mul_b <= ratio; state <= SX_WAIT;
                if (projection_particle) u <= native_q;
            end
            SX_WAIT: begin
                if (projection_particle) v <= native_q;
                state <= SX_ADD;
            end
            SX_ADD: begin projected <= projection_sum; state <= SX_CLAMP; end
            SX_CLAMP: begin sx <= (INCLUDE_PARTICLES && mode == PARTICLE)
                ? particle_screen(projected) : sat16(projected); state <= RY_START; end
            RY_START: begin mul_a <= cy; mul_b <= (INCLUDE_PARTICLES && mode == PARTICLE) ? depth : zi; state <= RY_WAIT; end
            RY_WAIT: state <= RY_CAPTURE;
            RY_CAPTURE: begin ratio <= (INCLUDE_PARTICLES && mode == PARTICLE) ? product[57:26] : product[47:16]; state <= SY_START; end
            SY_START: begin mul_a <= active_ys; mul_b <= ratio; state <= SY_WAIT; end
            SY_WAIT: state <= SY_ADD;
            SY_ADD: begin projected <= projection_sum; state <= SY_CLAMP; end
            SY_CLAMP: begin sy <= (INCLUDE_PARTICLES && mode == PARTICLE)
                ? particle_screen(projected) : sat16(projected); index <= 0; state <= EMIT; end
            EMIT: begin
                if (INCLUDE_PARTICLES && mode == PARTICLE && index == 2) begin
                    particle_outcode <= slot == 0 ? particle_vertex_outcode
                                                 : particle_outcode & particle_vertex_outcode;
                end
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
                       : (INCLUDE_PARTICLES && mode == PARTICLE) ? native_resume
                       : mode == RAW && remaining != 0 ? SPACE : HEADER;
            end
            P_CONTEXT_ADDR: begin native_addr <= 8'd184 + native_load_index; state <= P_CONTEXT_WAIT; end
            P_CONTEXT_WAIT: state <= P_CONTEXT_CAPTURE;
            P_CONTEXT_CAPTURE: begin
                // The RAM has two registered read stages; P_START captures
                // the addressed word after both registers have advanced.
                state <= P_START;
            end
            P_START: if (INCLUDE_PARTICLES) begin
                if (native_load_index < 16) begin
                    case (native_load_index)
                    6: native_xc <= native_q;
                    7: native_yc <= native_q;
                    8: native_xs <= native_q;
                    9: native_ys <= native_q;
                    10: native_nearclip <= native_q;
                    15: native_flags <= native_q;
                    default: ;
                    endcase
                    native_load_index <= native_load_index + 1'b1;
                    state <= native_load_index == 15 ? LOAD : P_CONTEXT_ADDR;
                end else if (!particle_context_valid || !surface_valid || particle_alpha == 0
                    || particle_scale <= 0 || vz < native_nearclip || vz < 32'sd1048576 || vz > 32'sd268435456)
                    state <= P_FINISH;
                else begin state <= P_ALPHA_SPACE; end
            end
            P_ALPHA_SPACE: if (free_words >= 2) state <= P_ALPHA_HEADER;
            P_ALPHA_HEADER: state <= P_ALPHA_WORD;
            P_ALPHA_WORD: begin slot <= 0; state <= P_VERTEX; end
            P_VERTEX: begin
                native_resume <= P_NEXT_VERTEX;
                if (slot == 0) begin cx <= vx; cy <= vy; cw <= vz; state <= P_PROJECT_SPACE; end
                else begin row <= 0; state <= P_BASIS_ADDR; end
            end
            P_BASIS_ADDR: begin native_addr <= 8'd184 + (slot == 1 ? 0 : 3) + {6'd0,row}; state <= P_BASIS_WAIT; end
            P_BASIS_WAIT: state <= P_BASIS_CAPTURE;
            P_BASIS_CAPTURE: state <= P_BASIS_CAPTURE_Q;
            P_BASIS_CAPTURE_Q: state <= P_MUL_WAIT;
            P_MUL_WAIT: begin mul_a <= native_q; mul_b <= particle_scale; state <= P_MUL_CAPTURE; end
            P_MUL_CAPTURE: state <= P_MUL_CAPTURE_Q;
            P_MUL_CAPTURE_Q: begin
                accumulator <= particle_round16(product); state <= P_PROJECT_SPACE;
            end
            P_PROJECT_SPACE: begin
                if (slot != 0) begin
                    if (particle_offset_overflow) state <= P_FINISH;
                    else if (row == 0) begin cx <= particle_offset[31:0]; row <= 1; state <= P_BASIS_ADDR; end
                    else if (free_words >= 7) begin
                        cy <= particle_offset[31:0]; native_resume <= P_NEXT_VERTEX; state <= RX_START;
                    end
                end else if (free_words >= 7) begin native_resume <= P_NEXT_VERTEX; state <= DIV_INIT; end
            end
            P_NEXT_VERTEX: begin
                if (slot < 2) begin slot <= slot + 1'b1; state <= P_VERTEX; end
                else state <= P_TRI_SPACE;
            end
            P_TRI_SPACE: if (particle_outside) state <= P_FINISH;
                else if (free_words >= 2) begin
                    triangle <= {15'd0,native_flags[1:0],5'd2,5'd1,5'd0};
                    point_addr <= 0; native_resume <= P_FINISH;
                    state <= native_flags == 0 ? TRI_HEADER : CULL_A;
                end
            P_FINISH: begin index <= 0; state <= remaining != 0 ? LOAD : HEADER; end
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
            N_WAIT: state <= N_WAIT_Q;
            N_WAIT_Q: state <= N_CAPTURE;
            N_CAPTURE: begin
                case (native_purpose)
                NP_HEADER: begin
                    // Validate counts separately from the bounded byte-size
                    // add. A valid block needs at most 644 bytes. Keeping the
                    // two carry chains out of one cycle fixes the v1 M10K ->
                    // pointer write-enable critical path.
                    native_header_valid <= native_q[31:24] == 0
                        && native_q[7:0] != 0 && native_q[7:0] <= 32
                        && native_q[23:8] != 0 && native_q[23:8] <= 64;
                    native_vertices <= native_q[5:0];
                    native_triangles <= native_q[14:8];
                    native_mesh_bytes <= 10'd4 + ({4'd0,native_q[5:0]} * 10'd12)
                        + {1'd0,native_q[14:8],2'b0};
                    state <= N_HEADER_BOUND;
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
            N_HEADER_BOUND: begin
                native_mesh_limit <= {1'b0,native_stream[23:0]} + {15'd0,native_mesh_bytes};
                state <= N_HEADER_CHECK;
            end
            N_HEADER_CHECK: begin
                if (!native_header_valid || native_mesh_limit > native_end[24:0]) state <= HEADER;
                else begin
                    native_vertex <= 0; native_tri <= 0;
                    native_stream <= native_stream + 4;
                    native_read_addr <= native_stream + 4;
                    native_purpose <= NP_SOURCE; state <= N_READ;
                end
            end
            N_MAC_ADDR: begin
                // One pipelined DSP accepts six products on consecutive
                // clocks. Coefficients pass through two RAM registers, the
                // operand registers, and product before accumulation. Cycles
                // 0..6 fetch coeff[0..5]/offset, 3..8 issue, 5..10 accumulate.
                if (native_column <= 6)
                    native_addr <= {3'd0,row,3'd0} + {4'd0,native_column};
                if (native_column >= 3 && native_column <= 8) begin
                    mul_a <= native_q;
                    case (native_column)
                    3: mul_b <= {24'd0,native_old_word[7:0]};
                    4: mul_b <= {24'd0,native_old_word[15:8]};
                    5: mul_b <= {24'd0,native_old_word[23:16]};
                    6: mul_b <= {24'd0,native_current_word[7:0]};
                    7: mul_b <= {24'd0,native_current_word[15:8]};
                    default: mul_b <= {24'd0,native_current_word[23:16]};
                    endcase
                end
                if (native_column >= 5 && native_column <= 10)
                    accumulator <= native_column == 5 ? product[47:0]
                                                      : accumulator + product[47:0];
                native_column <= native_column + 1'b1;
                if (native_column == 10) state <= N_MAC_ROW;
            end
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
            N_PALETTE_WAIT: state <= N_PALETTE_WAIT_Q;
            N_PALETTE_WAIT_Q: state <= N_PALETTE_CAPTURE;
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
