`default_nettype none

module tb_core_bridge_cmd (
    input  wire        clk,

    input  wire        bridge_endian_little,
    input  wire [31:0] bridge_addr,
    input  wire        bridge_rd,
    output wire [31:0] bridge_rd_data,
    input  wire        bridge_wr,
    input  wire [31:0] bridge_wr_data,

    input  wire        status_boot_done,
    input  wire        status_setup_done,
    input  wire        status_running,
    input  wire        shutdown_ack_s,

    output wire        reset_n,
    output wire        shutdown_pending
);

wire        dataslot_requestread;
wire [15:0] dataslot_requestread_id;
wire        dataslot_requestwrite;
wire [15:0] dataslot_requestwrite_id;
wire [31:0] dataslot_requestwrite_size;
wire        dataslot_update;
wire [15:0] dataslot_update_id;
wire [31:0] dataslot_update_size;
wire        dataslot_allcomplete;
wire [31:0] rtc_epoch_seconds;
wire [31:0] rtc_date_bcd;
wire [31:0] rtc_time_bcd;
wire        rtc_valid;
wire        osnotify_inmenu;
wire        savestate_start;
wire        savestate_load;
wire        target_dataslot_ack;
wire        target_dataslot_done;
wire [2:0]  target_dataslot_err;
wire [31:0] datatable_q;

core_bridge_cmd dut (
    .clk                (clk),
    .reset_n            (reset_n),

    .bridge_endian_little(bridge_endian_little),
    .bridge_addr        (bridge_addr),
    .bridge_rd          (bridge_rd),
    .bridge_rd_data     (bridge_rd_data),
    .bridge_wr          (bridge_wr),
    .bridge_wr_data     (bridge_wr_data),

    .shutdown_pending   (shutdown_pending),
    .shutdown_ack_s     (shutdown_ack_s),

    .status_boot_done   (status_boot_done),
    .status_setup_done  (status_setup_done),
    .status_running     (status_running),

    .dataslot_requestread    (dataslot_requestread),
    .dataslot_requestread_id (dataslot_requestread_id),
    .dataslot_requestread_ack(1'b1),
    .dataslot_requestread_ok (1'b1),

    .dataslot_requestwrite     (dataslot_requestwrite),
    .dataslot_requestwrite_id  (dataslot_requestwrite_id),
    .dataslot_requestwrite_size(dataslot_requestwrite_size),
    .dataslot_requestwrite_ack (1'b1),
    .dataslot_requestwrite_ok  (1'b1),

    .dataslot_update      (dataslot_update),
    .dataslot_update_id   (dataslot_update_id),
    .dataslot_update_size (dataslot_update_size),
    .dataslot_allcomplete (dataslot_allcomplete),

    .rtc_epoch_seconds (rtc_epoch_seconds),
    .rtc_date_bcd      (rtc_date_bcd),
    .rtc_time_bcd      (rtc_time_bcd),
    .rtc_valid         (rtc_valid),

    .savestate_supported   (1'b0),
    .savestate_addr        (32'b0),
    .savestate_size        (32'b0),
    .savestate_maxloadsize (32'b0),

    .osnotify_inmenu (osnotify_inmenu),

    .savestate_start     (savestate_start),
    .savestate_start_ack (1'b0),
    .savestate_start_busy(1'b0),
    .savestate_start_ok  (1'b0),
    .savestate_start_err (1'b0),

    .savestate_load     (savestate_load),
    .savestate_load_ack (1'b0),
    .savestate_load_busy(1'b0),
    .savestate_load_ok  (1'b0),
    .savestate_load_err (1'b0),

    .target_dataslot_read       (1'b0),
    .target_dataslot_write      (1'b0),
    .target_dataslot_write_ready(1'b1),
    .target_dataslot_getfile    (1'b0),
    .target_dataslot_openfile   (1'b0),

    .target_dataslot_ack  (target_dataslot_ack),
    .target_dataslot_done (target_dataslot_done),
    .target_dataslot_err  (target_dataslot_err),

    .target_dataslot_id        (16'b0),
    .target_dataslot_slotoffset(32'b0),
    .target_dataslot_bridgeaddr(32'b0),
    .target_dataslot_length    (32'b0),

    .target_buffer_param_struct(32'b0),
    .target_buffer_resp_struct (32'b0),

    .datatable_addr (10'b0),
    .datatable_wren (1'b0),
    .datatable_data (32'b0),
    .datatable_q    (datatable_q)
);

endmodule
