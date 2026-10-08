#!/usr/bin/env python3
"""Freeze external-shared-FMA legacy packet formatter and adversarial test."""
import argparse,json,subprocess
from pathlib import Path
from phase10_experiment import CAMP,ROOT,sha,write
HERE=Path(__file__).resolve().parent
WRAP='''module vertex_packet_top(
 input wire clk,reset,start,input wire [4:0] slot,
 input wire [31:0] draw_flags,prim_rgba,env_rgba,u_offset,v_offset,uv_bias,
 input wire [15:0] tile_uls,tile_ult,input wire filter_linear,
 output wire raw_read_valid,output wire [7:0] raw_read_addr,input wire [31:0] raw_read_data,
 output wire word_valid,input wire word_ready,output wire [31:0] word_data,
 output wire busy,done,unsupported,output wire [31:0] cycles,output wire [7:0] surface_alpha
);
wire fv,fm,rv,ru;wire [31:0] fa,fb,fc,rd;
wire pw,pr;wire [3:0] pwa,pra;wire [31:0] pwd;reg [31:0] prd;
reg [31:0] packet_mem[0:8];
always @(posedge clk)begin
 if(pw)packet_mem[pwa]<=pwd;
 if(pr)prd<=packet_mem[pra];
end
geometry_vertex_packet #(.CAPTURE_CONTEXT(CAPTURE_VALUE),.EXTERNAL_PACKET(EXTERNAL_VALUE),.INTEGER_UV(INTEGER_VALUE)) packet(.clk(clk),.reset(reset),.start(start),.slot(slot),
 .draw_flags(draw_flags),.prim_rgba(prim_rgba),.env_rgba(env_rgba),.u_offset(u_offset),.v_offset(v_offset),.uv_bias(uv_bias),.tile_uls(tile_uls),.tile_ult(tile_ult),.filter_linear(filter_linear),
 .raw_read_valid(raw_read_valid),.raw_read_addr(raw_read_addr),.raw_read_data(raw_read_data),
 .fma_request_valid(fv),.fma_request_a(fa),.fma_request_b(fb),.fma_request_c(fc),.fma_request_multiply(fm),
 .fma_response_valid(rv),.fma_response_data(rd),.fma_response_unsupported(ru),
 .word_valid(word_valid),.word_ready(word_ready),.word_data(word_data),.busy(busy),.done(done),.unsupported(unsupported),.cycles(cycles),.surface_alpha(surface_alpha),.packet_write(pw),.packet_write_addr(pwa),.packet_write_data(pwd),.packet_read_valid(pr),.packet_read_addr(pra),.packet_read_data(prd));
GeometryFmaProbe shared_fma(.clk(clk),.reset(reset),.io_valid(fv),.io_a(fa),.io_b(fb),.io_c(fc),.io_multiplyOnly(fm),.io_resultValid(rv),.io_result(rd),.io_unsupported(ru));
endmodule
'''
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--label',default='packet-emitter-v1');ap.add_argument('--held-context',action='store_true');ap.add_argument('--external-packet',action='store_true');ap.add_argument('--integer-uv',action='store_true');args=ap.parse_args();out=CAMP/'phase10'/args.label;out.mkdir(exist_ok=False)
 files=[HERE/'phase10_float_batch/geometry_vertex_packet.v',HERE/'phase10_float_fixed_pipe.v',CAMP/'phase10/fpu-feasibility/rtl/GeometryFmaProbe.v',HERE/'phase10_vertex_packet_test.cpp']
 for p in files:(out/p.name).write_bytes(p.read_bytes())
 (out/'vertex_packet_top.v').write_text(WRAP.replace('CAPTURE_VALUE','0' if args.held_context else '1').replace('EXTERNAL_VALUE','1' if args.external_packet else '0').replace('INTEGER_VALUE','1' if args.integer_uv else '0'))
 binary=CAMP/'phase10/packet-vectors-v1/cases.bin'
 write(out/'manifest.json',dict(integer_uv=args.integer_uv,capture_context=not args.held_context,external_packet=args.external_packet,source_sha256={p.name:sha(out/p.name) for p in files},wrapper_sha256=sha(out/'vertex_packet_top.v'),helper_sha256=sha(Path(__file__)),case_sha256=sha(binary),native_input_sha256=sha(CAMP/'phase10/raw-load-v1/native.bin'),scope='External shared native FMA; one owned raw slot and DRAW uniforms become exact legacy0x56 packet. General fixed conversion internally instantiated. No RUNcontroller/cache/fabric integration or full-core timing.'))
 cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','--top-module','vertex_packet_top','--Mdir',str(out/'obj'),'-CFLAGS','-O2 -std=c++17 -mfma -ffp-contract=off'+(' -DHELD_CONTEXT' if args.held_context else '')+(' -DINTEGER_UV' if args.integer_uv else ''),str(out/'vertex_packet_top.v'),*[str(out/p.name) for p in files]]
 with (out/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (out/'run.log').open('x') as log:p=subprocess.run([str(out/'obj/Vvertex_packet_top'),str(binary),str(CAMP/'phase10/raw-load-v1/native.bin')],stdout=log,stderr=subprocess.STDOUT)
 print((out/'run.log').read_text())
 if p.returncode:raise SystemExit(p.returncode)
 write(out/'result.json',dict(**json.loads((out/'run.log').read_text()),manifest_sha256=sha(out/'manifest.json')))
if __name__=='__main__':main()
