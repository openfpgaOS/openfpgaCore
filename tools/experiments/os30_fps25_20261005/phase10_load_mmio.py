#!/usr/bin/env python3
"""Private full-core LOAD-only MMIO probe, original CPU plus narrow cache."""
import copy,difflib,hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/raw-load-mmio-v1'
BASE=OUT.parent/'vcache-narrow-v1/fit-seed2';RAW=OUT.parent/'raw-frontend-attributes-v4'
FILES=['geometry_raw_load.v','geometry_transform_light.v','geometry_float_batch_recip.v','geometry_lighting_batch.v','GeometryFmaProbe.v','phase10_float_recip2.v','geometry_color_rtz.v','phase10_raw_vtx_dma.v']
TOP='src/fpga/targets/pocket/core_top.v';PERIPH='src/fpga/common/axi_periph_slave.v'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def replace(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def prepare():
 OUT.mkdir(exist_ok=False);src=OUT/'frozen';shutil.copytree(BASE/'frozen',src)
 old=json.loads((BASE/'frozen-sources.json').read_text())
 for p,h in old.items():assert sha(src/p)==h,p
 for f in FILES:shutil.copy2(RAW/f,src/'src/fpga/common'/f)
 shutil.copy2(HERE/'phase10_load_mmio.v',src/'src/fpga/common/geometry_load_mmio.v')
 p=src/PERIPH;t=p.read_text()
 t=replace(t,'    // CDC inputs\n','''    // Private LOAD-only experiment at previously unused region0x4B.
    output wire        geometry_reg_wr,
    output wire [9:0]  geometry_reg_addr,
    output wire [31:0] geometry_reg_wdata,
    output wire [3:0]  geometry_reg_wstrb,
    input wire [31:0]  geometry_reg_rdata,
    // CDC inputs
''')
 t=replace(t,"localparam [3:0] REGION_HPS    = 4'd9;","localparam [3:0] REGION_HPS    = 4'd9;\nlocalparam [3:0] REGION_GEOMETRY = 4'd10;")
 t=replace(t,'reg [3:0] req_region;','''reg [3:0] req_region;
// Single-cycle strobe: geometry never stalls S_PERIPH_WR. Fields are held
// by the existing AXI request registers; byte enables retain their meaning.
assign geometry_reg_wr=(state==S_PERIPH_WR) && (req_region==REGION_GEOMETRY);
assign geometry_reg_addr=req_addr[11:2];
assign geometry_reg_wdata=req_wdata;
assign geometry_reg_wstrb=req_wstrb;''')
 t=replace(t,"                8'h4A: decode_region = REGION_GPU;","                8'h4A: decode_region = REGION_GPU;\n                8'h4B: decode_region = REGION_GEOMETRY;")
 t=replace(t,'                REGION_GPU:    periph_rd_data_r <= gpu_reg_rdata;','                REGION_GPU:    periph_rd_data_r <= gpu_reg_rdata;\n                REGION_GEOMETRY: periph_rd_data_r <= geometry_reg_rdata;')
 p.write_text(t)
 p=src/TOP;t=p.read_text()
 marker='    // AXI4 peripheral slave\n'
 declaration='''    // Private raw LOAD frontend: original GPU and CPU remain unchanged.
    // M2 was idle in the retained Pocket top. Only its AR/R channel is used;
    // result DMA AW/W/B is deliberately excluded from this first area probe.
    wire geometry_reg_wr;
    wire [9:0] geometry_reg_addr;
    wire [31:0] geometry_reg_wdata,geometry_reg_rdata;
    wire [3:0] geometry_reg_wstrb;
    wire geometry_arvalid,geometry_arready,geometry_rvalid,geometry_rready,geometry_rlast;
    wire [31:0] geometry_araddr,geometry_rdata;
    wire [7:0] geometry_arlen;
    wire [1:0] geometry_rresp;
    geometry_load_mmio geometry_load (
        .clk(clk_cpu),.reset(!sdram_cpu_reset_n),
        .reg_wr(geometry_reg_wr),.reg_addr(geometry_reg_addr),
        .reg_wdata(geometry_reg_wdata),.reg_wstrb(geometry_reg_wstrb),.reg_rdata(geometry_reg_rdata),
        .m_arvalid(geometry_arvalid),.m_arready(geometry_arready),.m_araddr(geometry_araddr),.m_arlen(geometry_arlen),
        .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(geometry_rresp),.m_rlast(geometry_rlast)
    );

'''
 t=replace(t,marker,declaration+marker)
 t=replace(t,'        // CDC inputs\n','''        .geometry_reg_wr(geometry_reg_wr),.geometry_reg_addr(geometry_reg_addr),
        .geometry_reg_wdata(geometry_reg_wdata),.geometry_reg_wstrb(geometry_reg_wstrb),
        .geometry_reg_rdata(geometry_reg_rdata),
        // CDC inputs
''')
 t=replace(t,"        .m2_arvalid(1'b0), .m2_arready(),\n        .m2_araddr(32'd0), .m2_arlen(8'd0),\n        .m2_rvalid(), .m2_rdata(), .m2_rresp(), .m2_rlast(),\n        .m2_rready(1'b1),","""        .m2_arvalid(geometry_arvalid), .m2_arready(geometry_arready),
        .m2_araddr(geometry_araddr), .m2_arlen(geometry_arlen),
        .m2_rvalid(geometry_rvalid), .m2_rdata(geometry_rdata), .m2_rresp(geometry_rresp), .m2_rlast(geometry_rlast),
        .m2_rready(geometry_rready),""")
 t=t.replace('        // M2: tied idle on Pocket — the APF bridge no longer writes SDRAM','        // M2: private raw LOAD reads; the APF bridge no longer writes SDRAM')
 p.write_text(t)
 qsf=src/'src/fpga/targets/pocket/ap_core.qsf'
 with qsf.open('a') as f:
  for name in FILES+['geometry_load_mmio.v']:f.write('\nset_global_assignment -name VERILOG_FILE ../../common/'+name+'\n')
 actual={str(p.relative_to(src)):sha(p) for p in src.rglob('*') if p.is_file()}
 changed={p:dict(before=old.get(p),after=h) for p,h in actual.items() if old.get(p)!=h}
 save(OUT/'prepared.json',dict(private_only=True,production_modified=False,source_sha256=actual,changed=changed,raw_source_sha256={f:sha(RAW/f) for f in FILES},recipe_sha256=sha(Path(__file__)),mmio_source_sha256=sha(HERE/'phase10_load_mmio.v'),scope='Full-core original CPU+narrow GPU with operational LOAD-only MMIO/readback, M2 AR only. Result DMA writer and software integration excluded.'))
 patch=[]
 for name in [TOP,PERIPH,'src/fpga/targets/pocket/ap_core.qsf']:
  patch.extend(difflib.unified_diff((BASE/'frozen'/name).read_text().splitlines(True),(src/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'candidate.patch').write_text(''.join(patch))
 print(OUT)
def fitrun():
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 def inventory(**kwargs):return copy.deepcopy(json.loads((BASE/'inventory.json').read_text()))
 def freeze(out,rows):
  expected=json.loads((OUT/'prepared.json').read_text())['source_sha256']
  assert json.loads((OUT/'tests/result.json').read_text())['passed']
  for p,h in expected.items():assert sha(OUT/'frozen'/p)==h,p
  dest=out/'frozen';shutil.copytree(OUT/'frozen',dest)
  fit.save(out/'inventory.json',rows);fit.save(out/'frozen-sources.json',expected)
  fit.save(out/'experiment.json',dict(scope=__doc__,private_only=True,seed=2,seed_sweep=False,prepared_sha256=sha(OUT/'prepared.json'),test_sha256=sha(OUT/'tests/result.json')))
  return dest
 fit.inventory=inventory;fit.freeze=freeze
 sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(OUT/'fit-seed2')]
 fit.main()
if __name__=='__main__':
 if sys.argv[1:] == ['prepare']:prepare()
 elif sys.argv[1:] == ['fit']:fitrun()
 else:raise SystemExit('prepare|fit')

