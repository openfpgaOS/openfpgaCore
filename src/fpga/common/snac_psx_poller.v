//
// Autonomous PlayStation SNAC poller
//
// Runs the PSX serial read command at a fixed cadence, decodes buttons/axes,
// latches button edges, and exposes a compact state interface to firmware.
// This avoids synchronous CPU polling of the SNAC shifter in input paths.
//

`default_nettype none

module snac_psx_poller (
    input  wire        clk,
    input  wire        reset_n,

    input  wire        enable,
    input  wire        analog_en,
    input  wire        fast_en,
    input  wire        clear_irq,
    input  wire        clear_edges,

    input  wire [7:0]  pin_in,
    output reg  [7:0]  pin_out,
    output reg  [7:0]  pin_dir,

    output reg         valid,
    output reg         irq_pending,
    output reg [15:0]  buttons,
    output reg [15:0]  buttons_pressed,
    output reg [15:0]  buttons_released,
    output reg [15:0]  raw_buttons_debug,
    output reg [15:0]  joy_lx,
    output reg [15:0]  joy_ly,
    output reg [15:0]  joy_rx,
    output reg [15:0]  joy_ry,
    output reg [7:0]   debug_status
);

localparam [3:0]
    S_IDLE       = 4'd0,
    S_ATT_SETUP  = 4'd1,
    S_BYTE_SETUP = 4'd2,
    S_BIT_SETUP  = 4'd3,
    S_CLK_LOW    = 4'd4,
    S_CLK_HIGH   = 4'd5,
    S_BYTE_GAP   = 4'd6,
    S_FRAME_DONE = 4'd7,
    S_ACK_WAIT_LOW  = 4'd8,
    S_ACK_WAIT_HIGH = 4'd9;

localparam [31:0] POLL_INTERVAL_CYCLES = 32'd200000; // 500 Hz at 100 MHz
localparam [15:0] DIV_NORMAL           = 16'd999;    // 50 KHz
localparam [15:0] DIV_FAST             = 16'd499;    // 100 KHz
localparam [15:0] ATT_SETUP_CYCLES     = 16'd1000;
localparam [15:0] INTERBYTE_CYCLES     = 16'd2000;
localparam [15:0] ACK_LOW_TIMEOUT      = 16'd3000;
localparam [15:0] ACK_RELEASE_TIMEOUT  = 16'd3000;
localparam [6:0]  INVALID_LIMIT        = 7'd64;
localparam [3:0]  BUTTON_FILTER_MAX    = 4'd2;
localparam [3:0]  BUTTON_FILTER_ON     = 4'd1;

reg [3:0]  state;
reg [31:0] poll_timer;
reg [15:0] div_cnt;
reg [3:0]  byte_idx;
reg [2:0]  bit_idx;
reg [7:0]  cmd_byte;
reg [7:0]  rx_byte;
reg [7:0]  frame_id;
reg [7:0]  frame_btn_lo;
reg [7:0]  frame_btn_hi;
reg [7:0]  frame_rx;
reg [7:0]  frame_ry;
reg [7:0]  frame_lx;
reg [7:0]  frame_ly;
reg [3:0]  button_filter [0:15];
reg [6:0]  invalid_count;
reg        att_sel;     // 0=OUT1, 1=OUT2
reg        psx_clk;
reg        psx_cmd;
integer    button_i;

wire [15:0] half_div = fast_en ? DIV_FAST : DIV_NORMAL;
wire        frame_id_is_analog = (frame_id == 8'h53) || (frame_id == 8'h73);
wire [3:0]  last_byte_idx = (analog_en && frame_id_is_analog) ? 4'd8 : 4'd4;
wire        dat_in = pin_in[5];
wire        ack_in = pin_in[4]; // PSX ACK, active low when the adapter exposes it

function [7:0] psx_cmd_for_byte;
    input [3:0] idx;
    begin
        case (idx)
            4'd0: psx_cmd_for_byte = 8'h01;
            4'd1: psx_cmd_for_byte = 8'h42;
            default: psx_cmd_for_byte = 8'h00;
        endcase
    end
endfunction

function psx_id_valid;
    input [7:0] id;
    begin
        psx_id_valid = (id == 8'h41) || (id == 8'h53) || (id == 8'h73);
    end
endfunction

function [15:0] axis_scale;
    input [7:0] value;
    reg signed [8:0] centered;
    begin
        centered = $signed({1'b0, value}) - 9'sd128;
        axis_scale = centered <<< 8;
    end
endfunction

function [15:0] map_buttons;
    input [7:0] btn_lo;
    input [7:0] btn_hi;
    reg [15:0] raw;
    reg [15:0] mapped;
    begin
        raw = ~{btn_hi, btn_lo};
        mapped = 16'd0;

        if (raw[0])  mapped = mapped | 16'h4000; // SELECT
        if (raw[1])  mapped = mapped | 16'h1000; // L3
        if (raw[2])  mapped = mapped | 16'h2000; // R3
        if (raw[3])  mapped = mapped | 16'h8000; // START
        if (raw[4])  mapped = mapped | 16'h0001; // UP
        if (raw[5])  mapped = mapped | 16'h0008; // RIGHT
        if (raw[6])  mapped = mapped | 16'h0002; // DOWN
        if (raw[7])  mapped = mapped | 16'h0004; // LEFT
        if (raw[8])  mapped = mapped | 16'h0400; // L2
        if (raw[9])  mapped = mapped | 16'h0800; // R2
        if (raw[10]) mapped = mapped | 16'h0100; // L1
        if (raw[11]) mapped = mapped | 16'h0200; // R1
        if (raw[12]) mapped = mapped | 16'h0040; // X / Triangle top
        if (raw[13]) mapped = mapped | 16'h0010; // A / Circle right
        if (raw[14]) mapped = mapped | 16'h0020; // B / Cross bottom
        if (raw[15]) mapped = mapped | 16'h0080; // Y / Square left

        map_buttons = mapped;
    end
endfunction

task commit_empty;
    reg [15:0] old_buttons;
    integer i;
    begin
        old_buttons = buttons;
        buttons <= 16'd0;
        joy_lx <= 16'd0;
        joy_ly <= 16'd0;
        joy_rx <= 16'd0;
        joy_ry <= 16'd0;
        for (i = 0; i < 16; i = i + 1)
            button_filter[i] <= 4'd0;
        raw_buttons_debug <= 16'd0;
        buttons_released <= buttons_released | old_buttons;
        if (old_buttons != 16'd0)
            irq_pending <= 1'b1;
    end
endtask

task commit_frame;
    reg [15:0] old_buttons;
    reg [15:0] raw_buttons;
    reg [15:0] accepted_buttons;
    reg [3:0]  next_filter;
    reg [15:0] new_rx;
    reg [15:0] new_ry;
    reg [15:0] new_lx;
    reg [15:0] new_ly;
    integer i;
    begin
        old_buttons = buttons;
        raw_buttons = map_buttons(frame_btn_lo, frame_btn_hi);
        accepted_buttons = 16'd0;
        new_rx = ((frame_id == 8'h53) || (frame_id == 8'h73)) ? axis_scale(frame_rx) : 16'd0;
        new_ry = ((frame_id == 8'h53) || (frame_id == 8'h73)) ? axis_scale(frame_ry) : 16'd0;
        new_lx = ((frame_id == 8'h53) || (frame_id == 8'h73)) ? axis_scale(frame_lx) : 16'd0;
        new_ly = ((frame_id == 8'h53) || (frame_id == 8'h73)) ? axis_scale(frame_ly) : 16'd0;

        for (i = 0; i < 16; i = i + 1) begin
            next_filter = button_filter[i];
            if (raw_buttons[i]) begin
                if (next_filter < BUTTON_FILTER_MAX)
                    next_filter = next_filter + 4'd1;
            end else begin
                if (next_filter != 4'd0)
                    next_filter = next_filter - 4'd1;
            end
            button_filter[i] <= next_filter;
            if (next_filter >= BUTTON_FILTER_ON)
                accepted_buttons[i] = 1'b1;
        end

        buttons <= accepted_buttons;
        raw_buttons_debug <= raw_buttons;
        joy_rx <= new_rx;
        joy_ry <= new_ry;
        joy_lx <= new_lx;
        joy_ly <= new_ly;
        buttons_pressed  <= buttons_pressed  | (accepted_buttons & ~old_buttons);
        buttons_released <= buttons_released | (~accepted_buttons & old_buttons);
        if (accepted_buttons != old_buttons)
            irq_pending <= 1'b1;
    end
endtask

always @(*) begin
    pin_out = 8'hC3;
    pin_dir = 8'hC3; // OUT1, OUT2, IO5/CLK, IO6/CMD

    pin_out[0] = att_sel ? 1'b1 : 1'b0;
    pin_out[1] = att_sel ? 1'b0 : 1'b1;
    pin_out[6] = psx_clk;
    pin_out[7] = psx_cmd;

    if (!enable) begin
        pin_out = 8'hFF;
        pin_dir = 8'h00;
    end else if (state == S_IDLE) begin
        pin_out[0] = 1'b1;
        pin_out[1] = 1'b1;
        pin_out[6] = 1'b1;
        pin_out[7] = 1'b1;
    end
end

always @(posedge clk or negedge reset_n) begin
    if (!reset_n) begin
        state <= S_IDLE;
        poll_timer <= 32'd0;
        div_cnt <= 16'd0;
        byte_idx <= 4'd0;
        bit_idx <= 3'd0;
        cmd_byte <= 8'd0;
        rx_byte <= 8'd0;
        frame_id <= 8'd0;
        frame_btn_lo <= 8'd0;
        frame_btn_hi <= 8'd0;
        frame_rx <= 8'd128;
        frame_ry <= 8'd128;
        frame_lx <= 8'd128;
        frame_ly <= 8'd128;
        invalid_count <= 7'd0;
        att_sel <= 1'b0;
        for (button_i = 0; button_i < 16; button_i = button_i + 1)
            button_filter[button_i] <= 4'd0;
        psx_clk <= 1'b1;
        psx_cmd <= 1'b1;
        valid <= 1'b0;
        irq_pending <= 1'b0;
        buttons <= 16'd0;
        buttons_pressed <= 16'd0;
        buttons_released <= 16'd0;
        raw_buttons_debug <= 16'd0;
        joy_lx <= 16'd0;
        joy_ly <= 16'd0;
        joy_rx <= 16'd0;
        joy_ry <= 16'd0;
        debug_status <= 8'd0;
    end else begin
        if (clear_irq)
            irq_pending <= 1'b0;
        if (clear_edges) begin
            buttons_pressed <= 16'd0;
            buttons_released <= 16'd0;
        end

        if (!enable) begin
            state <= S_IDLE;
            poll_timer <= 32'd0;
            valid <= 1'b0;
            irq_pending <= 1'b0;
            buttons <= 16'd0;
            buttons_pressed <= 16'd0;
            buttons_released <= 16'd0;
            raw_buttons_debug <= 16'd0;
            joy_lx <= 16'd0;
            joy_ly <= 16'd0;
            joy_rx <= 16'd0;
            joy_ry <= 16'd0;
            psx_clk <= 1'b1;
            psx_cmd <= 1'b1;
            invalid_count <= 7'd0;
            for (button_i = 0; button_i < 16; button_i = button_i + 1)
                button_filter[button_i] <= 4'd0;
        end else begin
            case (state)
            S_IDLE: begin
                psx_clk <= 1'b1;
                psx_cmd <= 1'b1;
                if (poll_timer == 32'd0) begin
                    byte_idx <= 4'd0;
                    div_cnt <= ATT_SETUP_CYCLES;
                    state <= S_ATT_SETUP;
                end else begin
                    poll_timer <= poll_timer - 32'd1;
                end
            end

            S_ATT_SETUP: begin
                psx_clk <= 1'b1;
                psx_cmd <= 1'b1;
                if (div_cnt == 16'd0)
                    state <= S_BYTE_SETUP;
                else
                    div_cnt <= div_cnt - 16'd1;
            end

            S_BYTE_SETUP: begin
                cmd_byte <= psx_cmd_for_byte(byte_idx);
                rx_byte <= 8'd0;
                bit_idx <= 3'd0;
                psx_clk <= 1'b1;
                psx_cmd <= |(psx_cmd_for_byte(byte_idx) & 8'h01);
                div_cnt <= half_div;
                state <= S_BIT_SETUP;
            end

            S_BIT_SETUP: begin
                psx_clk <= 1'b1;
                if (div_cnt == 16'd0) begin
                    psx_clk <= 1'b0;
                    div_cnt <= half_div;
                    state <= S_CLK_LOW;
                end else begin
                    div_cnt <= div_cnt - 16'd1;
                end
            end

            S_CLK_LOW: begin
                psx_clk <= 1'b0;
                if (div_cnt == 16'd0) begin
                    rx_byte[bit_idx] <= dat_in;
                    psx_clk <= 1'b1;
                    if (bit_idx != 3'd7)
                        psx_cmd <= cmd_byte[bit_idx + 3'd1];
                    div_cnt <= half_div;
                    state <= S_CLK_HIGH;
                end else begin
                    div_cnt <= div_cnt - 16'd1;
                end
            end

            S_CLK_HIGH: begin
                psx_clk <= 1'b1;
                if (div_cnt == 16'd0) begin
                    if (bit_idx == 3'd7) begin
                        case (byte_idx)
                            4'd1: frame_id <= rx_byte;
                            4'd3: frame_btn_lo <= rx_byte;
                            4'd4: frame_btn_hi <= rx_byte;
                            4'd5: frame_rx <= rx_byte;
                            4'd6: frame_ry <= rx_byte;
                            4'd7: frame_lx <= rx_byte;
                            4'd8: frame_ly <= rx_byte;
                            default: ;
                        endcase

                        if (byte_idx == last_byte_idx) begin
                            state <= S_FRAME_DONE;
                        end else begin
                            byte_idx <= byte_idx + 4'd1;
                            div_cnt <= ACK_LOW_TIMEOUT;
                            state <= S_ACK_WAIT_LOW;
                        end
                    end else begin
                        bit_idx <= bit_idx + 3'd1;
                        psx_clk <= 1'b0;
                        div_cnt <= half_div;
                        state <= S_CLK_LOW;
                    end
                end else begin
                    div_cnt <= div_cnt - 16'd1;
                end
            end

            S_BYTE_GAP: begin
                psx_clk <= 1'b1;
                psx_cmd <= 1'b1;
                if (div_cnt == 16'd0)
                    state <= S_BYTE_SETUP;
                else
                    div_cnt <= div_cnt - 16'd1;
            end

            S_ACK_WAIT_LOW: begin
                psx_clk <= 1'b1;
                psx_cmd <= 1'b1;
                if (!ack_in) begin
                    div_cnt <= ACK_RELEASE_TIMEOUT;
                    state <= S_ACK_WAIT_HIGH;
                end else if (div_cnt == 16'd0) begin
                    div_cnt <= INTERBYTE_CYCLES;
                    state <= S_BYTE_GAP;
                end else begin
                    div_cnt <= div_cnt - 16'd1;
                end
            end

            S_ACK_WAIT_HIGH: begin
                psx_clk <= 1'b1;
                psx_cmd <= 1'b1;
                if (ack_in) begin
                    div_cnt <= INTERBYTE_CYCLES;
                    state <= S_BYTE_GAP;
                end else if (div_cnt == 16'd0) begin
                    div_cnt <= INTERBYTE_CYCLES;
                    state <= S_BYTE_GAP;
                end else begin
                    div_cnt <= div_cnt - 16'd1;
                end
            end

            S_FRAME_DONE: begin
                psx_clk <= 1'b1;
                psx_cmd <= 1'b1;
                poll_timer <= POLL_INTERVAL_CYCLES;
                debug_status <= {att_sel, psx_id_valid(frame_id), frame_id[1:0],
                                 invalid_count[3:0]};

                if (psx_id_valid(frame_id)) begin
                    valid <= 1'b1;
                    invalid_count <= 7'd0;
                    commit_frame();
                end else begin
                    if (invalid_count >= (INVALID_LIMIT - 7'd1)) begin
                        invalid_count <= 7'd0;
                        att_sel <= ~att_sel;
                        valid <= 1'b0;
                        commit_empty();
                    end else begin
                        invalid_count <= invalid_count + 7'd1;
                    end
                end
                state <= S_IDLE;
            end

            default: state <= S_IDLE;
            endcase
        end
    end
end

endmodule
