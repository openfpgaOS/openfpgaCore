#!/usr/bin/env python3
"""Exercise the private LOAD MMIO through the actual retained AXI peripheral."""
import hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase10/raw-load-mmio-v1';T=OUT/'tests';F=OUT/'frozen'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
T.mkdir(exist_ok=False)
tb=(ROOT/'src/fpga/test/tb_axi_periph.v').read_text()
tb=tb.replace('    input wire mixer_ready,','''    output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
    output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
    input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
    input wire mixer_ready,''',1)
instance='''wire geometry_wr;
wire [9:0] geometry_addr;
wire [31:0] geometry_wdata,geometry_rdata;
wire [3:0] geometry_wstrb;
geometry_load_mmio geometry(.clk(clk),.reset(!reset_n),
 .reg_wr(geometry_wr),.reg_addr(geometry_addr),.reg_wdata(geometry_wdata),
 .reg_wstrb(geometry_wstrb),.reg_rdata(geometry_rdata),
 .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
 .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast));
'''
tb=tb.replace('axi_periph_slave #(',instance+'\naxi_periph_slave #(',1)
tb=tb.replace(') dut (', ''') dut (
    .geometry_reg_wr(geometry_wr),.geometry_reg_addr(geometry_addr),
    .geometry_reg_wdata(geometry_wdata),.geometry_reg_wstrb(geometry_wstrb),
    .geometry_reg_rdata(geometry_rdata),
''',1)
assert '.geometry_reg_wr(geometry_wr)' in tb
(T/'tb_axi_periph.v').write_text(tb)
for src in [ROOT/'src/fpga/test/altsyncram_stub.v',HERE/'phase10_load_mmio_test.cpp']:
 shutil.copy2(src,T/src.name)
shutil.copy2(OUT.parent/'raw-frontend-attributes-v4/native.bin',T/'native.bin')
files=[T/'tb_axi_periph.v',T/'altsyncram_stub.v',F/'src/fpga/common/axi_periph_slave.v',F/'src/fpga/targets/pocket/snac_shifter.v',F/'src/fpga/common/sync_fifo.v',F/'src/fpga/targets/pocket/apf/common.v']
names=['geometry_load_mmio.v','geometry_raw_load.v','geometry_transform_light.v','geometry_float_batch_recip.v','geometry_lighting_batch.v','GeometryFmaProbe.v','phase10_float_recip2.v','geometry_color_rtz.v','phase10_raw_vtx_dma.v']
files += [F/'src/fpga/common'/p for p in names]
cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_axi_periph','--Mdir',str(T/'obj'),'-CFLAGS','-O2 -std=c++17',*[str(p) for p in files],str(T/'phase10_load_mmio_test.cpp')]
with (T/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
save(T/'build.json',dict(command=cmd,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files+[T/'phase10_load_mmio_test.cpp',T/'native.bin']},recipe_sha256=sha(Path(__file__)),binary_sha256=sha(T/'obj/Vtb_axi_periph')))
with (T/'run.log').open('x') as log:r=subprocess.run([str(T/'obj/Vtb_axi_periph'),str(T/'native.bin')],stdout=log,stderr=subprocess.STDOUT)
print((T/'run.log').read_text())
if r.returncode:raise SystemExit(r.returncode)
result=json.loads((T/'run.log').read_text().splitlines()[-1]);result['build_sha256']=sha(T/'build.json');save(T/'result.json',result)
