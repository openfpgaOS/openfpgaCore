`default_nettype none
module span_walker_tb (
    input wire clk, reset_n, abort_req, start,
    input wire subpix,
    input wire [15:0] x0,y0,x1,y1,x2,y2,cx0,cx1,cy0,cy1,
    input wire old_ready,new_ready,
    input wire new_start,load,
    input wire [3:0] load_index,
    input wire [31:0] load_data,
    output wire old_busy,new_busy,old_valid,new_valid,
    output wire [15:0] old_u,old_v,old_count,new_u,new_v,new_count,
    output wire prepared,
    output wire [287:0] packet
);
gpu_edge_walker #(.EW_PARALLEL_DIVS(0)) original (
    .clk(clk),.reset_n(reset_n),.abort(abort_req),.start(start),.subpix_y(subpix),
    .v0_x(x0),.v0_y(y0),.v1_x(x1),.v1_y(y1),.v2_x(x2),.v2_y(y2),
    .clip_x0(cx0),.clip_x1(cx1),.clip_y0(cy0),.clip_y1(cy1),
    .busy(old_busy),.rec_valid(old_valid),.rec_u(old_u),.rec_v(old_v),
    .rec_count(old_count),.rec_ready(old_ready)
);
gpu_span_walker candidate (
    .clk(clk),.reset_n(reset_n),.abort(abort_req),.start(new_start),
    .load(load),.load_index(load_index),.load_data(load_data),
    .busy(new_busy),.rec_valid(new_valid),.rec_u(new_u),.rec_v(new_v),
    .rec_count(new_count),.rec_ready(new_ready)
);
assign prepared=original.state==original.S_WALK_INIT;
assign packet[0*32+:32]=original.xl;
assign packet[1*32+:32]=original.xr;
assign packet[2*32+:32]=original.slope_long[31:0];
assign packet[3*32+:32]=original.slope_top[31:0];
assign packet[4*32+:32]=original.slope_bot[31:0];
assign packet[5*32+:32]=original.x1_q16+original.bot_mid_off;
assign packet[6*32+:32]={original.y_mid,original.y_cur};
assign packet[7*32+:32]={13'd0,subpix,original.long_left,original.in_bottom_half,original.y_end};
assign packet[8*32+:32]={original.clip_x1,original.clip_x0};
endmodule
`default_nettype wire
