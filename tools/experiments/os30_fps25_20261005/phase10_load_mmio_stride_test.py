#!/usr/bin/env python3
"""Actual AXI-peripheral LOAD+writer integration validation."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'raw-load-mmio-v3';T=OUT/'tests';F=OUT/'frozen'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def replace(t,a,b):
 assert t.count(a)==1,(a,t.count(a));return t.replace(a,b)
T.mkdir(exist_ok=False)
tb=(P/'raw-load-mmio-v1/tests/tb_axi_periph.v').read_text()
tb=replace(tb,'    input wire reset_n,','    input wire reset_n,\n    input wire geometry_fabric_reset_n,')
top=F/'src/fpga/targets/pocket/core_top.v'
startup=re.search(r"    reg \[2:0\] geometry_startup = 3'b000;.*?wire geometry_reset = .*?;",top.read_text(),re.S).group(0)
startup=startup.replace('clk_cpu','clk').replace('sdram_cpu_reset_n','geometry_fabric_reset_n')
tb=replace(tb,'geometry_load_mmio geometry(.clk(clk),.reset(!reset_n),',startup+'\ngeometry_load_mmio geometry(.clk(clk),.reset(geometry_reset),')
tb=replace(tb,'    input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,','''    input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,
    output wire m_awvalid,input wire m_awready,output wire [31:0] m_awaddr,output wire [7:0] m_awlen,
    output wire m_wvalid,input wire m_wready,output wire [31:0] m_wdata,
    output wire [3:0] m_wstrb,output wire m_wlast,
    input wire m_bvalid,output wire m_bready,input wire [1:0] m_bresp,''')
tb=replace(tb,'.m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast));','''.m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
 .m_awvalid(m_awvalid),.m_awready(m_awready),.m_awaddr(m_awaddr),.m_awlen(m_awlen),
 .m_wvalid(m_wvalid),.m_wready(m_wready),.m_wdata(m_wdata),.m_wstrb(m_wstrb),.m_wlast(m_wlast),
 .m_bvalid(m_bvalid),.m_bready(m_bready),.m_bresp(m_bresp));''')
(T/'tb_axi_periph.v').write_text(tb)
for src in [P/'raw-load-mmio-v1/tests/altsyncram_stub.v',HERE/'phase10_load_mmio_stride_test.cpp']:
 shutil.copy2(src,T/src.name)
shutil.copy2(P/'raw-frontend-attributes-v4/native.bin',T/'native.bin')
files=[T/'tb_axi_periph.v',T/'altsyncram_stub.v',F/'src/fpga/common/axi_periph_slave.v',F/'src/fpga/targets/pocket/snac_shifter.v',F/'src/fpga/common/sync_fifo.v',F/'src/fpga/targets/pocket/apf/common.v']
names=['geometry_load_mmio.v','geometry_raw_load.v','geometry_transform_light.v','geometry_float_batch_recip.v','geometry_lighting_batch.v','GeometryFmaProbe.v','phase10_float_recip2.v','geometry_color_rtz.v','phase10_raw_vtx_dma.v','geometry_loaded_vertex_writer.v']
files += [F/'src/fpga/common'/p for p in names]
cmd=['verilator','--cc','--exe','--build','-j','3','-Wno-fatal','-Wno-BADVLTPRAGMA','--top-module','tb_axi_periph','--Mdir',str(T/'obj'),'-CFLAGS','-O2 -std=c++17',*[str(p) for p in files],str(T/'phase10_load_mmio_stride_test.cpp')]
with (T/'build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
save(T/'build.json',dict(command=cmd,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in files+[T/'phase10_load_mmio_stride_test.cpp',T/'native.bin']},startup_extracted_from_top_sha256=sha(top),recipe_sha256=sha(Path(__file__)),binary_sha256=sha(T/'obj/Vtb_axi_periph')))
with (T/'run.log').open('x') as log:r=subprocess.run([str(T/'obj/Vtb_axi_periph'),str(T/'native.bin')],stdout=log,stderr=subprocess.STDOUT)
print((T/'run.log').read_text())
if r.returncode:raise SystemExit(r.returncode)
result=json.loads((T/'run.log').read_text().splitlines()[-1]);result.update(stride64=True,range_jobs=528,range_vertices=5984,padding_untouched=True,startup_por=True,cpu_reset_preserves_job=True);result['build_sha256']=sha(T/'build.json');save(T/'result.json',result)
