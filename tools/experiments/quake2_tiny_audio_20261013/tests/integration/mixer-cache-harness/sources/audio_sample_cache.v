// SPDX-License-Identifier: Apache-2.0
// Optional per-voice read cache. One32-byte line per logical mixer voice.
// Stream requests and requests accepted while disabled bypass unchanged.
// An invalidation may leave the already accepted response in flight; the
// mixer owns cancellation of that response. It prevents later line reuse.
`default_nettype none
module audio_sample_cache (
    input wire clk,
    input wire reset_n,
    input wire cache_enable,
    input wire request_cacheable,
    input wire [4:0] request_voice,
    input wire invalidate_valid,
    input wire [4:0] invalidate_voice,

    input wire s_arvalid,
    output wire s_arready,
    input wire [31:0] s_araddr,
    input wire [7:0] s_arlen,
    output wire s_rvalid,
    input wire s_rready,
    output wire [31:0] s_rdata,
    output wire [1:0] s_rresp,
    output wire s_rlast,

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
localparam IDLE=4'd0, TAG_WAIT=4'd1, CHECK=4'd2, DATA_WAIT=4'd3,
           REPLY=4'd4, FILL_AR=4'd5, FILL_R=4'd6,
           BYPASS_AR=4'd7, BYPASS_R=4'd8;
reg [3:0] state;
reg [4:0] voice;
reg [31:0] address;
reg [7:0] remaining;
reg canceled;

// Reads are synchronous. A read result is consumed only after its WAIT
// state. During fills neither read result is consumed; on a same-address
// read/write the unspecified RAM output is therefore harmless.
(* ramstyle = "M10K, no_rw_check" *) reg [31:0] data_mem [0:255];
(* ramstyle = "M10K, no_rw_check" *) reg [26:0] tag_mem [0:31];
reg [7:0] data_read_address;
reg [4:0] tag_read_address;
reg [31:0] data_q;
reg [26:0] tag_q;
reg [31:0] valid;

reg [2:0] fill_word;
reg [1:0] fill_error;
reg transient_valid;
reg [1:0] transient_error;

wire accepted = s_arvalid && s_arready;
wire invalidating_current = invalidate_valid && invalidate_voice == voice;
wire fill_beat = state == FILL_R && m_rvalid;
wire fill_done = fill_beat && m_rlast;
wire [1:0] ending_error = fill_error | m_rresp |
                         ((fill_word != 3'd7) ? 2'b10 : 2'b00);
wire commit_line = fill_done && ending_error == 2'b00 &&
                   !canceled && cache_enable && !invalidating_current;
wire transient_hit = transient_valid;
wire stored_hit = valid[voice] && tag_q == address[31:5] &&
                  cache_enable && !canceled && !invalidating_current;

assign s_arready = state == IDLE;
assign s_rvalid = state == REPLY || (state == BYPASS_R && m_rvalid);
assign s_rdata = state == BYPASS_R ? m_rdata : data_q;
assign s_rresp = state == BYPASS_R ? m_rresp : transient_error;
assign s_rlast = state == BYPASS_R ? m_rlast : remaining == 8'd0;
assign m_arvalid = state == FILL_AR || state == BYPASS_AR;
assign m_araddr = state == FILL_AR ? {address[31:5],5'b0} : address;
assign m_arlen = state == FILL_AR ? 8'd7 : remaining;
assign m_rready = state == FILL_R || (state == BYPASS_R && s_rready);

always @(posedge clk) begin
    data_q <= data_mem[data_read_address];
    tag_q <= tag_mem[tag_read_address];
    if (fill_beat)
        data_mem[{voice,fill_word}] <= m_rdata;
    if (commit_line)
        tag_mem[voice] <= address[31:5];
end

always @(posedge clk) begin
    if (!reset_n) begin
        valid <= 32'b0;
    end else if (!cache_enable) begin
        valid <= 32'b0;
    end else begin
        if (state == CHECK && !transient_hit && !stored_hit)
            valid[voice] <= 1'b0;
        if (commit_line)
            valid[voice] <= 1'b1;
        // Invalidation wins over a simultaneous refill commit.
        if (invalidate_valid)
            valid[invalidate_voice] <= 1'b0;
    end
end

always @(posedge clk) begin
    if (!reset_n) begin
        state <= IDLE;
        voice <= 0;
        address <= 0;
        remaining <= 0;
        canceled <= 0;
        data_read_address <= 0;
        tag_read_address <= 0;
        fill_word <= 0;
        fill_error <= 0;
        transient_valid <= 0;
        transient_error <= 0;
    end else begin
        if (state != IDLE && (!cache_enable || invalidating_current))
            canceled <= 1'b1;
        case (state)
            IDLE: if (accepted) begin
                voice <= request_voice;
                address <= s_araddr;
                remaining <= s_arlen;
                tag_read_address <= request_voice;
                transient_valid <= 1'b0;
                canceled <= invalidate_valid && invalidate_voice == request_voice;
                // The mixer issues word-aligned requests. Be conservative
                // for any other requester: preserve the original burst.
                state <= cache_enable && request_cacheable && s_araddr[1:0] == 0
                         ? TAG_WAIT : BYPASS_AR;
            end
            TAG_WAIT: state <= CHECK;
            CHECK: begin
                if (transient_hit || stored_hit) begin
                    data_read_address <= {voice,address[4:2]};
                    if (!transient_hit) begin
                        transient_valid <= 1'b1;
                        transient_error <= 2'b00;
                    end
                    state <= DATA_WAIT;
                end else begin
                    fill_word <= 3'd0;
                    fill_error <= 2'b00;
                    transient_valid <= 1'b0;
                    state <= FILL_AR;
                end
            end
            DATA_WAIT: state <= REPLY;
            REPLY: if (s_rready) begin
                if (remaining == 0) state <= IDLE;
                else begin
                    remaining <= remaining - 8'd1;
                    address <= address + 32'd4;
                    // The in-flight line is usable until this request
                    // crosses its32-byte boundary, even if invalidated.
                    if (address[4:2] == 3'd7) transient_valid <= 1'b0;
                    // Adjacent interpolation taps often straddle a line and
                    // repeat for several output samples. Fetching a whole
                    // second line would evict the first and thrash on every
                    // repetition. The final crossing word bypasses without
                    // allocation, retaining the useful first line.
                    state <= remaining == 8'd1 && address[4:2] == 3'd7
                             ? BYPASS_AR : CHECK;
                end
            end
            FILL_AR: if (m_arready) state <= FILL_R;
            FILL_R: if (m_rvalid) begin
                fill_error <= fill_error | m_rresp;
                if (m_rlast) begin
                    // Even an invalidated/error line can finish this one
                    // accepted burst, but it never becomes a reusable line.
                    transient_valid <= 1'b1;
                    transient_error <= ending_error;
                    state <= CHECK;
                end else fill_word <= fill_word + 3'd1;
            end
            BYPASS_AR: if (m_arready) state <= BYPASS_R;
            BYPASS_R: if (m_rvalid && s_rready && m_rlast) state <= IDLE;
            default: state <= IDLE;
        endcase
    end
end
endmodule
`default_nettype wire
