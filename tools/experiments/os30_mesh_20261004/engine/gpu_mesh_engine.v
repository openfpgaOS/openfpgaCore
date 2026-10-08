// Private fixed-point geometry microengine. See ISA.txt. No CPU/cache/FPU.
// Each synchronous scratch RAM port performs at most one access per clock.
`default_nettype none
module gpu_mesh_engine #(parameter COMPACT_LAYOUT=0, parameter ENABLE_DOT=1, parameter EXTENDED_ISA=1, parameter CODE_PAGES=3) (
    input wire clk, reset_n, cancel,
    input wire start,
    input wire [9:0] entry,
    output wire busy,
    output reg done,
    output reg [1:0] fault,
    output reg [31:0] cycles,
    input wire code_we,
    input wire [9:0] code_addr,
    input wire [31:0] code_data,
    input wire scratch_we,
    input wire [7:0] scratch_addr,
    input wire [31:0] scratch_data,
    output wire [7:0] bank_addr,
    output wire bank_we,
    output wire [31:0] bank_wdata,
    input wire [31:0] bank_rdata,
    output wire out_valid,
    input wire out_ready,
    output wire [31:0] out_data
);
localparam IDLE=0, FETCH=1, DECODE=2, READ=3, EXEC=4,
    MUL_WAIT=5, MUL_COMMIT=6, DIV_RUN=7, DIV_COMMIT=8,
    LOAD_WAIT=9, LOAD_COMMIT=10, EMIT=11, PLANE_WAIT=12, EDGE_WAIT=13, DOT_WAIT=14,
    MUL_SAVE=15, NORM_WAIT=16, COPY_REQ=17, COPY_RUN=18, PROJ_WAIT=19, VERTEX_WAIT=20, ALU_COMMIT=21, GATHER_WAIT=22, ATTR_WAIT=23, BURST_REQ=24, BURST_EMIT=25, SETUP_WAIT=26;
localparam HALT=0, LI=1, LUI=2, ORI=3, MOV=4, ADD=5, SUB=6,
    AND_OP=7, OR_OP=8, XOR_OP=9, SHL=10, SHR=11, SAR=12, ADDI=13,
    MUL=14, MAC=15, ACCLO=16, ACCHI=17, ACCSAR=18, ACCSET=19,
    DIVU=20, DIVS=21, LOAD=22, STORE=23, OUT=24, JMP=25, BR=26,
    CMP=27, ADDC=28, SUBB=29, JAL=30, JREG=31, SEXT=32, DIVW=33,
    REM=34, ACCADD=35, ACCSUB=36, SAT=37, NEG=38, ACCSHL=39, PLANE=40, SLOAD=41, SSTORE=42, EDGE=43, MATROW=44, DOT3=45, NORM=46, PROJ=47, VERTEX=48, COPY=49, GATHER=50, PROJA=51, ATTR=52, SCOPY=53, PLANES=54, OUTBLOCK=55, SETUP=56;
reg [4:0] state;
reg [9:0] pc;
// Explicit256-wordpagesinferexactlythreeM10Ks,notapower-of-two1024depth.
(* ramstyle = "M10K" *) reg [31:0] code0 [0:255];
(* ramstyle = "M10K" *) reg [31:0] code1 [0:255];
reg [31:0] code0_q,code1_q;
wire [31:0] code2_q;
reg [1:0] code_page;
wire [31:0] code_q=code_page==0?code0_q:code_page==1?code1_q:code_page==2?code2_q:32'hff000000;
(* ramstyle = "M10K" *) reg [31:0] scratch [0:255];
reg [31:0] qa, qb;
reg [7:0] op, rd, ra, rb, immediate;
reg [15:0] immediate16;
reg flag_eq, flag_lt, flag_ltu, flag_c;
reg signed [63:0] accumulator;
reg signed [31:0] mul_a, mul_b;
(* multstyle = "dsp" *) reg signed [63:0] product;
reg [63:0] quotient;
reg [32:0] divisor, remainder;
reg [6:0] div_count;
reg div_negative, rem_negative, div_signed32, div_zero, edge_div_done, proj_div_done, vertex_div_done;
reg [1:0] div_owner;
reg [31:0] emit_data,architectural_remainder;
reg [7:0] bank_addr_q;
reg [31:0] bank_data_q;
reg [7:0] copy_src,copy_dst;
reg [8:0] copy_left;
reg copy_scratch;
reg base_scratch_write;
reg [7:0] base_write_addr;
reg [31:0] base_write_data;
reg [31:0] result;
reg result_valid;
reg [31:0] alu_result;
wire active = reset_n && !cancel;
assign busy = state != IDLE;
wire bank_copy = state==COPY_REQ || state==COPY_RUN;
wire bank_generic = state==LOAD_WAIT || state==LOAD_COMMIT;
assign bank_addr = ({8{attr_busy}} & attr_bank_addr) |
    ({8{gather_busy}} & gather_bank_addr) | ({8{vertex_busy}} & vertex_bank_addr) |
    ({8{bank_copy}} & copy_src) | ({8{norm_busy}} & norm_addr) |
    ({8{plane_busy}} & plane_bank_addr) | ({8{bank_generic}} & bank_addr_q);
assign bank_we = active && ((EXTENDED_ISA && state == LOAD_WAIT && op == STORE) || norm_we || vertex_bank_we || attr_bank_we);
assign bank_wdata = ({32{attr_busy}} & attr_bank_wdata) |
    ({32{vertex_busy}} & vertex_bank_wdata) | ({32{norm_busy}} & norm_wdata) |
    ({32{EXTENDED_ISA && bank_generic}} & bank_data_q);
assign out_valid = active && (state == EMIT || state == BURST_EMIT || plane_out_valid);
assign out_data = plane_out_valid ? plane_out_data : state == BURST_EMIT ? qb : emit_data;

wire subtract32 = op == SUB || op == SUBB || op == CMP;
wire [31:0] add_a = qa;
wire [31:0] add_b = op == ADDI ? {{24{immediate[7]}},immediate} : qb;
wire carry_in = (EXTENDED_ISA && op == ADDC) || op == SUBB ? flag_c : subtract32;
wire [32:0] sum = {1'b0,add_a} + {1'b0,(subtract32 ? ~add_b : add_b)} + carry_in;
// NEG uses qa, unlike binary subtraction.
wire [31:0] neg_a = ~qa + 32'd1;
wire signed [63:0] wide_operand = state == MUL_COMMIT ? product : $signed({qa,qb});
wire [63:0] wide_sum = EXTENDED_ISA ? accumulator + (op == ACCSUB ? ~wide_operand : wide_operand)
                                      + (op == ACCSUB ? 64'd1 : 64'd0) : 64'd0;
wire [33:0] div_trial = {remainder,quotient[63]};
wire [33:0] div_sub = div_trial - {1'b0,divisor};
wire div_take = !div_sub[33];
wire [31:0] signed_quotient = div_negative ? (~quotient[31:0]+32'd1) : quotient[31:0];
wire [63:0] div_result = div_zero ? 64'hffffffffffffffff :
    (EXTENDED_ISA && div_signed32) ? {{32{signed_quotient[31]}},signed_quotient} : quotient;
// One reversible64-bit barrel shifter serves ordinary and accumulator shifts.
// Bit reversal turns left shifts into right shifts without a second barrel.
wire shift_wide = EXTENDED_ISA && (op==ACCSAR || op==ACCSHL);
wire shift_left = op==SHL || (EXTENDED_ISA && op==ACCSHL);
wire shift_arithmetic = op==SAR || op==ACCSAR;
wire [5:0] shift_count = shift_wide ? immediate[5:0] : {1'b0,qb[4:0]};
wire [63:0] shift_operand = shift_wide ? accumulator :
    {{32{op==SAR && qa[31]}},qa};
wire [63:0] shift_reversed,shift_unreversed;
genvar bit_index;
generate for(bit_index=0;bit_index<64;bit_index=bit_index+1) begin:g_shift_reverse
    assign shift_reversed[bit_index]=shift_operand[63-bit_index];
    assign shift_unreversed[bit_index]=shift_right_result[63-bit_index];
end endgenerate
wire [63:0] shift_input = shift_left ? shift_reversed : shift_operand;
wire signed [64:0] shift_signed_input = {shift_arithmetic && shift_operand[63],shift_input};
wire signed [64:0] shift_right_full = shift_signed_input >>> shift_count;
wire [63:0] shift_right_result = shift_right_full[63:0];
wire [63:0] shift_result = shift_left ? shift_unreversed : shift_right_result;
// The release mesh program needs only fixed slices of the wide accumulator.
// Keep its ordinary shift barrel physically32 bits, independent of that bus.
wire [31:0] lean_reverse_input,lean_reverse_output;
generate for(bit_index=0;bit_index<32;bit_index=bit_index+1) begin:g_lean_shift_reverse
    assign lean_reverse_input[bit_index]=qa[31-bit_index];
    assign lean_reverse_output[bit_index]=lean_shift_right[31-bit_index];
end endgenerate
wire [31:0] lean_shift_input=op==SHL ? lean_reverse_input : qa;
wire signed [32:0] lean_shift_signed={op==SAR && qa[31],lean_shift_input};
wire signed [32:0] lean_shift_full=lean_shift_signed >>> qb[4:0];
wire [31:0] lean_shift_right=lean_shift_full[31:0];
wire [31:0] lean_shift_result=op==SHL ? lean_reverse_output : lean_shift_right;

wire [31:0] signed_remainder = rem_negative ? (~remainder[31:0]+32'd1) : remainder[31:0];
// PROJ's numerator is exactly2^38 and denominator is at least256. Consume
// its first k=floor(log2(den)) known-zero quotient bits without iterating.
wire [31:0] proj_prefix1=proj_div_den | (proj_div_den>>1);
wire [31:0] proj_prefix2=proj_prefix1 | (proj_prefix1>>2);
wire [31:0] proj_prefix4=proj_prefix2 | (proj_prefix2>>4);
wire [31:0] proj_prefix8=proj_prefix4 | (proj_prefix4>>8);
wire [31:0] proj_prefix16=proj_prefix8 | (proj_prefix8>>16);
wire [31:0] proj_highbit=proj_prefix16 & ~(proj_prefix16>>1);
wire [4:0] proj_log2={|(proj_highbit & 32'hffff0000),
    |(proj_highbit & 32'hff00ff00), |(proj_highbit & 32'hf0f0f0f0),
    |(proj_highbit & 32'hcccccccc), |(proj_highbit & 32'haaaaaaaa)};

wire plane_busy,plane_done,plane_write;
wire plane_out_valid;
wire [31:0] plane_out_data;
wire [7:0] plane_ra,plane_rb,plane_wa,plane_bank_addr;
wire [31:0] plane_wd;
wire signed [31:0] plane_mul_a,plane_mul_b;
wire signed [31:0] shared_mul_a =
    ({32{setup_busy}} & setup_mul_a) |
    ({32{attr_busy}} & attr_mul_a) |
    ({32{vertex_busy}} & vertex_mul_a) |
    ({32{proj_busy}} & proj_mul_a) |
    ({32{edge_busy}} & edge_mul_a) |
    ({32{dot_busy}} & dot_mul_a) |
    ({32{plane_busy}} & plane_mul_a) |
    ({32{state==MUL_WAIT || state==MUL_COMMIT}} & mul_a);
wire signed [31:0] shared_mul_b =
    ({32{setup_busy}} & setup_mul_b) |
    ({32{attr_busy}} & attr_mul_b) |
    ({32{vertex_busy}} & vertex_mul_b) |
    ({32{proj_busy}} & proj_mul_b) |
    ({32{edge_busy}} & edge_mul_b) |
    ({32{dot_busy}} & dot_mul_b) |
    ({32{plane_busy}} & plane_mul_b) |
    ({32{state==MUL_WAIT || state==MUL_COMMIT}} & mul_b);
gpu_mesh_plane #(.COMPACT_LAYOUT(COMPACT_LAYOUT), .BATCH_ONLY(!EXTENDED_ISA)) plane (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),
    .start(state==EXEC && ((EXTENDED_ISA && op==PLANE) || op==PLANES)),.batch(op==PLANES),
    .ptr0(qa[7:0]),.ptr1(qb[7:0]),.ptr2_reg(rd),
    .busy(plane_busy),.done(plane_done),
    .scratch_ra(plane_ra),.scratch_rb(plane_rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(plane_write),.scratch_wa(plane_wa),.scratch_wd(plane_wd),
    .bank_addr(plane_bank_addr),.bank_rdata(bank_rdata),
    .mul_a(plane_mul_a),.mul_b(plane_mul_b),.product(product),
    .out_valid(plane_out_valid),.out_data(plane_out_data),.out_ready(out_ready && active)
);
wire dot_busy,dot_done,dot_write;
wire [7:0] dot_ra,dot_rb,dot_wa;
wire [31:0] dot_wd;
wire signed [31:0] dot_mul_a,dot_mul_b;
wire signed [63:0] dot_acc;
generate if(ENABLE_DOT) begin:g_dot
gpu_mesh_dot dot (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),
    .start(state==EXEC && (op==MATROW || op==DOT3)),.matrix_row(op==MATROW),
    .ptr_a(qa[7:0]),.ptr_b(qb[7:0]),.destination(rd),.busy(dot_busy),.done(dot_done),
    .scratch_ra(dot_ra),.scratch_rb(dot_rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(dot_write),.scratch_wa(dot_wa),.scratch_wd(dot_wd),
    .mul_a(dot_mul_a),.mul_b(dot_mul_b),.product(product),.accumulator(dot_acc)
);
end else begin:g_no_dot
assign dot_busy=0;assign dot_done=0;assign dot_write=0;
assign dot_ra=0;assign dot_rb=0;assign dot_wa=0;assign dot_wd=0;
assign dot_mul_a=0;assign dot_mul_b=0;assign dot_acc=0;
end endgenerate
wire edge_busy,edge_done,edge_write,edge_div_start;
wire [7:0] edge_ra,edge_rb,edge_wa;
wire [31:0] edge_wd,edge_div_num;
wire [15:0] edge_div_divisor;
wire signed [31:0] edge_mul_a,edge_mul_b;
gpu_mesh_edges #(.COMPACT_LAYOUT(COMPACT_LAYOUT)) edges (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(state==EXEC && op==EDGE),
    .busy(edge_busy),.done(edge_done),.scratch_ra(edge_ra),.scratch_rb(edge_rb),
    .scratch_qa(qa),.scratch_qb(qb),.scratch_we(edge_write),.scratch_wa(edge_wa),.scratch_wd(edge_wd),
    .mul_a(edge_mul_a),.mul_b(edge_mul_b),.product(product),
    .div_start(edge_div_start),.div_num(edge_div_num),.div_divisor(edge_div_divisor),
    .div_done(edge_div_done),.quotient(quotient[31:0])
);
wire norm_busy,norm_done,norm_we;
wire [7:0] norm_addr;
wire [31:0] norm_wdata;
gpu_mesh_normalize normalize (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(state==EXEC && op==NORM),
    .base(qa[7:0]),.busy(norm_busy),.done(norm_done),.bank_addr(norm_addr),
    .bank_rdata(bank_rdata),.bank_we(norm_we),.bank_wdata(norm_wdata)
);
wire proj_busy,proj_done,proj_write,proj_div_start;
wire [7:0] proj_ra,proj_rb,proj_wa;
wire [31:0] proj_wd,proj_div_den;
wire signed [31:0] proj_mul_a,proj_mul_b;
gpu_mesh_project project (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),
    .start(state==EXEC && ((EXTENDED_ISA && op==PROJ) || op==PROJA)),
    .material_mode(!EXTENDED_ISA || op==PROJA),
    .src_ptr(qa[7:0]),.dst_ptr(qb[7:0]),.busy(proj_busy),.done(proj_done),
    .scratch_ra(proj_ra),.scratch_rb(proj_rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(proj_write),.scratch_wa(proj_wa),.scratch_wd(proj_wd),
    .mul_a(proj_mul_a),.mul_b(proj_mul_b),.product(product),
    .div_start(proj_div_start),.div_den(proj_div_den),.div_done(proj_div_done),.quotient(quotient[31:0])
);
wire vertex_busy,vertex_done,vertex_bank_we,vertex_div_start;
wire [7:0] vertex_ra,vertex_rb,vertex_bank_addr;
wire [31:0] vertex_bank_wdata,vertex_div_den;
wire [63:0] vertex_div_num;
wire [6:0] vertex_div_bits;
wire signed [31:0] vertex_mul_a,vertex_mul_b;
gpu_mesh_vertex vertex_shader (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(state==EXEC && op==VERTEX),
    .raw_ptr(qa[7:0]),.output_ptr(qb[7:0]),.busy(vertex_busy),.done(vertex_done),
    .scratch_ra(vertex_ra),.scratch_rb(vertex_rb),.scratch_qa(qa),.scratch_qb(qb),
    .bank_addr(vertex_bank_addr),.bank_we(vertex_bank_we),.bank_wdata(vertex_bank_wdata),.bank_rdata(bank_rdata),
    .mul_a(vertex_mul_a),.mul_b(vertex_mul_b),.product(product),
    .div_start(vertex_div_start),.div_numerator(vertex_div_num),.div_denominator(vertex_div_den),
    .div_bits(vertex_div_bits),.div_busy(state==DIV_RUN || state==DIV_COMMIT),
    .div_done(vertex_div_done),.div_quotient(quotient)
);
wire gather_busy,gather_done,gather_write;
wire [7:0] gather_ra,gather_rb,gather_wa,gather_bank_addr;
wire [31:0] gather_wd;
gpu_mesh_gather gather (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(state==EXEC && op==GATHER),
    .tri_index(qa[5:0]),.dst_ptr(qb[7:0]),.mask_reg(rd),.busy(gather_busy),.done(gather_done),
    .scratch_ra(gather_ra),.scratch_rb(gather_rb),.scratch_qa(qa),.scratch_qb(qb),
    .scratch_we(gather_write),.scratch_wa(gather_wa),.scratch_wd(gather_wd),
    .bank_addr(gather_bank_addr),.bank_rdata(bank_rdata)
);
wire attr_busy,attr_done,attr_bank_we;
wire [7:0] attr_ra,attr_rb,attr_bank_addr;
wire [31:0] attr_bank_wdata;
wire signed [31:0] attr_mul_a,attr_mul_b;
gpu_mesh_attributes attributes (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(state==EXEC && op==ATTR),
    .src_ptr(qa[7:0]),.dst_ptr(qb[7:0]),.combine_reg(rd),.busy(attr_busy),.done(attr_done),
    .scratch_ra(attr_ra),.scratch_rb(attr_rb),.scratch_qa(qa),.scratch_qb(qb),
    .bank_addr(attr_bank_addr),.bank_we(attr_bank_we),.bank_wdata(attr_bank_wdata),
    .mul_a(attr_mul_a),.mul_b(attr_mul_b),.product(product)
);
reg branch_take;
wire setup_busy,setup_done,setup_write;
wire [7:0] setup_ra,setup_rb,setup_wa;
wire [31:0] setup_wd;
wire signed [31:0] setup_mul_a,setup_mul_b;
generate if(COMPACT_LAYOUT)begin:g_setup
gpu_mesh_setup setup_unit (
    .clk(clk),.reset_n(reset_n),.cancel(cancel),.start(state==EXEC && op==SETUP),
    .busy(setup_busy),.done(setup_done),.scratch_ra(setup_ra),.scratch_rb(setup_rb),
    .scratch_qa(qa),.scratch_qb(qb),.scratch_we(setup_write),.scratch_wa(setup_wa),.scratch_wd(setup_wd),
    .mul_a(setup_mul_a),.mul_b(setup_mul_b),.product(product)
);
end else begin:g_no_setup
assign setup_busy=0;assign setup_done=0;assign setup_write=0;
assign setup_ra=0;assign setup_rb=0;assign setup_wa=0;assign setup_wd=0;
assign setup_mul_a=0;assign setup_mul_b=0;
end endgenerate
wire [9:0] decoded_next_pc = code_q[31:24]==JMP || code_q[31:24]==JAL ||
    (code_q[31:24]==BR && branch_take) ? code_q[9:0] : pc+10'd1;
wire [9:0] code_read_addr = state==DECODE ? decoded_next_pc : pc;
wire read_generic=!(setup_busy || attr_busy || gather_busy || vertex_busy || proj_busy || edge_busy || dot_busy || plane_busy);
wire [7:0] read_generic_a=state==LOAD_WAIT && op==SLOAD ? bank_addr_q : state==DECODE ?
    (code_q[31:24]==ORI ? code_q[23:16] : code_q[15:8]) : ra;
wire [7:0] read_generic_b=state==BURST_REQ || state==BURST_EMIT ?
    copy_src + ((state==BURST_EMIT && out_ready) ? 8'd1 : 8'd0) :
    bank_copy && copy_scratch ? copy_src : state==DECODE ?
    ((code_q[31:24]==STORE || code_q[31:24]==SSTORE) ? code_q[23:16] : code_q[7:0]) : rb;
wire [7:0] read_addr_a=
    ({8{setup_busy}} & setup_ra) |
    ({8{attr_busy}} & attr_ra) |
    ({8{gather_busy}} & gather_ra) |
    ({8{vertex_busy}} & vertex_ra) |
    ({8{proj_busy}} & proj_ra) |
    ({8{edge_busy}} & edge_ra) |
    ({8{dot_busy}} & dot_ra) |
    ({8{plane_busy}} & plane_ra) |
    ({8{read_generic}} & read_generic_a);
wire [7:0] read_addr_b=
    ({8{setup_busy}} & setup_rb) |
    ({8{attr_busy}} & attr_rb) |
    ({8{gather_busy}} & gather_rb) |
    ({8{vertex_busy}} & vertex_rb) |
    ({8{proj_busy}} & proj_rb) |
    ({8{edge_busy}} & edge_rb) |
    ({8{dot_busy}} & dot_rb) |
    ({8{plane_busy}} & plane_rb) |
    ({8{read_generic}} & read_generic_b);
// One-hot ownership follows the engine wait state; units cannot overlap.
wire scratch_write=(base_scratch_write) || (state==COPY_RUN) || (setup_busy && setup_write) || (gather_busy && gather_write) || (proj_busy && proj_write) || (edge_busy && edge_write) || (dot_busy && dot_write) || (plane_busy && plane_write);
wire [7:0] write_addr=
    ({8{setup_busy && setup_write}} & setup_wa) |
    ({8{base_scratch_write}} & base_write_addr) |
    ({8{state==COPY_RUN}} & copy_dst) |
    ({8{gather_busy && gather_write}} & gather_wa) |
    ({8{proj_busy && proj_write}} & proj_wa) |
    ({8{edge_busy && edge_write}} & edge_wa) |
    ({8{dot_busy && dot_write}} & dot_wa) |
    ({8{plane_busy && plane_write}} & plane_wa);
wire [31:0] write_data=
    ({32{setup_busy && setup_write}} & setup_wd) |
    ({32{base_scratch_write}} & base_write_data) |
    ({32{state==COPY_RUN}} & (copy_scratch ? qb : bank_rdata)) |
    ({32{gather_busy && gather_write}} & gather_wd) |
    ({32{proj_busy && proj_write}} & proj_wd) |
    ({32{edge_busy && edge_write}} & edge_wd) |
    ({32{dot_busy && dot_write}} & dot_wd) |
    ({32{plane_busy && plane_write}} & plane_wd);
wire [7:0] scratch_port_a_addr = scratch_write ? write_addr : read_addr_a;
always @* begin
    case(code_q[18:16])
        0: branch_take=flag_eq; 1: branch_take=!flag_eq;
        2: branch_take=flag_lt; 3: branch_take=!flag_lt;
        4: branch_take=flag_ltu;5: branch_take=!flag_ltu;
        6: branch_take=flag_c;  7: branch_take=!flag_c;
        default: branch_take=1'b0;
    endcase
    result_valid=1'b1;
    case(op)
        ORI: result=qa|{16'd0,immediate16};
        MOV: result=qa;
        ADD,SUB,ADDI,SUBB: result=sum[31:0];
        ADDC:begin result=EXTENDED_ISA ? sum[31:0] : 32'd0;result_valid=EXTENDED_ISA;end
        AND_OP: result=qa&qb;
        OR_OP: result=qa|qb;
        XOR_OP:begin result=EXTENDED_ISA ? qa^qb : 32'd0;result_valid=EXTENDED_ISA;end
        SHL,SHR,SAR: result=EXTENDED_ISA ? shift_result[31:0] : lean_shift_result;
        ACCLO: result=accumulator[31:0];
        ACCHI: result=accumulator[63:32];
        ACCSAR:begin
            if(EXTENDED_ISA)result=shift_result[31:0];
            else case(immediate)
                1:result=accumulator[32:1];
                16:result=accumulator[47:16];
                30:result=accumulator[61:30];
                default:begin result=0;result_valid=0;end
            endcase
        end
        REM:begin result=EXTENDED_ISA ? architectural_remainder : 32'd0;result_valid=EXTENDED_ISA;end
        SAT:begin result=EXTENDED_ISA ? (accumulator>64'sh7fffffff ? 32'h7fffffff :
                    accumulator < -64'sh80000000 ? 32'h80000000 : accumulator[31:0]) : 32'd0;result_valid=EXTENDED_ISA;end
        NEG:begin result=EXTENDED_ISA ? neg_a : 32'd0;result_valid=EXTENDED_ISA;end
        SEXT:begin
            result=0;result_valid=EXTENDED_ISA;
            if(EXTENDED_ISA)case(immediate)
                0:result={{24{qa[7]}},qa[7:0]};
                1:result={{16{qa[15]}},qa[15:0]};
                2:result={{8{qa[23]}},qa[23:0]};
                default:begin result=0;result_valid=1'b0;end
            endcase
        end
        default:begin result=0;result_valid=1'b0;end
    endcase
    base_scratch_write=1'b0;base_write_addr=rd;base_write_data=result;
    if (state==IDLE && scratch_we) begin
        base_scratch_write=1'b1;base_write_addr=scratch_addr;base_write_data=scratch_data;
    end else if (state==ALU_COMMIT) begin
        base_scratch_write=1'b1;base_write_data=alu_result;
    end
    else if (state==MUL_SAVE) begin
        base_scratch_write=1'b1;base_write_data=accumulator[31:0];
    end else if (state==DIV_COMMIT && div_owner==0) begin
        base_scratch_write=1'b1;base_write_data=div_result[31:0];
    end else if (state==LOAD_COMMIT) begin
        base_scratch_write=1'b1;base_write_data=op==SLOAD ? qa : bank_rdata;
    end else if(state==LOAD_WAIT && op==SSTORE) begin
        base_scratch_write=1'b1;base_write_addr=bank_addr_q;base_write_data=bank_data_q;
    end

end

// Port A is multiplexed between reading A and writing D. Writes happen only
// after operands were captured; no hidden third register-file port is needed.
always @(posedge clk) begin
    if (active && code_we && state==IDLE) begin
        case(code_addr[9:8])
            0:code0[code_addr[7:0]]<=code_data;
            1:code1[code_addr[7:0]]<=code_data;
            default:begin end
        endcase
    end
    code0_q<=code0[code_read_addr[7:0]];
    code1_q<=code1[code_read_addr[7:0]];
    code_page<=code_read_addr[9:8];
    if (active && scratch_write) begin
        scratch[scratch_port_a_addr] <= write_data;
        qa <= write_data;
    end else qa <= scratch[scratch_port_a_addr];
    product <= shared_mul_a * shared_mul_b;
end
always @(posedge clk) qb <= scratch[read_addr_b];
generate if(CODE_PAGES>2) begin:g_code_page2
    (* ramstyle = "M10K" *) reg [31:0] words[0:255];
    reg [31:0] q;
    assign code2_q=q;
    always @(posedge clk) begin
        if(active && code_we && state==IDLE && code_addr[9:8]==2)
            words[code_addr[7:0]]<=code_data;
        q<=words[code_read_addr[7:0]];
    end
end else begin:g_no_code_page2
    assign code2_q=32'hff000000;
end endgenerate


always @(posedge clk) begin
    if (!reset_n || cancel) begin
        state<=IDLE;pc<=0;done<=0;fault<=0;cycles<=0;
        flag_eq<=0;flag_lt<=0;flag_ltu<=0;flag_c<=0;
        accumulator<=0;div_count<=0;
        div_negative<=0;rem_negative<=0;div_signed32<=0;div_zero<=0;div_owner<=0;edge_div_done<=0;proj_div_done<=0;vertex_div_done<=0;
        architectural_remainder<=0;copy_left<=0;
    end else begin
        done<=0;edge_div_done<=0;proj_div_done<=0;vertex_div_done<=0;
        if (busy) cycles<=cycles+32'd1;
        case(state)
        IDLE: if(start) begin
            pc<=entry;state<=FETCH;fault<=0;cycles<=0;
            flag_eq<=0;flag_lt<=0;flag_ltu<=0;flag_c<=0;accumulator<=0;
        end
        FETCH:state<=DECODE;
        DECODE: begin
            op<=code_q[31:24];rd<=code_q[23:16];
            ra<=code_q[31:24]==ORI ? code_q[23:16] : code_q[15:8];
            rb<=(code_q[31:24]==STORE || code_q[31:24]==SSTORE) ? code_q[23:16] : code_q[7:0];
            immediate<=code_q[7:0];immediate16<=code_q[15:0];
            pc<=pc+10'd1;state<=EXEC;
            case(code_q[31:24])
            HALT:begin state<=IDLE;done<=1;end
            LI:begin alu_result<={{16{code_q[15]}},code_q[15:0]};state<=ALU_COMMIT;end
            LUI:begin alu_result<={code_q[15:0],16'd0};state<=ALU_COMMIT;end
            JAL:begin alu_result<={22'd0,pc+10'd1};pc<=code_q[9:0];state<=ALU_COMMIT;end
            JMP:begin pc<=code_q[9:0];state<=DECODE;end
            BR:begin if(branch_take) pc<=code_q[9:0];state<=DECODE;end
            default:begin end
            endcase
        end
        READ:state<=EXEC;
        EXEC:begin
            state<=DECODE;
            if(op==ADD || op==SUB || op==ADDC || op==SUBB || op==CMP) begin
                flag_eq<=qa==qb;flag_lt<=$signed(qa)<$signed(qb);
                flag_ltu<=qa<qb;flag_c<=sum[32];
            end
            if(result_valid) begin alu_result<=result;state<=ALU_COMMIT;end
            else case(op)
                CMP:begin end
                MUL,MAC:if(!EXTENDED_ISA && op==MAC)begin state<=IDLE;done<=1;fault<=1;end
                    else begin mul_a<=qa;mul_b<=qb;state<=MUL_WAIT;end
                ACCSET:accumulator<={qa,qb};
                ACCADD,ACCSUB:if(EXTENDED_ISA)accumulator<=wide_sum;else begin state<=IDLE;done<=1;fault<=1;end
                ACCSHL:if(EXTENDED_ISA)accumulator<=shift_result;else begin state<=IDLE;done<=1;fault<=1;end
                DIVU,DIVS,DIVW:if(!EXTENDED_ISA && op!=DIVW)begin state<=IDLE;done<=1;fault<=1;end else begin
                    div_owner<=0;
                    divisor<=op==DIVW ? {qa[0],qb} :
                        {1'b0,(EXTENDED_ISA && op==DIVS && qb[31] ? (~qb+32'd1) : qb)};
                    quotient<=op==DIVW ? accumulator :
                        {(EXTENDED_ISA && op==DIVS && qa[31] ? (~qa+32'd1) : qa),32'd0};
                    remainder<=0;div_count<=op==DIVW ? 7'd64 : 7'd32;
                    div_signed32<=EXTENDED_ISA && op==DIVS;
                    div_negative<=EXTENDED_ISA && op==DIVS && (qa[31]^qb[31]);
                    rem_negative<=EXTENDED_ISA && op==DIVS && qa[31];
                    div_zero<=qb==0 && (op!=DIVW || !qa[0]);
                    state<=DIV_RUN;
                    if(qb==0 && (op!=DIVW || !qa[0])) begin
                        remainder<=op==DIVW ? {1'b0,accumulator[31:0]} : {1'b0,qa};
                        state<=DIV_COMMIT;
                    end
                end
                ATTR:state<=ATTR_WAIT;
                SETUP:if(COMPACT_LAYOUT)state<=SETUP_WAIT;else begin state<=IDLE;done<=1;fault<=1;end
                GATHER:state<=GATHER_WAIT;
                PROJ,PROJA:if(EXTENDED_ISA || op==PROJA)state<=PROJ_WAIT;
                    else begin state<=IDLE;done<=1;fault<=1;end
                VERTEX:state<=VERTEX_WAIT;
                COPY,SCOPY:begin
                    copy_scratch<=op==SCOPY;
                    copy_src<=qa[7:0];copy_dst<=qb[7:0];copy_left<={1'b0,rd}+9'd1;state<=COPY_REQ;
                end
                OUTBLOCK:begin copy_src<=qa[7:0];copy_left<={1'b0,rd}+9'd1;state<=BURST_REQ;end
                NORM:state<=NORM_WAIT;
                EDGE:state<=EDGE_WAIT;
                MATROW,DOT3:if(ENABLE_DOT)state<=DOT_WAIT;else begin state<=IDLE;done<=1;fault<=1;end
                PLANE,PLANES:if(EXTENDED_ISA || op==PLANES)state<=PLANE_WAIT;
                    else begin state<=IDLE;done<=1;fault<=1;end
                LOAD,STORE,SLOAD,SSTORE:if(!EXTENDED_ISA && op==STORE)begin state<=IDLE;done<=1;fault<=1;end else begin
                    bank_addr_q<=qa[7:0]+immediate;
                    bank_data_q<=qb;state<=LOAD_WAIT;
                end
                OUT:begin emit_data<=qa;state<=EMIT;end
                JREG:begin pc<=qa[9:0];state<=FETCH;end
                default:begin state<=IDLE;done<=1;fault<=1;end
            endcase
        end
        ALU_COMMIT:state<=DECODE;
        MUL_WAIT:state<=MUL_COMMIT;
        MUL_COMMIT:begin accumulator<=(EXTENDED_ISA && op==MAC) ? wide_sum : product;state<=MUL_SAVE;end
        MUL_SAVE:state<=DECODE;
        DIV_RUN:begin
            quotient<={quotient[62:0],div_take};
            remainder<=div_take ? div_sub[32:0] : div_trial[32:0];
            div_count<=div_count-7'd1;
            if(div_count==1) state<=DIV_COMMIT;
        end
        DIV_COMMIT:if(div_owner!=0) begin
            case(div_owner)
                1:begin edge_div_done<=1;state<=EDGE_WAIT;end
                2:begin proj_div_done<=1;state<=PROJ_WAIT;end
                3:begin vertex_div_done<=1;state<=VERTEX_WAIT;end
                default:state<=IDLE;
            endcase
            div_owner<=0;
        end else begin
            accumulator<=div_result;
            architectural_remainder<=(div_signed32 && !div_zero) ? signed_remainder : remainder[31:0];
            if(EXTENDED_ISA && div_signed32 && !div_zero) remainder<={signed_remainder[31],signed_remainder};
            state<=DECODE;
        end
        LOAD_WAIT:state<=op==STORE || op==SSTORE ? DECODE : LOAD_COMMIT;
        LOAD_COMMIT:state<=DECODE;
        EDGE_WAIT:begin
            if(edge_done)state<=DECODE;
            if(edge_div_start)begin
                divisor<={17'd0,edge_div_divisor};quotient<={edge_div_num,32'd0};
                remainder<=0;div_count<=7'd32;div_owner<=1;
                div_signed32<=0;div_negative<=0;rem_negative<=0;div_zero<=0;state<=DIV_RUN;
            end
        end
        PROJ_WAIT:begin
            if(proj_done)state<=DECODE;
            if(proj_div_start)begin
                divisor<={1'b0,proj_div_den};quotient<=0;
                remainder<={2'b0,proj_highbit[31:1]};div_count<=7'd39-{2'b0,proj_log2};div_owner<=2;
                div_signed32<=0;div_negative<=0;rem_negative<=0;div_zero<=0;state<=DIV_RUN;
            end
        end
        VERTEX_WAIT:begin
            if(vertex_done)state<=DECODE;
            if(vertex_div_start)begin
                divisor<={1'b0,vertex_div_den};
                quotient<=vertex_div_bits==32 ? {vertex_div_num[31:0],32'd0} : vertex_div_num;
                remainder<=0;div_count<=vertex_div_bits;div_owner<=3;
                div_signed32<=0;div_negative<=0;rem_negative<=0;div_zero<=0;state<=DIV_RUN;
            end
        end
        COPY_REQ:begin copy_src<=copy_src+8'd1;state<=COPY_RUN;end
        COPY_RUN:begin
            copy_src<=copy_src+8'd1;copy_dst<=copy_dst+8'd1;copy_left<=copy_left-9'd1;
            if(copy_left==1)state<=DECODE;
        end
        BURST_REQ:state<=BURST_EMIT;
        BURST_EMIT:if(out_ready)begin
            copy_src<=copy_src+8'd1;copy_left<=copy_left-9'd1;
            if(copy_left==1)state<=DECODE;
        end
        ATTR_WAIT:if(attr_done)state<=DECODE;
        SETUP_WAIT:if(setup_done)state<=DECODE;
        GATHER_WAIT:if(gather_done)state<=DECODE;
        NORM_WAIT:if(norm_done)state<=DECODE;
        DOT_WAIT:if(dot_done)begin accumulator<=dot_acc;state<=DECODE;end
        PLANE_WAIT:if(plane_done)state<=DECODE;
        EMIT:if(out_ready) state<=DECODE;
        default:begin state<=IDLE;done<=1;fault<=1;end
        endcase
    end
end
endmodule
`default_nettype wire
