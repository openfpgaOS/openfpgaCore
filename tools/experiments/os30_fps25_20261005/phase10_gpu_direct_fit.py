#!/usr/bin/env python3
"""Freeze/fit a PRIVATE full-core GPU-direct resource probe, not deployment."""
import copy,difflib,hashlib,json,os,re,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
LABEL=os.environ.get('GDR_FIT_LABEL','gpu-direct-core-v1')
assert re.fullmatch(r'[a-z0-9][a-z0-9-]*',LABEL),LABEL
OUT=P/LABEL;BASE=P/'vcache-narrow-v1/fit-seed2';RING=P/'gpu-direct-ring-v1'
TOP='src/fpga/targets/pocket/core_top.v';PERIPH='src/fpga/common/axi_periph_slave.v'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def replace(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def prepare():
 assert json.loads((RING/'tests/result.json').read_text())['passed']
 assert json.loads((RING/'negative-control/result.json').read_text())['passed']
 OUT.mkdir(exist_ok=False);F=OUT/'frozen';shutil.copytree(BASE/'frozen',F)
 old=json.loads((BASE/'frozen-sources.json').read_text())
 for p,h in old.items():assert sha(F/p)==h,p
 files={n:HERE/'phase10_float_batch'/n for n in [
  'geometry_raw_frontend.v','geometry_raw_load.v','geometry_transform_light.v',
  'geometry_float_batch_recip.v','geometry_color_rtz.v','geometry_run_dma.v',
  'geometry_run_precheck.v','geometry_run_commit.v','geometry_vertex_packet.v']}
 files.update({
  'geometry_direct_mmio.v':HERE/'phase10_gpu_direct_mmio.v',
  'geometry_lighting_batch.v':HERE/'phase10_lighting/geometry_lighting_batch_pipelined_shared.v',
  'GeometryFmaProbe.v':P/'fpu-feasibility/rtl/GeometryFmaProbe.v',
  'phase10_float_recip2.v':HERE/'phase10_float_recip2.v',
  'phase10_raw_vtx_dma.v':HERE/'phase10_raw_vtx_dma.v',
  'phase10_float_fixed_pipe.v':HERE/'phase10_float_fixed_pipe.v',
  'geometry_float_cull_mem.v':HERE/'phase10_float_cull_mem.v'})
 for n,src in files.items():shutil.copy2(src,F/'src/fpga/common'/n)
 for n in ['gpu_core.v','axi_periph_slave.v']:shutil.copy2(RING/n,F/'src/fpga/common'/n)
 p=F/PERIPH;t=p.read_text()
 t=replace(t,'    // CDC inputs\n','''    // Private GDR1 geometry frontend at previously unused region0x4B.
    output wire        geometry_reg_wr,
    output wire [9:0]  geometry_reg_addr,
    output wire [31:0] geometry_reg_wdata,
    output wire [3:0]  geometry_reg_wstrb,
    input wire [31:0]  geometry_reg_rdata,
    // CDC inputs
''')
 t=replace(t,"localparam [3:0] REGION_HPS    = 4'd9;","localparam [3:0] REGION_HPS    = 4'd9;\nlocalparam [3:0] REGION_GEOMETRY = 4'd10;")
 t=replace(t,'reg [3:0] req_region;','''reg [3:0] req_region;
// Geometry control writes are sampled exactly in the existing write state.
assign geometry_reg_wr=(state==S_PERIPH_WR) && (req_region==REGION_GEOMETRY);
assign geometry_reg_addr=req_addr[11:2];
assign geometry_reg_wdata=req_wdata;
assign geometry_reg_wstrb=req_wstrb;''')
 t=replace(t,"                8'h4A: decode_region = REGION_GPU;","                8'h4A: decode_region = REGION_GPU;\n                8'h4B: decode_region = REGION_GEOMETRY;")
 t=replace(t,'                REGION_GPU:    periph_rd_data_r <= gpu_reg_rdata;','                REGION_GPU:    periph_rd_data_r <= gpu_reg_rdata;\n                REGION_GEOMETRY: periph_rd_data_r <= geometry_reg_rdata;')
 p.write_text(t)
 p=F/TOP;t=p.read_text()
 marker='    // AXI4 peripheral slave\n'
 declaration='''    // Private GDR1: M2 reads raw vertices/Gfx and direct ring publication.
    // No result DMA writes or CPU readback on the accepted fast path.
    wire geometry_reg_wr;
    wire [9:0] geometry_reg_addr;
    wire [31:0] geometry_reg_wdata,geometry_reg_rdata;
    wire [3:0] geometry_reg_wstrb;
    wire geometry_arvalid,geometry_arready,geometry_rvalid,geometry_rready,geometry_rlast;
    wire [31:0] geometry_araddr,geometry_rdata;
    wire [7:0] geometry_arlen;
    wire [1:0] geometry_rresp;
    wire geo_request,geo_grant,geo_valid,geo_ready,geo_abort,geo_owned,geo_done;
    wire [12:0] geo_words;
    wire [31:0] geo_data;
    wire gpu_cpu_pending,gpu_cpu_ready;
    // Only startup/fabric reset cancels M2; APF/CPU reset must drain a live read.
    reg [2:0] geometry_startup = 3'b000;
    always @(posedge clk_cpu) geometry_startup <= {geometry_startup[1:0],1'b1};
    wire geometry_reset = !geometry_startup[2] || !sdram_cpu_reset_n;
    geometry_direct_mmio geometry_direct (
        .clk(clk_cpu),.reset(geometry_reset),
        .reg_wr(geometry_reg_wr),.reg_addr(geometry_reg_addr),
        .reg_wdata(geometry_reg_wdata),.reg_wstrb(geometry_reg_wstrb),.reg_rdata(geometry_reg_rdata),
        .m_arvalid(geometry_arvalid),.m_arready(geometry_arready),.m_araddr(geometry_araddr),.m_arlen(geometry_arlen),
        .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(geometry_rresp),.m_rlast(geometry_rlast),
        .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
        .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),
        .geo_abort(geo_abort),.geo_owned(geo_owned),.geo_done(geo_done)
    );

'''
 t=replace(t,marker,declaration+marker)
 t=replace(t,'    axi_periph_slave #(\n','    axi_periph_slave #(\n        .INCLUDE_GEOMETRY_INGRESS(1),\n')
 t=replace(t,'        // CDC inputs\n','''        .geometry_reg_wr(geometry_reg_wr),.geometry_reg_addr(geometry_reg_addr),
        .geometry_reg_wdata(geometry_reg_wdata),.geometry_reg_wstrb(geometry_reg_wstrb),
        .geometry_reg_rdata(geometry_reg_rdata),
        .gpu_write_pending(gpu_cpu_pending),.gpu_write_ready(gpu_cpu_ready),
        // CDC inputs
''')
 t=replace(t,"        .m2_arvalid(1'b0), .m2_arready(),\n        .m2_araddr(32'd0), .m2_arlen(8'd0),\n        .m2_rvalid(), .m2_rdata(), .m2_rresp(), .m2_rlast(),\n        .m2_rready(1'b1),",'''        .m2_arvalid(geometry_arvalid), .m2_arready(geometry_arready),
        .m2_araddr(geometry_araddr), .m2_arlen(geometry_arlen),
        .m2_rvalid(geometry_rvalid), .m2_rdata(geometry_rdata), .m2_rresp(geometry_rresp), .m2_rlast(geometry_rlast),
        .m2_rready(geometry_rready),''')
 t=t.replace('        // M2: tied idle on Pocket — the APF bridge no longer writes SDRAM','        // M2: private geometry reads; AW/W remain idle')
 t=replace(t,'gpu_core #(\n','gpu_core #(\n    .INCLUDE_GEOMETRY_INGRESS(1),\n')
 t=replace(t,'    // MMIO registers\n','''    // Exclusive geometry ring producer and CPU posted-write interlock.
    .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
    .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),
    .geo_abort(geo_abort),.geo_owned(geo_owned),.geo_done(geo_done),
    .gpu_cpu_pending(gpu_cpu_pending),.gpu_cpu_ready(gpu_cpu_ready),
    // MMIO registers
''')
 p.write_text(t)
 qsf=F/'src/fpga/targets/pocket/ap_core.qsf'
 with qsf.open('a') as f:
  for name in files:f.write('\nset_global_assignment -name VERILOG_FILE ../../common/'+name+'\n')
 actual={str(p.relative_to(F)):sha(p) for p in F.rglob('*') if p.is_file()}
 save(OUT/'prepared.json',dict(scope='UNQUALIFIED resource probe only; whole coordinator/GPU integration tests owned separately',private_only=True,production_modified=False,source_sha256=actual,changed={p:dict(before=old.get(p),after=h) for p,h in actual.items() if old.get(p)!=h},input_sha256={str(p.relative_to(ROOT)):sha(p) for p in files.values()},recipe_sha256=sha(Path(__file__)),ring_protocol_sha256=sha(RING/'tests/result.json'),ring_negative_sha256=sha(RING/'negative-control/result.json'),reset_limit='GPU media reset versus M2 drain continuity requires coordinated ownership-loss handling; no deployment qualification.'))
 patch=[]
 for name in [TOP,PERIPH,'src/fpga/common/gpu_core.v','src/fpga/targets/pocket/ap_core.qsf']:
  patch.extend(difflib.unified_diff((BASE/'frozen'/name).read_text().splitlines(True),(F/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'candidate.patch').write_text(''.join(patch));print(OUT)
def fitrun():
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 def inventory(**kwargs):return copy.deepcopy(json.loads((BASE/'inventory.json').read_text()))
 def freeze(out,rows):
  expected=json.loads((OUT/'prepared.json').read_text())['source_sha256']
  for p,h in expected.items():assert sha(OUT/'frozen'/p)==h,p
  dest=out/'frozen';shutil.copytree(OUT/'frozen',dest)
  fit.save(out/'inventory.json',rows);fit.save(out/'frozen-sources.json',expected)
  fit.save(out/'experiment.json',dict(scope=__doc__,resource_only=True,functionally_qualified=False,private_only=True,seed=2,seed_sweep=False,prepared_sha256=sha(OUT/'prepared.json')))
  return dest
 fit.inventory=inventory;fit.freeze=freeze
 sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT/'fit-seed2')]
 fit.main()
if __name__=='__main__':
 if sys.argv[1:]==['prepare']:prepare()
 elif sys.argv[1:]==['fit']:fitrun()
 else:raise SystemExit('prepare|fit')
