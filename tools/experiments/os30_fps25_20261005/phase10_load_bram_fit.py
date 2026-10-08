#!/usr/bin/env python3
"""Private native LOAD + cacheable32B BRAM alias, one original-speed seed2 fit.

The component binding is created only after the separate wrapper/alias test
passes. It lists exact frozen RTL paths/hashes and passed test result paths.
No live HDL files, CPU generator, production project or SD are modified.
"""
import argparse,copy,difflib,hashlib,json,re,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'vcache-narrow-v1/fit-seed2'
TOP='src/fpga/targets/pocket/core_top.v'
PERIPH='src/fpga/common/axi_periph_slave.v'
QSF='src/fpga/targets/pocket/ap_core.qsf'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def replace(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def bound_inputs(binding):
 d=json.loads(binding.read_text());assert d['scope']=='native-load-bram-alias'
 assert d['tests'] and d['rtl']
 for name,record in d['rtl'].items():
  assert re.fullmatch(r'[A-Za-z0-9_]+\.v',name)
  p=ROOT/record['path'];assert p.is_file() and sha(p)==record['sha256'],p
 for record in d['tests']:
  p=ROOT/record['path'];assert p.is_file() and sha(p)==record['sha256'],p
  assert json.loads(p.read_text())['passed'],p
 return d
def top_patch(t):
 # Keep the retained CPU target/ID scheduler and top register slices. The
 # alias intercepts only their already-serialized SDRAM-facing interface.
 fields={
  'arvalid':1,'arready':1,'araddr':32,'arlen':8,
  'rvalid':1,'rready':1,'rdata':32,'rresp':2,'rlast':1,
  'awvalid':1,'awready':1,'awaddr':32,'awlen':8,
  'wvalid':1,'wready':1,'wdata':32,'wstrb':4,'wlast':1,
  'bvalid':1,'bresp':2,
 }
 declarations='''    // Private LOD4 results: cacheable BRAM view at reserved0x13f80000.
    // No result DMA and no GPU command ingress. CPU copies completed results.
    wire geometry_reg_wr;
    wire [9:0] geometry_reg_addr;
    wire [31:0] geometry_reg_wdata,geometry_reg_rdata;
    wire [3:0] geometry_reg_wstrb;
    wire geometry_arvalid,geometry_arready,geometry_rvalid,geometry_rready,geometry_rlast;
    wire [31:0] geometry_araddr,geometry_rdata;
    wire [7:0] geometry_arlen;
    wire [1:0] geometry_rresp;
    wire [7:0] geometry_view_addr;
    wire [31:0] geometry_view_data;
    wire geometry_view_active,geometry_view_block,geometry_view_valid;
    wire [5:0] geometry_view_count;
    // Only startup/fabric reset cancels the independent M2 source read.
    // CPU-only reset discards only local alias responses, never raw LOAD DMA.
    reg [2:0] geometry_startup = 3'b000;
    always @(posedge clk_cpu) geometry_startup <= {geometry_startup[1:0],1'b1};
    wire geometry_reset = !geometry_startup[2] || !sdram_cpu_reset_n;
'''
 for name,width in fields.items():
  declarations+=f'    wire '+('' if width==1 else f'[{width-1}:0] ')+f'geometry_cpu_sdram_{name};\n'
 declarations+='''    geometry_load_alias geometry_load (
        .clk(clk_cpu),.reset(geometry_reset),
        .reg_wr(geometry_reg_wr),.reg_addr(geometry_reg_addr),
        .reg_wdata(geometry_reg_wdata),.reg_wstrb(geometry_reg_wstrb),.reg_rdata(geometry_reg_rdata),
        .m_arvalid(geometry_arvalid),.m_arready(geometry_arready),.m_araddr(geometry_araddr),.m_arlen(geometry_arlen),
        .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(geometry_rresp),.m_rlast(geometry_rlast),
        .view_read_addr(geometry_view_addr),.view_read_data(geometry_view_data),
        .view_active(geometry_view_active),.view_block(geometry_view_block),
        .view_valid(geometry_view_valid),.view_count(geometry_view_count)
    );
    geometry_bram_alias geometry_alias (
        .clk(clk_cpu),.reset(geometry_reset),.cpu_reset(!reset_n_cpu_core),
'''
 for name in fields:declarations+=f'        .s_{name}(cpu_sdram_bus_{name}),.m_{name}(geometry_cpu_sdram_{name}),\n'
 declarations+='''        .view_read_addr(geometry_view_addr),.view_read_data(geometry_view_data),
        .view_active(geometry_view_active),.view_block(geometry_view_block),
        .view_valid(geometry_view_valid),.view_count(geometry_view_count)
    );

'''
 t=replace(t,'    // AXI4 peripheral slave\n',declarations+'    // AXI4 peripheral slave\n')
 t=replace(t,'        // CDC inputs\n','''        .geometry_reg_wr(geometry_reg_wr),.geometry_reg_addr(geometry_reg_addr),
        .geometry_reg_wdata(geometry_reg_wdata),.geometry_reg_wstrb(geometry_reg_wstrb),
        .geometry_reg_rdata(geometry_reg_rdata),
        // CDC inputs
''')
 for name in fields:t=replace(t,f'.m1_{name}(cpu_sdram_bus_{name})',f'.m1_{name}(geometry_cpu_sdram_{name})')
 t=replace(t,"        .m2_arvalid(1'b0), .m2_arready(),\n        .m2_araddr(32'd0), .m2_arlen(8'd0),\n        .m2_rvalid(), .m2_rdata(), .m2_rresp(), .m2_rlast(),\n        .m2_rready(1'b1),",'''        .m2_arvalid(geometry_arvalid), .m2_arready(geometry_arready),
        .m2_araddr(geometry_araddr), .m2_arlen(geometry_arlen),
        .m2_rvalid(geometry_rvalid), .m2_rdata(geometry_rdata), .m2_rresp(geometry_rresp), .m2_rlast(geometry_rlast),
        .m2_rready(geometry_rready),''')
 t=replace(t,'''        // M2: tied idle on Pocket — the APF bridge no longer writes SDRAM
        // through the arbiter (bridge_to_sdram removed; SDRAM-dest goes via the
        // firmware CRAM0 bounce).  M2 read channel is MiSTer-only (hps_bridge
        // sector writeback); the Pocket bridge never reads SDRAM through it.
''','''        // M2: private raw vertex reads. All write channels remain tied idle.
''')
 return t
def periph_patch(t):
 t=replace(t,'    // CDC inputs\n','''    // Private native LOAD control; result reads use the cacheable BRAM alias.
    output wire        geometry_reg_wr,
    output wire [9:0]  geometry_reg_addr,
    output wire [31:0] geometry_reg_wdata,
    output wire [3:0]  geometry_reg_wstrb,
    input wire [31:0] geometry_reg_rdata,
    // CDC inputs
''')
 t=replace(t,"localparam [3:0] REGION_HPS    = 4'd9;","localparam [3:0] REGION_HPS    = 4'd9;\nlocalparam [3:0] REGION_GEOMETRY = 4'd10;")
 t=replace(t,'reg [3:0] req_region;','''reg [3:0] req_region;
assign geometry_reg_wr=(state==S_PERIPH_WR) && (req_region==REGION_GEOMETRY);
assign geometry_reg_addr=req_addr[11:2];
assign geometry_reg_wdata=req_wdata;
assign geometry_reg_wstrb=req_wstrb;''')
 t=replace(t,"                8'h4A: decode_region = REGION_GPU;","                8'h4A: decode_region = REGION_GPU;\n                8'h4B: decode_region = REGION_GEOMETRY;")
 return replace(t,'                REGION_GPU:    periph_rd_data_r <= gpu_reg_rdata;','                REGION_GPU:    periph_rd_data_r <= gpu_reg_rdata;\n                REGION_GEOMETRY: periph_rd_data_r <= geometry_reg_rdata;')
def prepare(out,binding):
 inputs=bound_inputs(binding)
 expected=json.loads((BASE/'frozen-sources.json').read_text())
 for p,h in expected.items():assert sha(BASE/'frozen'/p)==h,p
 # Compute all source transforms before creating the immutable stage.
 new_top=top_patch((BASE/'frozen'/TOP).read_text())
 new_periph=periph_patch((BASE/'frozen'/PERIPH).read_text())
 out.mkdir(exist_ok=False);F=out/'frozen';shutil.copytree(BASE/'frozen',F)
 for name,record in inputs['rtl'].items():shutil.copy2(ROOT/record['path'],F/'src/fpga/common'/name)
 (F/TOP).write_text(new_top);(F/PERIPH).write_text(new_periph)
 with (F/QSF).open('a') as f:
  for name in inputs['rtl']:f.write('\nset_global_assignment -name VERILOG_FILE ../../common/'+name+'\n')
 actual={str(p.relative_to(F)):sha(p) for p in sorted(F.rglob('*')) if p.is_file()}
 assert actual['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']==expected['src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v']
 assert actual['src/fpga/common/gpu_core.v']==expected['src/fpga/common/gpu_core.v']
 save(out/'component-binding.json',inputs)
 save(out/'prepared.json',dict(scope=__doc__,private_only=True,production_modified=False,seed=2,seed_sweep=False,source_sha256=actual,changed={p:dict(before=expected.get(p),after=h) for p,h in actual.items() if expected.get(p)!=h},component_binding_sha256=sha(out/'component-binding.json'),input_binding_sha256=sha(binding),recipe_sha256=sha(Path(__file__)),baseline_cpu_preserved=True,baseline_gpu_preserved=True,baseline_clock_and_speed_settings_preserved=True,full_core_functionally_qualified=False,deployment_ready=False))
 patch=[]
 for name in [TOP,PERIPH,QSF]:patch.extend(difflib.unified_diff((BASE/'frozen'/name).read_text().splitlines(True),(F/name).read_text().splitlines(True),fromfile='narrow-baseline/'+name,tofile=out.name+'/'+name))
 (out/'candidate.patch').write_text(''.join(patch));print(out)
def runfit(out):
 inputs=bound_inputs(out/'component-binding.json')
 assert inputs
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 def inventory(**kwargs):return copy.deepcopy(json.loads((BASE/'inventory.json').read_text()))
 def freeze(dest,rows):
  prep=json.loads((out/'prepared.json').read_text());expected=prep['source_sha256']
  assert prep['recipe_sha256']==sha(Path(__file__))
  for p,h in expected.items():assert sha(out/'frozen'/p)==h,p
  F=dest/'frozen';shutil.copytree(out/'frozen',F)
  fit.save(dest/'inventory.json',rows);fit.save(dest/'frozen-sources.json',expected)
  fit.save(dest/'experiment.json',dict(scope=__doc__,resource_only=True,component_tests_passed=True,full_core_functionally_qualified=False,seed=2,seed_sweep=False,prepared_sha256=sha(out/'prepared.json')))
  return F
 fit.inventory=inventory;fit.freeze=freeze
 sys.argv=[sys.argv[0],'--target','pocket','--variant','os30','--seed-offsets','0','--jobs','1','--processors','4','--output',str(out/'fit-seed2')]
 fit.main()
def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('action',choices=['prepare','fit']);ap.add_argument('--label',default='load-bram-core-v1');ap.add_argument('--binding',type=Path)
 a=ap.parse_args();assert re.fullmatch(r'[a-z0-9][a-z0-9-]*',a.label)
 out=P/a.label
 if a.action=='prepare':assert a.binding;prepare(out,a.binding.resolve())
 else:assert not a.binding;runfit(out)
if __name__=='__main__':main()
