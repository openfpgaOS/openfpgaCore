#!/usr/bin/env python3
"""Stage minimal private LOAD+result-DMA full-core probe (no production edits)."""
import difflib,hashlib,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'raw-load-mmio-v2';V1=P/'raw-load-mmio-v1';BASE=P/'vcache-narrow-v1/fit-seed2'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def replace(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
def add_startup(t):
 t=replace(t,'    geometry_load_mmio geometry_load (','''    // Startup-only reset initializes the new requester before any M2 work.
    // The retained100MHz fabric reset is tied high. CPU-local reset must not
    // cancel a live DMA: software must wait/quiesce before reusing this block.
    reg [2:0] geometry_startup = 3'b000;
    always @(posedge clk_cpu) geometry_startup <= {geometry_startup[1:0],1'b1};
    wire geometry_reset = !geometry_startup[2] || !sdram_cpu_reset_n;
    geometry_load_mmio geometry_load (''')
 return replace(t,'.clk(clk_cpu),.reset(!sdram_cpu_reset_n),','.clk(clk_cpu),.reset(geometry_reset),')
def prepare():
 assert json.loads((V1/'tests/result.json').read_text())['passed']
 OUT.mkdir(exist_ok=False);F=OUT/'frozen';shutil.copytree(V1/'frozen',F)
 orig=json.loads((V1/'prepared.json').read_text())['source_sha256']
 for p,h in orig.items():assert sha(F/p)==h,p
 raw=P/'raw-load-held-context-v7/geometry_raw_load.v'
 writer=P/'loaded-vertex-writer-v2/geometry_loaded_vertex_writer.v'
 assert 'parameter CAPTURE_CONTEXT=1' in raw.read_text()
 shutil.copy2(raw,F/'src/fpga/common/geometry_raw_load.v')
 shutil.copy2(writer,F/'src/fpga/common/geometry_loaded_vertex_writer.v')
 shutil.copy2(HERE/'phase10_load_mmio_dma.v',F/'src/fpga/common/geometry_load_mmio.v')
 top=F/'src/fpga/targets/pocket/core_top.v';t=top.read_text()
 t=replace(t,'    wire [1:0] geometry_rresp;','''    wire [1:0] geometry_rresp;
    wire geometry_awvalid,geometry_awready,geometry_wvalid,geometry_wready,geometry_wlast,geometry_bvalid;
    wire [31:0] geometry_awaddr,geometry_wdata;
    wire [7:0] geometry_awlen;
    wire [3:0] geometry_wstrb;
    wire [1:0] geometry_bresp;''')
 t=replace(t,'        .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(geometry_rresp),.m_rlast(geometry_rlast)\n','''        .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(geometry_rresp),.m_rlast(geometry_rlast),
        .m_awvalid(geometry_awvalid),.m_awready(geometry_awready),.m_awaddr(geometry_awaddr),.m_awlen(geometry_awlen),
        .m_wvalid(geometry_wvalid),.m_wready(geometry_wready),.m_wdata(geometry_wdata),.m_wstrb(geometry_wstrb),.m_wlast(geometry_wlast),
        // Arbiter retires B without a BREADY input. Writer is already in
        // RESPONSE and holds BREADY throughout every legal B response.
        .m_bvalid(geometry_bvalid),.m_bready(),.m_bresp(geometry_bresp)
''')
 t=replace(t,"        .m2_awvalid(1'b0), .m2_awready(),\n        .m2_awaddr(32'd0), .m2_awlen(8'd0),\n        .m2_wvalid(1'b0),  .m2_wready(),\n        .m2_wdata(32'd0),  .m2_wstrb(4'd0),\n        .m2_wlast(1'b0),\n        .m2_bvalid(),      .m2_bresp(),","""        .m2_awvalid(geometry_awvalid), .m2_awready(geometry_awready),
        .m2_awaddr(geometry_awaddr), .m2_awlen(geometry_awlen),
        .m2_wvalid(geometry_wvalid), .m2_wready(geometry_wready),
        .m2_wdata(geometry_wdata), .m2_wstrb(geometry_wstrb),
        .m2_wlast(geometry_wlast),
        .m2_bvalid(geometry_bvalid), .m2_bresp(geometry_bresp),""")
 t=t.replace('// M2 was idle in the retained Pocket top. Only its AR/R channel is used;\n    // result DMA AW/W/B is deliberately excluded from this first area probe.','// M2 was idle in the retained Pocket top; raw LOAD reads and packed\n    // result DMA writes now share it, without changing CPU/GPU protocols.')
 t=t.replace('Arbiter M2 AW/W is tied idle below.','Arbiter M2 now serves the private geometry LOAD/result DMA below.')
 t=t.replace('// M2: private raw LOAD reads; the APF bridge no longer writes SDRAM','// M2: private raw LOAD reads/result writes; APF no longer writes SDRAM')
 t=add_startup(t)
 top.write_text(t)
 qsf=F/'src/fpga/targets/pocket/ap_core.qsf'
 with qsf.open('a') as f:f.write('\nset_global_assignment -name VERILOG_FILE ../../common/geometry_loaded_vertex_writer.v\n')
 hashes={str(p.relative_to(F)):sha(p) for p in F.rglob('*') if p.is_file()}
 base=json.loads((BASE/'frozen-sources.json').read_text())
 patch=[]
 for name in ['src/fpga/targets/pocket/core_top.v','src/fpga/common/axi_periph_slave.v','src/fpga/targets/pocket/ap_core.qsf']:
  patch.extend(difflib.unified_diff((BASE/'frozen'/name).read_text().splitlines(True),(F/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'candidate.patch').write_text(''.join(patch))
 save(OUT/'prepared.json',dict(private_only=True,production_modified=False,scope=__doc__,source_sha256=hashes,changed={p:dict(before=base.get(p),after=h) for p,h in hashes.items() if base.get(p)!=h},v1_protocol_sha256=sha(V1/'tests/result.json'),raw_source_sha256=sha(raw),writer_source_sha256=sha(writer),mmio_source_sha256=sha(HERE/'phase10_load_mmio_dma.v'),recipe_sha256=sha(Path(__file__)),contract='CPU must exclusively own and invalidate result buffer before start and after completed+success. No CPU access/config mutation while active. Context remains stable through final B; no local reset/abort. Result DMA packed36bytes/vertex from resultbase, independent of source dest. Current job debug slots only; any descriptor/context write invalidates previous success.'))
 save(OUT/'registers.json',dict(base='0x4B000000',id='0x4C4F4402',full_word_writes_only=True,busy_writes_ignored=True,registers={'000':'RO ID','004':'RO active[0],completed[1],success[2],unsupported[3],all_inside[4],read_error[6:5],matrix_config_error[7],matrix_ready[8],write_error[10:9]','008':'WO start[0],clear_completed[1]','00C':'RW source_base','010':'RW count[5:0],dest[12:8]','014':'RW aspect','018/01C/020':'RW coefficient_x/y/z','024/028':'RW ambient/diffuse RGB low24','02C':'RW scale_u[15:0],scale_v[31:16]','030':'RW lighting[0]','034/038/03C':'reserved;reads0','040':'RW result_base64aligned','100..13C':'WO matrix16column-majorfloatwords','400..7FC':'RO row-major raw results; only current successful job dest..dest+count-1 while idle, else0; current ownership invalidated by descriptor/context writes'}))
 print(OUT)
def startup_revision():
 # Preserve the reviewed pre-POR preparation; only the top gains3 startup FFs.
 path=OUT/'prepared.json';before=json.loads(path.read_text())
 top=OUT/'frozen/src/fpga/targets/pocket/core_top.v';assert sha(top)==before['source_sha256']['src/fpga/targets/pocket/core_top.v']
 top.write_text(add_startup(top.read_text()))
 path.rename(OUT/'prepared-before-startup.json')
 before['source_sha256']['src/fpga/targets/pocket/core_top.v']=sha(top)
 before['changed']['src/fpga/targets/pocket/core_top.v']['after']=sha(top)
 before['recipe_sha256']=sha(Path(__file__))
 before['startup_revision']='3cycle startup-only initialized shift register OR existing fabric reset; CPU-only reset cannot cancel DMA.'
 save(path,before)
 patch=[]
 for name in ['src/fpga/targets/pocket/core_top.v','src/fpga/common/axi_periph_slave.v','src/fpga/targets/pocket/ap_core.qsf']:
  patch.extend(difflib.unified_diff((BASE/'frozen'/name).read_text().splitlines(True),(OUT/'frozen'/name).read_text().splitlines(True),fromfile='a/'+name,tofile='b/'+name))
 (OUT/'candidate.patch').rename(OUT/'candidate-before-startup.patch')
 (OUT/'candidate.patch').write_text(''.join(patch))
if __name__=='__main__':
 if sys.argv[1:]==['prepare']:prepare()
 elif sys.argv[1:]==['startup-revision']:startup_revision()
 elif sys.argv[1:]==['fit']:
  import phase10_load_mmio as old
  old.OUT=OUT;old.fitrun()
 else:raise SystemExit('prepare|fit')
