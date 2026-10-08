// Observation only. The same reference-coordinate oracle runs on both GPUs.
reg [1:0] coordmon_phase=0;
reg coordmon_capture_check=0,coordmon_delta_check=0;
reg [99:0] coordmon_capture_bank=0,coordmon_delta_bank=0;
reg [127:0] coordmon_delta_operands=0;
reg [95:0] coordmon_coordinates=0;
reg [63:0] coordmon_captures=0,coordmon_deltas=0,coordmon_reads=0;
reg [63:0] coordmon_scratch_cycles=0,coordmon_normalize=0,coordmon_reset_scratch=0;
reg signed [15:0] coordmon_x0,coordmon_x1,coordmon_x2,coordmon_y0,coordmon_y1,coordmon_y2;
reg signed [16:0] coordmon_e1x,coordmon_e2x,coordmon_e1y,coordmon_e2y;
always @(posedge clk) begin
    coordmon_capture_check=0;
    coordmon_delta_check=0;
    if(!reset_n || gpu.soft_reset) begin
        if(coordmon_phase==1) coordmon_reset_scratch=coordmon_reset_scratch+1;
        coordmon_phase=0;
    end else if(gpu.state!=gpu.S_TRI_DERIVE) begin
        coordmon_phase=0;
    end else begin
        if(coordmon_phase==1) begin
            coordmon_scratch_cycles=coordmon_scratch_cycles+1;
            if(coordmon_coordinates !== {gpu.dvx[gpu.dv_ord[0]],gpu.dvx[gpu.dv_ord[1]],gpu.dvx[gpu.dv_ord[2]],gpu.dvy[gpu.dv_ord[0]],gpu.dvy[gpu.dv_ord[1]],gpu.dvy[gpu.dv_ord[2]]})
                $fatal(1,"coordinates/order changed between capture and DELTA");
`ifdef COORD_STAGED_CANDIDATE
            if({gpu.dd1x,gpu.dd2x,gpu.dd1y,gpu.dd2y,gpu.dd_x0,gpu.dd_y0} !== coordmon_capture_bank)
                $fatal(1,"scratch coordinate bank changed before DELTA");
`endif
        end
        if(gpu.dstate==gpu.DRV_NORMALIZE) coordmon_normalize=coordmon_normalize+1;
        if(gpu.dstate==gpu.DRV_PROD_L0) begin
            if(gpu.dv_ord[0]>2 || gpu.dv_ord[1]>2 || gpu.dv_ord[2]>2 || gpu.dv_ord[0]==gpu.dv_ord[1] || gpu.dv_ord[0]==gpu.dv_ord[2] || gpu.dv_ord[1]==gpu.dv_ord[2])
                $fatal(1,"sort result is not a permutation");
            coordmon_captures=coordmon_captures+1;
            coordmon_phase=1;
            coordmon_capture_check=1;
            coordmon_x0=gpu.dvx[gpu.dv_ord[0]];coordmon_x1=gpu.dvx[gpu.dv_ord[1]];coordmon_x2=gpu.dvx[gpu.dv_ord[2]];
            coordmon_y0=gpu.dvy[gpu.dv_ord[0]];coordmon_y1=gpu.dvy[gpu.dv_ord[1]];coordmon_y2=gpu.dvy[gpu.dv_ord[2]];
            coordmon_coordinates={coordmon_x0,coordmon_x1,coordmon_x2,coordmon_y0,coordmon_y1,coordmon_y2};
            coordmon_capture_bank={{coordmon_x1[15],coordmon_x1},{coordmon_x2[15],coordmon_x2},{coordmon_y1[15],coordmon_y1},{coordmon_y2[15],coordmon_y2},coordmon_x0,coordmon_y0};
        end
        if(gpu.dstate==gpu.DRV_DELTA) begin
            if(coordmon_phase!=1) $fatal(1,"DELTA reached without coordinate capture");
            coordmon_deltas=coordmon_deltas+1;
            coordmon_delta_check=1;
            coordmon_phase=2;
            coordmon_e1x={coordmon_x1[15],coordmon_x1}-{coordmon_x0[15],coordmon_x0};
            coordmon_e2x={coordmon_x2[15],coordmon_x2}-{coordmon_x0[15],coordmon_x0};
            coordmon_e1y={coordmon_y1[15],coordmon_y1}-{coordmon_y0[15],coordmon_y0};
            coordmon_e2y={coordmon_y2[15],coordmon_y2}-{coordmon_y0[15],coordmon_y0};
            coordmon_delta_bank={coordmon_e1x,coordmon_e2x,coordmon_e1y,coordmon_e2y,coordmon_x0,coordmon_y0};
            coordmon_delta_operands={{15{coordmon_e1x[16]}},coordmon_e1x,{15{coordmon_e2y[16]}},coordmon_e2y,{15{coordmon_e2x[16]}},coordmon_e2x,{15{coordmon_e1y[16]}},coordmon_e1y};
        end
        if(gpu.dstate==gpu.DRV_PLANE_NUM || gpu.dstate==gpu.DRV_PLANE_ORG) begin
            coordmon_reads=coordmon_reads+1;
            if(coordmon_phase!=2 || {gpu.dd1x,gpu.dd2x,gpu.dd1y,gpu.dd2y,gpu.dd_x0,gpu.dd_y0} !== coordmon_delta_bank)
                $fatal(1,"DDS consumer sees uninitialized or changed deltas");
        end
    end
end
always @(negedge clk) begin
`ifdef COORD_STAGED_CANDIDATE
    if(coordmon_capture_check && {gpu.dd1x,gpu.dd2x,gpu.dd1y,gpu.dd2y,gpu.dd_x0,gpu.dd_y0} !== coordmon_capture_bank)
        $fatal(1,"staged coordinate capture differs from original indexed values");
`endif
    if(coordmon_delta_check) begin
        if({gpu.dd1x,gpu.dd2x,gpu.dd1y,gpu.dd2y,gpu.dd_x0,gpu.dd_y0} !== coordmon_delta_bank)
            $fatal(1,"DELTA bank differs from indexed reference");
        if({gpu.dsp_a,gpu.dsp_b,gpu.dsp2_a,gpu.dsp2_b} !== coordmon_delta_operands)
            $fatal(1,"DELTA DSP operands differ from indexed reference");
    end
end
final $display("COORD_MON captures=%0d deltas=%0d consumers=%0d scratch_cycles=%0d normalize_cycles=%0d reset_scratch=%0d",coordmon_captures,coordmon_deltas,coordmon_reads,coordmon_scratch_cycles,coordmon_normalize,coordmon_reset_scratch);
