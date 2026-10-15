// GPU colour/depth render cache (write-back, M10K-backed) between gpu_core's
// SDRAM master ports and the arbiter.  Enabled per target by
// INCLUDE_GPU_RENDER_CACHE (Pocket os30); see core_top.v for the barrier
// wiring.  Promoted from src/fpga/experimental/gpu_color_depth_cache_bram.sv
// (tools/experiments/sm64_cache_fit_20260922, sm64_cache_20260924).
//
// Coherence contract: the cache only sees GPU traffic.  gpu_core requests a
// flush (write back dirty lines, invalidate everything) before fence
// publication, display flips and clear commands, and on GPU_TEX_FLUSH; the
// wrapper also flushes when the GPU goes idle and reports busy until then.
// CPU writes to memory the GPU may have cached must follow the existing
// texture-cache rule (wait for idle/fence, or write GPU_TEX_FLUSH).
// Data + byte-valid/dirty masks use 40-bit simple-dual-port M10K banks.
// Banks are at most 2048 words; reads retain the original one-cycle latency.
// Four synchronous tag banks hold tags and dirty bounds. A miss initializes one line before
// accepting writes; reads fill directly. Cache words never reset globally.
// Dirty victims are written back without waiting for B (see "Posted
// writebacks" below); bus_reset_n clears only that tracking, so a GPU soft
// reset on reset_n cannot lose count of writes still completing downstream.
`default_nettype none
module gpu_color_depth_cache #(
    parameter ADDR_W = 32,
    parameter SET_BITS = 6,
    parameter WAYS = 4,
    parameter WORD_BITS = 4,
    // For a dedicated GPU physical-address bus, omit range decode and the
    // uncached-read path. Flush/idle still govern CPU ownership transfers.
    parameter CACHE_ALL = 0,
    parameter MAX_WRITE_WORDS = 8,
    // Preserve AW/W timing. Capture AR before its hot/tag/data lookup, using
    // existing addr/left registers; one extra cycle for overlapping reads.
    parameter REGISTER_READ_LOOKUP = 0,
    // Break address/posted-line compare -> downstream AR-valid timing.
    // Stable fill ownership makes a one-cycle-late B retirement conservative.
    parameter REGISTER_FILL_HAZARD = 0,
    // Speculate on hot0's data slot before AW is registered. A hot1-only
    // hit re-reads through existing W_WAIT; no live AW tag selects data RAM.
    // With unstaged reads, AR retains its original immediate hot-slot path.
    parameter SPECULATE_WRITE_HOT0 = 0
) (
    input wire clk, reset_n, bus_reset_n,
    input wire [ADDR_W-1:0] range0_lo, range0_hi, range1_lo, range1_hi,
    input wire write_no_allocate,
    input wire flush_req,
    output wire flush_done, busy, has_lines,
    input wire s_arvalid,
    output wire s_arready,
    input wire [ADDR_W-1:0] s_araddr,
    input wire [7:0] s_arlen,
    output reg s_rvalid,
    output reg [31:0] s_rdata,
    output reg s_rlast,
    input wire s_awvalid,
    output wire s_awready,
    input wire [ADDR_W-1:0] s_awaddr,
    input wire [7:0] s_awlen,
    input wire s_wvalid,
    output wire s_wready,
    input wire [31:0] s_wdata,
    input wire [3:0] s_wstrb,
    input wire s_wlast,
    output reg s_bvalid,
    output reg m_arvalid,
    input wire m_arready,
    output reg [ADDR_W-1:0] m_araddr,
    output reg [7:0] m_arlen,
    input wire m_rvalid,
    input wire [31:0] m_rdata,
    input wire m_rlast,
    output reg m_awvalid,
    input wire m_awready,
    output reg [ADDR_W-1:0] m_awaddr,
    output reg [7:0] m_awlen,
    output reg m_wvalid,
    input wire m_wready,
    output reg [31:0] m_wdata,
    output reg [3:0] m_wstrb,
    output reg m_wlast,
    input wire m_bvalid,
    output reg [31:0] hits, misses, writebacks,
    output reg protocol_error
);
localparam WORDS=1<<WORD_BITS, SETS=1<<SET_BITS, WAY_BITS=$clog2(WAYS);
localparam LINE_BITS=WORD_BITS+2, INDEX_BITS=SET_BITS+WAY_BITS;
localparam LINES=SETS*WAYS, DATA_BITS=INDEX_BITS+WORD_BITS;
localparam TAG_BITS=ADDR_W-LINE_BITS-SET_BITS;
localparam META_TAG=2+2*WORD_BITS;
// Power-of-two lanes make a variable way selection a shift. Quartus otherwise
// implements way * (tag width + bounds width) with three DSP multipliers.
localparam META_USED=TAG_BITS+META_TAG, META_BITS=1<<$clog2(META_USED);
localparam INIT=0, IDLE=1, LOOK_WAIT=2, LOOK=3, R_WAIT=4, R_WORD=5,
           W_WAIT=6, W_GET=7, ALLOC=8, WCLEAR=9, FILL_AR=10, FILL_R=11,
           WB_FIRST=14, WB_AW=15, WB_DATA=16,
           BY_AR=18, BY_R=19, BY_AW=20, BY_W=21, BY_B=22,
           MAINT_WAIT=23, MAINT=24, MAINT_DONE=25, R_ACCEPT=26, FILL_CHECK=27;
reg [4:0] state;
reg [ADDR_W-1:0] addr;
reg [8:0] left;
reg read_op, bypass_whole, maintenance, replacing_valid, fill_new;
reg [INDEX_BITS-1:0] walk, slot;
reg [INDEX_BITS:0] live_lines;
reg [WORD_BITS-1:0] beat, dirty_first, dirty_last, wb_pos, wb_last;
reg [TAG_BITS-1:0] victim_tag;
reg bounds_fresh;
reg fwd_stop;       // early restart reached a word the fill does not cover
reg [WORD_BITS-1:0] current_first, current_last;
// Two recently used lines avoid a tag-RAM lookup on alternating color/depth
// accesses. Entries are invalidated on replacement and on maintenance entry.
reg hot0_valid, hot1_valid;
reg [ADDR_W-LINE_BITS-1:0] hot0_line, hot1_line;
reg [INDEX_BITS-1:0] hot0_slot, hot1_slot;
(* ramstyle = "M10K, no_rw_check" *) reg [WAY_BITS-1:0] victim [0:SETS-1];
reg [WAY_BITS-1:0] victim_q;
wire [SET_BITS-1:0] set_index=addr[LINE_BITS+:SET_BITS];
wire [WORD_BITS-1:0] word_index=addr[2+:WORD_BITS];
wire [TAG_BITS-1:0] tag=addr[LINE_BITS+SET_BITS+:TAG_BITS];
wire [ADDR_W-1:0] next_addr=addr+ADDR_W'(4);
wire same_line=(word_index!=WORD_BITS'(WORDS-1)) && cacheable(next_addr);

// Keep each data RAM at most 2048 words deep so its 40 bits pack into
// eight M10Ks. The registered bank selector follows the same read edge as
// the RAM address; selecting with live data_ra would misalign prefetches.
wire [39:0] data_q;
reg data_we;
reg [DATA_BITS-1:0] data_ra, data_wa;
reg [39:0] data_w;
genvar b;
generate if(DATA_BITS<=11) begin: data_single
    (* ramstyle = "M10K, no_rw_check" *) reg [39:0] data_mem [0:LINES*WORDS-1];
    reg [39:0] q;
    always @(posedge clk) begin
        if(data_we) data_mem[data_wa]<=data_w;
        q<=data_mem[data_ra];
`ifdef CACHE_POISON_COLLISIONS
        if(data_we && data_wa==data_ra) q<=~data_w;
