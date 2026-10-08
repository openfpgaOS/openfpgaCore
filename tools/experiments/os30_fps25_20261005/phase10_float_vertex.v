// Private feasibility prototype. Float matrix/vertex transform only; no DMA,
// lighting, reciprocal, culling, clipping or raster submission.
module geometry_float_vertex(
 input wire clk,reset,
 input wire matrix_write,input wire [3:0] matrix_index,input wire [31:0] matrix_data,
 input wire in_valid,output wire in_ready,
 input wire [31:0] vertex_x,vertex_y,vertex_z,
 input wire [31:0] aspect,
 output reg out_valid,input wire out_ready,
 output reg [31:0] clip_x,clip_y,clip_z,clip_w,
 output reg unsupported
);
reg [31:0] matrix[0:15];
reg [31:0] px,py,pz,scale;
reg [31:0] accum[0:3];
reg active;
reg [2:0] phase,issued,received;
reg [1:0] tag[0:6];
reg [6:0] tag_valid;
reg bad_seen;
reg fvalid,fmul;
reg [31:0] fa,fb,fc;
wire [31:0] fr;
wire fr_valid,fr_bad;
integer i;
assign in_ready=!active && (!out_valid || out_ready);
always @* begin
 fvalid=active && issued < (phase==4 ? 1 : 4);
 fa=0;fb=0;fc=0;fmul=phase==0 || phase==4;
 case(phase)
 0:begin fa=py;fb=matrix[4+issued[1:0]];end
 1:begin fa=px;fb=matrix[issued[1:0]];fc=accum[issued[1:0]];end
 2:begin fa=32'h3f800000;fb=matrix[12+issued[1:0]];fc=accum[issued[1:0]];end
 3:begin fa=pz;fb=matrix[8+issued[1:0]];fc=accum[issued[1:0]];end
 4:begin fa=accum[0];fb=scale;end
 endcase
end
GeometryFmaProbe arithmetic(.clk(clk),.reset(reset),.io_valid(fvalid),
 .io_a(fa),.io_b(fb),.io_c(fc),.io_multiplyOnly(fmul),
 .io_result(fr),.io_resultValid(fr_valid),.io_unsupported(fr_bad));
always @(posedge clk) begin
 if(reset)begin active<=0;out_valid<=0;phase<=0;issued<=0;received<=0;tag_valid<=0;unsupported<=0;bad_seen<=0;end
 else begin
  if(out_valid && out_ready)out_valid<=0;
  if(matrix_write && !active)matrix[matrix_index]<=matrix_data;
  tag_valid<={tag_valid[5:0],fvalid};
  tag[0]<=issued[1:0];for(i=1;i<7;i=i+1)tag[i]<=tag[i-1];
  if(in_valid && in_ready)begin
   active<=1;phase<=0;issued<=0;received<=0;bad_seen<=0;
   px<=vertex_x;py<=vertex_y;pz<=vertex_z;scale<=aspect;
  end
  if(fvalid)issued<=issued+1;
  if(fr_valid)begin
   accum[tag[6]]<=fr;bad_seen<=bad_seen||fr_bad;
   if(phase==4)begin
    clip_x<=fr;clip_y<=accum[1];clip_z<=accum[2];clip_w<=accum[3];
    unsupported<=bad_seen||fr_bad;out_valid<=1;active<=0;
   end else if(received==3)begin phase<=phase+1;issued<=0;received<=0;end
   else received<=received+1;
  end
 end
end
endmodule
