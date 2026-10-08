// Private os30 mesh frontend. Ordinary packets retain their position relative
// to jobs; two owned RAM banks allow next-job DMA during current-job execution.
`default_nettype none
module gpu_mesh_frontend #(
    parameter ADDR_BITS = 12,
    parameter QUEUE_BITS = 8,
    parameter COMPACT_LAYOUT = 0,
    parameter ENABLE_DOT = 1,
    parameter EXTENDED_ISA = 1,
    parameter CODE_PAGES = 3
) (
    input wire clk, reset_n, bus_reset_n, enable, soft_reset, ring_reset,
    input wire [ADDR_BITS-1:0] input_wrptr,
    output reg [ADDR_BITS-1:0] input_rdptr,
    output wire [ADDR_BITS-1:0] input_addr,
    input wire [31:0] input_data,
    input wire [ADDR_BITS-1:0] output_rdptr, output_addr,
    output reg [31:0] output_data,
    output reg [ADDR_BITS-1:0] output_wrptr,
    output wire busy,
    output reg [2:0] fault,
    output wire dbg_engine_busy, dbg_dma_busy,
    output wire [1:0] dbg_bank_owned,
    output wire m_arvalid,
    input wire m_arready,
    output wire [31:0] m_araddr,
    output wire [7:0] m_arlen,
    input wire m_rvalid,
    output wire m_rready,
    input wire [31:0] m_rdata,
    input wire [1:0] m_rresp,
    input wire m_rlast
);
    localparam HEADER=0, DECODE=1, JOB_WAIT=2, JOB_LOAD=3,
        VERT_ISSUE=4, VERT_WAIT=5, INDEX_ISSUE=6, INDEX_WAIT=7,
        STATE_ISSUE=8, PASS_HEADER=9, PASS_WORD=10,
        CODE_WAIT=11, CODE_ADDR=12, CODE_WORD=13;
    reg [3:0] state;
    reg [31:0] header;
    reg [12:0] remaining;
    reg [2:0] field;
    reg job_bad;
    reg [31:0] vertex_addr, index_addr, context_addr;
    reg [8:0] vertex_words, index_words, context_words;
    reg next_fill_bank, next_run_bank, engine_bank, engine_running;
    reg [1:0] pending;
    reg [9:0] entry0, entry1, upload_addr;
    reg [5:0] vertices0, vertices1;
    reg [6:0] triangles0, triangles1;
    reg [1:0] engine_prepare;
    reg code_loaded;
    wire cancelled = soft_reset || ring_reset || (fault != 0);
    wire active = reset_n && !cancelled;
    wire input_empty = input_rdptr == input_wrptr;
    wire [ADDR_BITS-1:0] used_words = output_wrptr - output_rdptr;
    wire fifo_room = {1'b0,used_words} < (1 << QUEUE_BITS);
    wire earlier_done = pending == 0 && !engine_running;
    wire take_input = enable && active && !input_empty &&
        (state == HEADER || state == JOB_LOAD || state == CODE_ADDR ||
         state == CODE_WORD || (state == PASS_WORD && fifo_room));
    assign input_addr = input_rdptr + {{(ADDR_BITS-1){1'b0}},take_input};
    assign busy = !input_empty || state != HEADER || pending != 0 ||
                  engine_running || dbg_dma_busy || (fault != 0);

    wire dma_desc_ready, dma_wr_en, dma_wr_bank;
    wire [7:0] dma_wr_index;
    wire [31:0] dma_wr_data;
    wire [1:0] bank_owned, bank_ready, bank_error;
    wire dma_desc_valid = enable && active && (state == VERT_ISSUE ||
                                  state == INDEX_ISSUE || state == STATE_ISSUE);
    wire [31:0] dma_desc_addr = state == VERT_ISSUE ? vertex_addr :
                                 state == INDEX_ISSUE ? index_addr : context_addr;
    wire [8:0] dma_desc_words = state == VERT_ISSUE ? vertex_words :
                                 state == INDEX_ISSUE ? index_words : context_words;
    wire [7:0] dma_desc_dst = state == VERT_ISSUE ? 8'd0 :
                              state == INDEX_ISSUE ? 8'd192 : 8'd128;
    wire engine_done;
    wire [1:0] engine_fault;
    wire [1:0] release_bank = engine_done && engine_fault == 0 && active ?
                                 (engine_bank ? 2'b10 : 2'b01) : 2'b00;
    assign dbg_bank_owned = bank_owned;
    gpu_mesh_dma dma (
        .clk(clk), .reset_n(bus_reset_n), .abort(!reset_n || cancelled),
        .desc_valid(dma_desc_valid), .desc_ready(dma_desc_ready),
        .desc_bank(next_fill_bank), .desc_addr(dma_desc_addr),
        .desc_dst(dma_desc_dst), .desc_words(dma_desc_words),
        .desc_commit(state == STATE_ISSUE),
        .bank_owned(bank_owned), .bank_ready(bank_ready), .bank_error(bank_error),
        .bank_release(release_bank), .fetch_busy(dbg_dma_busy),
        .wr_en(dma_wr_en), .wr_bank(dma_wr_bank), .wr_index(dma_wr_index), .wr_data(dma_wr_data),
        .m_arvalid(m_arvalid), .m_arready(m_arready), .m_araddr(m_araddr), .m_arlen(m_arlen),
        .m_rvalid(m_rvalid), .m_rready(m_rready), .m_rdata(m_rdata),
        .m_rresp(m_rresp), .m_rlast(m_rlast)
    );

    wire engine_available = enable && active && code_loaded && !engine_running &&
        pending[next_run_bank] && bank_ready[next_run_bank] && !bank_error[next_run_bank];
    // Metadata belongs to its bank until that job retires. Bootstrap driver
    // registers only while the engine is idle, before its first instruction.
    wire engine_scratch_we = engine_available && engine_prepare != 2;
    wire [7:0] engine_scratch_addr = engine_prepare == 0 ? 8'd62 : 8'd63;
    wire [31:0] engine_scratch_data = engine_prepare == 0 ?
        {26'd0,(next_run_bank ? vertices1 : vertices0)} :
        {25'd0,(next_run_bank ? triangles1 : triangles0)};
    wire engine_start = engine_available && engine_prepare == 2;
    wire [7:0] engine_addr;
    wire engine_we, engine_out_valid;
    wire [31:0] engine_wdata, engine_out_data;
    reg [31:0] bank0_q, bank1_q;
    wire [31:0] engine_rdata = engine_bank ? bank1_q : bank0_q;
    wire engine_out_ready = enable && active && fifo_room;
    wire code_we = take_input && state == CODE_WORD;
    gpu_mesh_engine #(.COMPACT_LAYOUT(COMPACT_LAYOUT), .ENABLE_DOT(ENABLE_DOT),
                     .EXTENDED_ISA(EXTENDED_ISA), .CODE_PAGES(CODE_PAGES)) engine (
        .clk(clk), .reset_n(reset_n), .cancel(cancelled),
        .start(engine_start), .entry(next_run_bank ? entry1 : entry0),
        .busy(dbg_engine_busy), .done(engine_done), .fault(engine_fault), .cycles(),
        .code_we(code_we), .code_addr(upload_addr), .code_data(input_data),
        .scratch_we(engine_scratch_we), .scratch_addr(engine_scratch_addr), .scratch_data(engine_scratch_data),
        .bank_addr(engine_addr), .bank_we(engine_we), .bank_wdata(engine_wdata),
        .bank_rdata(engine_rdata), .out_valid(engine_out_valid),
        .out_ready(engine_out_ready), .out_data(engine_out_data)
    );

    // Separate banks provide two physical RAM write ports without inventing a
    // third port on either RAM: DMA and engine never own the same bank.
    (* ramstyle = "M10K" *) reg [31:0] bank0 [0:255];
    (* ramstyle = "M10K" *) reg [31:0] bank1 [0:255];
    wire b0_we = (dma_wr_en && !dma_wr_bank) || (engine_we && !engine_bank);
    wire b1_we = (dma_wr_en && dma_wr_bank) || (engine_we && engine_bank);
    // When the engine is idle its remembered bank can equal the next DMA
    // bank. Select by actual DMA write ownership, not that stale selector.
    always @(posedge clk) begin
        if (b0_we) bank0[dma_wr_en && !dma_wr_bank ? dma_wr_index : engine_addr]
            <= dma_wr_en && !dma_wr_bank ? dma_wr_data : engine_wdata;
        if (b1_we) bank1[dma_wr_en && dma_wr_bank ? dma_wr_index : engine_addr]
            <= dma_wr_en && dma_wr_bank ? dma_wr_data : engine_wdata;
        bank0_q <= bank0[engine_addr];
        bank1_q <= bank1[engine_addr];
    end

    (* ramstyle = "M10K" *) reg [31:0] queue [0:(1<<QUEUE_BITS)-1];
    wire pass_header = enable && active && state == PASS_HEADER && earlier_done && fifo_room;
    wire pass_word = take_input && state == PASS_WORD;
    wire emit = (engine_out_valid && engine_out_ready) || pass_header || pass_word;
    wire [31:0] emit_data = engine_out_valid ? engine_out_data :
                              pass_header ? header : input_data;
    always @(posedge clk) begin
        output_data <= queue[output_addr[QUEUE_BITS-1:0]];
        if (emit) queue[output_wrptr[QUEUE_BITS-1:0]] <= emit_data;
    end

    always @(posedge clk) begin
        if (!reset_n || soft_reset || ring_reset) begin
            input_rdptr <= reset_n && soft_reset && !ring_reset ? input_wrptr : 0;
            output_wrptr <= 0;
            state <= HEADER; header <= 0; remaining <= 0; field <= 0; job_bad <= 0;
            vertex_addr <= 0; index_addr <= 0; context_addr <= 0;
            vertex_words <= 0; index_words <= 0; context_words <= 0;
            next_fill_bank <= 0; next_run_bank <= 0; engine_bank <= 0;
            engine_running <= 0; pending <= 0; entry0 <= 0; entry1 <= 0;
            vertices0 <= 0; vertices1 <= 0; triangles0 <= 0; triangles1 <= 0;
            engine_prepare <= 0;
            upload_addr <= 0; fault <= 0;
            if (!reset_n) code_loaded <= 0;
        end else begin
            if (emit) output_wrptr <= output_wrptr + 1'b1;
            if (engine_scratch_we) engine_prepare <= engine_prepare + 2'd1;
            if (engine_start) begin
                engine_running <= 1; engine_bank <= next_run_bank; engine_prepare <= 0;
            end
            if (engine_done) begin
                engine_running <= 0;
                if (engine_fault != 0) fault <= 3'd3;
                else begin pending[engine_bank] <= 0; next_run_bank <= ~next_run_bank; end
            end
            if (|bank_error) fault <= 3'd2;
            if (enable && active) begin
                if (take_input) input_rdptr <= input_rdptr + 1'b1;
                case (state)
                    HEADER: if (take_input) begin
                        header <= input_data; remaining <= input_data[12:0]; state <= DECODE;
                    end
                    DECODE: begin
                        case (header[31:24])
                            8'h62: if (remaining != 13'd7 || !code_loaded) fault <= 3'd1;
                                   else state <= JOB_WAIT;
                            8'h63: if (remaining < 13'd2 || remaining > 13'd769) fault <= 3'd1;
                                   else state <= CODE_WAIT;
                            default: state <= PASS_HEADER;
                        endcase
                    end
                    JOB_WAIT: if (!pending[next_fill_bank] && !bank_owned[next_fill_bank]) begin
                        pending[next_fill_bank] <= 1; field <= 0; job_bad <= 0; state <= JOB_LOAD;
                    end
                    JOB_LOAD: if (take_input) begin
                        field <= field + 3'd1;
                        case (field)
                            0: vertex_addr <= input_data;
                            1: begin vertex_words <= input_data[8:0];
                                     if (next_fill_bank) vertices1 <= input_data[7:2];
                                     else vertices0 <= input_data[7:2];
                                     if (input_data == 0 || input_data > 128 ||
                                         (COMPACT_LAYOUT && input_data[1:0] != 0)) job_bad <= 1; end
                            2: index_addr <= input_data;
                            3: begin index_words <= input_data[8:0];
                                     if (next_fill_bank) triangles1 <= input_data[31:16] == 0 ?
                                         input_data[6:0] : input_data[22:16];
                                     else triangles0 <= input_data[31:16] == 0 ?
                                         input_data[6:0] : input_data[22:16];
                                     if (input_data[15:0] == 0 || input_data[15:0] > 64 ||
                                         input_data[31:16] > 64 ||
                                         (input_data[31:16] != 0 &&
                                          {1'b0,input_data[15:0]} !=
                                          (({1'b0,input_data[31:16]} + 17'd1) >> 1))) job_bad <= 1; end
                            4: context_addr <= input_data;
                            5: begin context_words <= input_data[8:0];
                                     if (input_data == 0 || input_data > 64) job_bad <= 1; end
                            6: begin
                                if (next_fill_bank) entry1 <= input_data[9:0];
                                else entry0 <= input_data[9:0];
                                if (job_bad || input_data >= CODE_PAGES*256) fault <= 3'd1;
                                else state <= VERT_ISSUE;
                            end
                            default: fault <= 3'd1;
                        endcase
                    end
                    VERT_ISSUE: if (dma_desc_ready) state <= VERT_WAIT;
                    VERT_WAIT: if (!dbg_dma_busy) state <= INDEX_ISSUE;
                    INDEX_ISSUE: if (dma_desc_ready) state <= INDEX_WAIT;
                    INDEX_WAIT: if (!dbg_dma_busy) state <= STATE_ISSUE;
                    STATE_ISSUE: if (dma_desc_ready) begin
                        next_fill_bank <= ~next_fill_bank; state <= HEADER;
                    end
                    PASS_HEADER: if (pass_header) state <= remaining == 0 ? HEADER : PASS_WORD;
                    PASS_WORD: if (take_input) begin
                        remaining <= remaining - 13'd1;
                        if (remaining == 1) state <= HEADER;
                    end
                    CODE_WAIT: if (earlier_done) state <= CODE_ADDR;
                    CODE_ADDR: if (take_input) begin
                        // Payload is start PC followed by instructions; never
                        // let an oversized upload wrap into live program words.
                        if (input_data >= CODE_PAGES*256 || {3'd0,input_data[9:0]} + remaining - 13'd1 > CODE_PAGES*256)
                            fault <= 3'd1;
                        else begin upload_addr <= input_data[9:0]; remaining <= remaining - 13'd1;
                            code_loaded <= 0; state <= CODE_WORD; end
                    end
                    CODE_WORD: if (take_input) begin
                        upload_addr <= upload_addr + 10'd1; remaining <= remaining - 13'd1;
                        if (remaining == 1) begin code_loaded <= 1; state <= HEADER; end
                    end
                    default: fault <= 3'd1;
                endcase
            end
        end
    end
endmodule
`default_nettype wire