`endif
    end
    assign data_q=q;
end else begin: data_banked
    localparam BANK_BITS=DATA_BITS-11, BANKS=1<<BANK_BITS;
    wire [39:0] bank_q [0:BANKS-1];
    reg [BANK_BITS-1:0] read_bank;
    always @(posedge clk) read_bank<=data_ra[DATA_BITS-1:11];
    for(b=0;b<BANKS;b=b+1) begin: bank
        (* ramstyle = "M10K, no_rw_check" *) reg [39:0] data_mem [0:2047];
        reg [39:0] q;
        wire write_here=data_we && data_wa[DATA_BITS-1:11]==BANK_BITS'(b);
        always @(posedge clk) begin
            if(write_here) data_mem[data_wa[10:0]]<=data_w;
            q<=data_mem[data_ra[10:0]];
`ifdef CACHE_POISON_COLLISIONS
            if(write_here && data_wa[10:0]==data_ra[10:0]) q<=~data_w;
`endif
        end
        assign bank_q[b]=q;
    end
    assign data_q=bank_q[read_bank];
end endgenerate
wire [3:0] word_valid=data_q[35:32], word_dirty=data_q[39:36];
reg meta_we;
reg [INDEX_BITS-1:0] meta_wa;
reg [META_BITS-1:0] meta_w;
// Start the synchronous lookup with the accepted request, before addr is
// registered. The selected data word is fetched during LOOK below.
wire [ADDR_W-1:0] lookup_addr=state==IDLE
    ? (REGISTER_READ_LOOKUP ? s_awaddr : (s_awvalid ? s_awaddr : s_araddr)) : addr;
wire [SET_BITS-1:0] meta_ra=maintenance ? walk[INDEX_BITS-1:WAY_BITS]
                                      : lookup_addr[LINE_BITS+:SET_BITS];
// Replacement metadata shares the tag lookup's registered read address.
// INIT/ALLOC writes are never consumed by LOOK on the following cycle, so
// mixed-port read-during-write remains unowned, just as for the tag banks.
wire victim_we=reset_n && ((state==INIT && walk[WAY_BITS-1:0]==0) || state==ALLOC);
wire [SET_BITS-1:0] victim_wa=state==INIT ? walk[INDEX_BITS-1:WAY_BITS] : set_index;
wire [WAY_BITS-1:0] victim_w=state==INIT ? {WAY_BITS{1'b0}} : slot[WAY_BITS-1:0]+1'b1;
always @(posedge clk) begin
    if(victim_we) victim[victim_wa]<=victim_w;
    victim_q<=victim[meta_ra];
`ifdef CACHE_POISON_COLLISIONS
    if(victim_we && victim_wa==meta_ra) victim_q<=~victim_w;
`endif
end
wire [WAYS*META_BITS-1:0] meta_q;
wire hot0_hit=hot0_valid && hot0_line==lookup_addr[ADDR_W-1:LINE_BITS];
wire hot1_hit=hot1_valid && hot1_line==lookup_addr[ADDR_W-1:LINE_BITS];
wire [INDEX_BITS-1:0] hot_slot=hot0_hit ? hot0_slot : hot1_slot;
wire [META_BITS-1:0] active_meta=meta_q[slot[WAY_BITS-1:0]*META_BITS+:META_BITS];
wire old_dirty=bounds_fresh ? active_meta[1] : 1'b1;
wire [WORD_BITS-1:0] old_first=bounds_fresh ? active_meta[2+:WORD_BITS] : current_first;
wire [WORD_BITS-1:0] old_last=bounds_fresh ? active_meta[2+WORD_BITS+:WORD_BITS] : current_last;
wire [WORD_BITS-1:0] new_first=(!old_dirty || word_index<old_first) ? word_index : old_first;
wire [WORD_BITS-1:0] new_last=(!old_dirty || word_index>old_last) ? word_index : old_last;
genvar w;
generate for(w=0;w<WAYS;w=w+1) begin: tag_bank
    (* ramstyle = "M10K, no_rw_check" *) reg [META_BITS-1:0] mem [0:SETS-1];
    reg [META_BITS-1:0] q;
    always @(posedge clk) begin
        if(meta_we && meta_wa[WAY_BITS-1:0]==WAY_BITS'(w))
            mem[meta_wa[INDEX_BITS-1:WAY_BITS]]<=meta_w;
        q<=mem[meta_ra];
`ifdef CACHE_POISON_COLLISIONS
        if(meta_we && meta_wa[WAY_BITS-1:0]==WAY_BITS'(w)
           && meta_wa[INDEX_BITS-1:WAY_BITS]==meta_ra) q<=~meta_w;
`endif
    end
    assign meta_q[w*META_BITS+:META_BITS]=q;
end endgenerate
wire [META_BITS-1:0] walk_meta=meta_q[walk[WAY_BITS-1:0]*META_BITS+:META_BITS];
reg hit, empty;
reg [INDEX_BITS-1:0] selected;
reg [META_BITS-1:0] selected_meta;
always @* begin
    hit=0; empty=0;
    selected={set_index,victim_q};
    selected_meta=meta_q[victim_q*META_BITS+:META_BITS];
    for(integer i=0;i<WAYS;i=i+1) begin
        if(!meta_q[i*META_BITS] && !empty) begin
            empty=1;
            selected={set_index,WAY_BITS'(i)};
            selected_meta=meta_q[i*META_BITS+:META_BITS];
        end
    end
    for(integer i=0;i<WAYS;i=i+1) begin
        if(meta_q[i*META_BITS] && meta_q[i*META_BITS+META_TAG+:TAG_BITS]==tag) begin
            hit=1; selected={set_index,WAY_BITS'(i)};
            selected_meta=meta_q[i*META_BITS+:META_BITS];
        end
    end
end

function automatic cacheable(input [ADDR_W-1:0] a);
    cacheable=CACHE_ALL || (a>=range0_lo && a<range0_hi) || (a>=range1_lo && a<range1_hi);
endfunction
function automatic overlaps(input [ADDR_W-1:0] a,input [7:0] len);
    reg [ADDR_W:0] finish;
    begin
        finish={1'b0,a}+((ADDR_W+1)'(len)+1)*4;
        overlaps=CACHE_ALL || (range0_lo<range0_hi && a<range0_hi && finish>{1'b0,range0_lo})
              || (range1_lo<range1_hi && a<range1_hi && finish>{1'b0,range1_lo});
    end
endfunction
function automatic [WORD_BITS-1:0] chunk_end(input [WORD_BITS-1:0] first,last);
    if (MAX_WRITE_WORDS<WORDS && ((WORD_BITS+1)'(last)-(WORD_BITS+1)'(first))>=(WORD_BITS+1)'(MAX_WRITE_WORDS))
        chunk_end=first+WORD_BITS'(MAX_WRITE_WORDS-1);
    else chunk_end=last;
endfunction

// Posted writes: a victim's W beats leave and the line is refilled while its
// B is still pending, instead of stalling the waiting GPU read for a full
// SDRAM write commit (os30 SM64: ~20% of GPU cycles).  Bypass write bursts of
// at most one line (clears, uncached words) are posted the same way and get
// their upstream B at once.  The Pocket arbiter lets reads overtake queued
// writes without address checks, so a fill of a line with a posted write in
// flight waits for it.  Bypass reads and longer bypass bursts wait until none
// is pending, which also keeps every B unambiguous (B returns in order).  busy
// and flush_done cover posted writes, so barriers still mean "in SDRAM".
localparam WB_POST=4, WB_PTR=$clog2(WB_POST);
reg [WB_POST-1:0] wb_valid, wb_cross;   // cross: the burst also covers line+1
reg [ADDR_W-LINE_BITS-1:0] wb_line [0:WB_POST-1];
reg [WB_PTR-1:0] wb_head, wb_tail;
wire wb_any=|wb_valid, wb_full=&wb_valid;
wire [ADDR_W-LINE_BITS-1:0] cur_line=addr[ADDR_W-1:LINE_BITS], prev_line=cur_line-1'b1;
reg fill_hazard;
reg fill_hazard_r;
wire fill_blocked=REGISTER_FILL_HAZARD ? fill_hazard_r : fill_hazard;
// FILL_CHECK is visited on both fill entrances before AR can become valid.
// During FILL_CHECK/FILL_AR addr cannot change and no downstream AW is
// generated, so posted hazards can only disappear as B responses retire.
always @(posedge clk) begin
    if(!reset_n) fill_hazard_r<=1'b1;
    else if(state==FILL_CHECK || state==FILL_AR) fill_hazard_r<=fill_hazard;
end
always @* begin
    fill_hazard=0;
    for(integer i=0;i<WB_POST;i=i+1)
        if(wb_valid[i] && (wb_line[i]==cur_line || (wb_cross[i] && wb_line[i]==prev_line)))
            fill_hazard=1;
end
// A bypass burst is posted when it spans at most one line's worth of words.
wire [8:0] by_len=bypass_whole ? left : 9'd1;
wire by_post=by_len<=9'(WORDS);
wire by_cross=(9'(word_index)+by_len-9'd1)>=9'(WORDS);
reg by_posted;
// Early restart: forward the current requested word while its fill beat lands.
wire fwd_now=state==FILL_R && m_rvalid && left!=0 && !fwd_stop && beat==word_index;
always @(posedge clk) begin
    if(!bus_reset_n) begin
        wb_valid<=0; wb_head<=0; wb_tail<=0;
    end else begin
        if(m_awvalid && m_awready && (state==WB_AW || (state==BY_AW && by_post))) begin
            wb_valid[wb_tail]<=1; wb_cross[wb_tail]<=state==BY_AW && by_cross;
            wb_line[wb_tail]<=state==WB_AW ? {victim_tag,slot[INDEX_BITS-1:WAY_BITS]} : cur_line;
            wb_tail<=wb_tail+1'b1;
        end
        if(m_bvalid && wb_valid[wb_head]) begin
            wb_valid[wb_head]<=0; wb_head<=wb_head+1'b1;
        end
    end
end

assign has_lines=(live_lines!=0);
assign busy=(state!=IDLE && state!=MAINT_DONE) || wb_any;
assign flush_done=state==MAINT_DONE && !wb_any;
assign s_awready=state==IDLE && !flush_req;
assign s_arready=state==IDLE && !flush_req && !s_awvalid;
// A full word replaces all 40 data/valid/dirty bits. W_WAIT already
// owns its slot and metadata, so its stale data_q is irrelevant for this beat.
wire write_get=(state==W_GET) || (SPECULATE_WRITE_HOT0 && state==W_WAIT && s_wstrb==4'hf);
assign s_wready=write_get || (state==BY_W && m_wready);

always @* begin
    m_arvalid=0; m_araddr=addr; m_arlen=0;
    m_awvalid=0; m_awaddr=addr; m_awlen=0;
    m_wvalid=0; m_wdata=s_wdata; m_wstrb=s_wstrb; m_wlast=left==1;
    case(state)
        FILL_AR: begin
            m_arvalid=!fill_blocked; m_araddr={addr[ADDR_W-1:LINE_BITS],{LINE_BITS{1'b0}}};
            m_arlen=8'(WORDS-1);
        end
        BY_AR: begin m_arvalid=!wb_any; m_arlen=bypass_whole ? 8'(left-1) : 0; end
        BY_AW: begin m_awvalid=by_post ? !wb_full : !wb_any; m_awlen=bypass_whole ? 8'(left-1) : 0; end
        BY_W: begin m_wvalid=s_wvalid; m_wlast=bypass_whole ? left==1 : 1'b1; end
        WB_AW: begin
            m_awvalid=!wb_full;
            m_awaddr={victim_tag,slot[INDEX_BITS-1:WAY_BITS],wb_pos,2'b00};
            m_awlen=8'(wb_last-wb_pos);
        end
        WB_DATA: begin
            m_wvalid=1; m_wdata=data_q[31:0]; m_wstrb=word_dirty; m_wlast=wb_pos==wb_last;
        end
        default: ;
    endcase
end

// Only this mux writes each storage array; RAM inference must not depend on
// proving mutually exclusive assignments scattered through the control FSM.
always @* begin
    data_we=0; data_wa={slot,word_index}; data_w=data_q;
    data_ra={slot,word_index};
    meta_we=0; meta_wa=slot; meta_w=0;
    case(state)
        INIT: begin meta_we=reset_n; meta_wa=walk; end
        IDLE: data_ra={(SPECULATE_WRITE_HOT0 && (REGISTER_READ_LOOKUP || s_awvalid))
                       ? hot0_slot : hot_slot,lookup_addr[2+:WORD_BITS]};
        // R_ACCEPT owns addr. Do not route the live IDLE address mux
        // through this RAM input: hot_slot is equivalent in this state,
        // but includes an otherwise unowned upstream AW tag comparison.
        R_ACCEPT: data_ra={(hot0_valid && hot0_line==addr[ADDR_W-1:LINE_BITS])
                           ? hot0_slot : hot1_slot,word_index};
        LOOK: data_ra={selected,word_index};
        ALLOC: begin meta_we=1; meta_w={tag,{(2*WORD_BITS){1'b0}},2'b01}; end
        WCLEAR: begin data_we=1; data_wa={slot,beat}; data_w=0; end
        W_WAIT, W_GET: begin
            if(write_get && s_wvalid) begin
                data_we=1;
                data_w[39:36]=word_dirty|s_wstrb;
                data_w[35:32]=word_valid|s_wstrb;
                for(integer b=0;b<4;b=b+1) if(s_wstrb[b])
                    data_w[b*8+:8]=s_wdata[b*8+:8];
                if(s_wstrb!=0) begin meta_we=1; meta_w={tag,new_last,new_first,2'b11}; end
                if(left!=1 && same_line) data_ra={slot,word_index+WORD_BITS'(1)};
            end
        end
// An invalid word enters FILL_CHECK/FILL_AR, which do not consume
        // this speculative read. FILL_AR fetches word zero before FILL_R
        // can merge any data. Keep validity in the response/FSM decision,
        // but remove the RAM-output feedback from next-word read address.
        R_WORD: if(left!=1 && same_line)
            data_ra={slot,word_index+WORD_BITS'(1)};
        FILL_AR: data_ra={slot,{WORD_BITS{1'b0}}};
        FILL_R: begin
            data_ra={slot,beat};
            if(m_rvalid) begin
                data_we=1; data_wa={slot,beat};
                data_w[39:36]=fill_new ? 4'd0 : word_dirty;
                data_w[35:32]=4'hf;
                for(integer b=0;b<4;b=b+1) if(fill_new || !word_valid[b])
                    data_w[b*8+:8]=m_rdata[b*8+:8];
                data_ra={slot,beat+WORD_BITS'(1)};
            end
        end
        WB_FIRST: data_ra={slot,dirty_first};
        WB_AW: data_ra={slot,wb_pos};
        WB_DATA: begin
            data_ra={slot,wb_pos};
            if(m_wready && wb_pos!=wb_last) data_ra={slot,wb_pos+WORD_BITS'(1)};
        end
        MAINT: if(!walk_meta[1] || !walk_meta[0]) begin meta_we=1; meta_wa=walk; end
        default: ;
    endcase
    if(state==WB_DATA && m_wready && wb_pos==wb_last && wb_last==dirty_last && maintenance) begin
        meta_we=1; meta_wa=walk; meta_w=0;
    end
end

always @(posedge clk) begin
    if(!reset_n) begin
        state<=INIT; walk<=0; slot<=0; live_lines<=0;
        addr<=0; left<=0; read_op<=0; bypass_whole<=0; maintenance<=0;
        replacing_valid<=0; fill_new<=0; beat<=0; dirty_first<=0; dirty_last<=0;
        by_posted<=0; fwd_stop<=0;
        wb_pos<=0; wb_last<=0; victim_tag<=0;
        bounds_fresh<=1; current_first<=0; current_last<=0;
        hot0_valid<=0; hot1_valid<=0; hot0_line<=0; hot1_line<=0; hot0_slot<=0; hot1_slot<=0;
        s_rvalid<=0; s_rdata<=0; s_rlast<=0; s_bvalid<=0;
        hits<=0; misses<=0; writebacks<=0; protocol_error<=0;
    end else begin
        s_rvalid<=0; s_rlast<=0; s_bvalid<=0;
        case(state)
            INIT: begin
                if(walk==INDEX_BITS'(LINES-1)) state<=IDLE;
                else walk<=walk+1'b1;
            end
            IDLE: begin
                maintenance<=0;
                if(flush_req) begin
                    walk<=0; maintenance<=1; state<=MAINT_WAIT;
                    hot0_valid<=0; hot1_valid<=0;
                end
                else if(s_awvalid) begin
                    // The accepted AW owns this candidate slot. On a miss,
                    // LOOK replaces it before any data/metadata consumer;
                    // bypass states never consume it. Flush still wins above.
                    // Use the owned AW address, avoiding the shared live/
                    // registered lookup mux and hit qualification on slot CE.
                    slot<=(hot0_valid && hot0_line==s_awaddr[ADDR_W-1:LINE_BITS])
                        ? hot0_slot : hot1_slot;
                    addr<=s_awaddr; left<={1'b0,s_awlen}+9'd1; read_op<=0;
                    if(!overlaps(s_awaddr,s_awlen) || (!has_lines && (write_no_allocate || s_awlen>=7))) begin
                        bypass_whole<=1; state<=BY_AW;
                    end else begin
                        bypass_whole<=0; bounds_fresh<=1;
                        if((hot0_hit || hot1_hit) && cacheable(s_awaddr)) begin
                            hits<=hits+1;
                            state<=(SPECULATE_WRITE_HOT0 && !hot0_hit) ? W_WAIT : W_GET;
                        end else state<=LOOK;
                    end
                end else if(s_arvalid) begin
                    addr<=s_araddr; left<={1'b0,s_arlen}+9'd1; read_op<=1;
                    if(!overlaps(s_araddr,s_arlen)) begin bypass_whole<=1; state<=BY_AR; end
                    else begin
                        bypass_whole<=0;
                        if(REGISTER_READ_LOOKUP) state<=R_ACCEPT;
                        else if((hot0_hit || hot1_hit) && cacheable(s_araddr)) begin
                            slot<=hot_slot; hits<=hits+1; state<=R_WORD;
                        end else state<=LOOK;
                    end
                end
            end
            // IDLE already accepted and owns the read: AW and flush cannot
            // overtake it. Both RAMs now see registered addr. Their outputs
            // are ready when R_WORD/LOOK execute on the next cycle.
            R_ACCEPT: begin
                if((hot0_hit || hot1_hit) && cacheable(addr)) begin
                    slot<=hot_slot; hits<=hits+1; state<=R_WORD;
                end else state<=LOOK;
            end
            LOOK_WAIT: state<=LOOK;
            LOOK: begin
                if(!cacheable(addr)) state<=read_op ? BY_AR : BY_AW;
                else begin
                    slot<=selected;
                    bounds_fresh<=1;
                    if(hit) begin
                        hits<=hits+1; state<=read_op ? R_WORD : W_GET;
                        if(!hot0_valid || hot0_line!=addr[ADDR_W-1:LINE_BITS]) begin
                            hot1_valid<=hot0_valid; hot1_line<=hot0_line; hot1_slot<=hot0_slot;
                            hot0_valid<=1; hot0_line<=addr[ADDR_W-1:LINE_BITS]; hot0_slot<=selected;
                        end
                    end
                    else begin
                        misses<=misses+1; replacing_valid<=selected_meta[0];
                        victim_tag<=selected_meta[META_BITS-1:META_TAG];
                        dirty_first<=selected_meta[2+:WORD_BITS];
                        dirty_last<=selected_meta[2+WORD_BITS+:WORD_BITS];
                        if(selected_meta[0] && selected_meta[1]) state<=WB_FIRST;
                        else state<=ALLOC;
                    end
                end
            end
            ALLOC: begin
                if(!replacing_valid) live_lines<=live_lines+1;
                hot1_valid<=hot0_valid && hot0_slot!=slot;
                hot1_line<=hot0_line; hot1_slot<=hot0_slot;
                hot0_valid<=1; hot0_line<=addr[ADDR_W-1:LINE_BITS]; hot0_slot<=slot;
                beat<=0; fill_new<=1;
                state<=read_op ? (REGISTER_FILL_HAZARD ? FILL_CHECK : FILL_AR) : WCLEAR;
            end
            WCLEAR: begin
                if(beat==WORD_BITS'(WORDS-1)) state<=W_WAIT;
                else beat<=beat+1'b1;
            end
            W_WAIT, W_GET: begin
                // W_WAIT without an accepted full beat fetches current data;
                // an accepted same-line beat fetches the next word instead.
                state<=W_GET;
                if(write_get && s_wvalid) begin
                if(s_wstrb!=0) begin
                    current_first<=new_first; current_last<=new_last; bounds_fresh<=0;
                end
                if(s_wlast!=(left==1)) protocol_error<=1;
                if(left==1) begin s_bvalid<=1; state<=IDLE; end
                else begin
                    addr<=next_addr; left<=left-1;
                    if(!same_line) state<=LOOK_WAIT;
                end
            end
            end
            R_WAIT: state<=R_WORD;
            R_WORD: begin
                if(word_valid!=4'hf) begin fill_new<=0; state<=REGISTER_FILL_HAZARD ? FILL_CHECK : FILL_AR; end
                else begin
                    s_rvalid<=1; s_rdata<=data_q[31:0]; s_rlast<=left==1;
                    if(left==1) state<=IDLE;
                    else begin
                        addr<=next_addr; left<=left-1;
                        if(!same_line) state<=LOOK_WAIT;
                    end
                end
            end
            FILL_CHECK: state<=FILL_AR;
            FILL_AR: if(m_arready && !fill_blocked) begin beat<=0; fwd_stop<=0; state<=FILL_R; end
            FILL_R: if(m_rvalid) begin
                // Early restart: each requested word goes upstream as its beat
                // arrives (the merged value written below), not after the fill.
                if(m_rlast!=(beat==WORD_BITS'(WORDS-1))) protocol_error<=1;
                if(fwd_now) begin
                    s_rvalid<=1; s_rdata<=data_w[31:0]; s_rlast<=left==1;
                    if(left==1) left<=0;
                    else begin
                        addr<=next_addr; left<=left-1;
                        if(!same_line) fwd_stop<=1;
                    end
                end
                if(beat==WORD_BITS'(WORDS-1)) begin
                    if(left==0 || (fwd_now && left==1)) state<=IDLE;
                    else if(fwd_stop || fwd_now) state<=LOOK_WAIT;
                    else state<=R_WAIT;
                end
                else beat<=beat+1'b1;
            end
            WB_FIRST: begin wb_pos<=dirty_first; wb_last<=chunk_end(dirty_first,dirty_last); state<=WB_AW; end
            WB_AW: if(m_awready && !wb_full) state<=WB_DATA;
            WB_DATA: if(m_wready) begin
                if(wb_pos!=wb_last) wb_pos<=wb_pos+1'b1;
                else begin
                    // Posted: the next chunk, the refill or the next flush line
                    // starts now; the tracker above waits for this burst's B.
                    writebacks<=writebacks+1;
                    if(wb_last!=dirty_last) begin dirty_first<=wb_last+1'b1; state<=WB_FIRST; end
                    else if(maintenance) begin
                        live_lines<=live_lines-1;
                        if(walk==INDEX_BITS'(LINES-1)) state<=MAINT_DONE;
                        else begin walk<=walk+1'b1; state<=MAINT_WAIT; end
                    end else state<=ALLOC;
                end
            end
            BY_AR: if(m_arready && !wb_any) state<=BY_R;
            BY_R: if(m_rvalid) begin
                s_rvalid<=1; s_rdata<=m_rdata; s_rlast<=left==1;
                if(m_rlast!=(bypass_whole ? left==1 : 1'b1)) protocol_error<=1;
                if(left==1) state<=IDLE;
                else begin addr<=next_addr; left<=left-1; if(!bypass_whole) state<=LOOK_WAIT; end
            end
            BY_AW: if(m_awready && (by_post ? !wb_full : !wb_any)) begin by_posted<=by_post; state<=BY_W; end
            BY_W: if(s_wvalid && m_wready) begin
                if(s_wlast!=(left==1)) protocol_error<=1;
                if(bypass_whole && left!=1) begin addr<=next_addr; left<=left-1; end
                else if(!by_posted) state<=BY_B;
                // Posted: the tracker waits for this burst's B.
                else if(left==1) begin s_bvalid<=1; state<=IDLE; end
                else begin addr<=next_addr; left<=left-1; state<=LOOK_WAIT; end
            end
            BY_B: if(m_bvalid) begin
                if(left==1) begin s_bvalid<=1; state<=IDLE; end
                else begin addr<=next_addr; left<=left-1; state<=LOOK_WAIT; end
            end
            MAINT_WAIT: state<=MAINT;
            MAINT: begin
                if(walk_meta[0] && walk_meta[1]) begin
                    slot<=walk; victim_tag<=walk_meta[META_BITS-1:META_TAG];
                    dirty_first<=walk_meta[2+:WORD_BITS];
                    dirty_last<=walk_meta[2+WORD_BITS+:WORD_BITS]; state<=WB_FIRST;
                end else begin
                    if(walk_meta[0]) live_lines<=live_lines-1;
                    if(walk==INDEX_BITS'(LINES-1)) state<=MAINT_DONE;
                    else begin walk<=walk+1'b1; state<=MAINT_WAIT; end
                end
            end
            MAINT_DONE: if(!flush_req) begin maintenance<=0; state<=IDLE; end
            default: begin protocol_error<=1; state<=INIT; walk<=0; live_lines<=0; end
        endcase
    end
end
initial begin
    if(WAYS<2 || (WAYS & (WAYS-1))!=0 || SET_BITS<1 || WORD_BITS<1 || WORD_BITS>8 || TAG_BITS<1)
        $fatal(1,"unsupported cache geometry");
    if(MAX_WRITE_WORDS<1 || MAX_WRITE_WORDS>256) $fatal(1,"unsupported write burst limit");
end
endmodule
`default_nettype wire
