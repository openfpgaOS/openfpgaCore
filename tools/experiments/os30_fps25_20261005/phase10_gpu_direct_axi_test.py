#!/usr/bin/env python3
"""Native GDR LOAD/RUN via actual AXI peripheral into actual narrow GPU ring."""
import hashlib,json,re,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'gpu-direct-axi-native-v1';OUT.mkdir(exist_ok=False)
CORE=P/'gpu-direct-core-v1/frozen/src/fpga';RING=P/'gpu-direct-ring-v1';NATIVE=P/'gpu-direct-native-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sub(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
tb=(RING/'tests/tb_axi_periph.v').read_text()
for a,b in [('input wire geo_request','output wire geo_request'),('input wire [12:0] geo_words','output wire [12:0] geo_words'),('input wire geo_valid','output wire geo_valid'),('input wire [31:0] geo_data','output wire [31:0] geo_data'),('input wire geo_abort','output wire geo_abort')]:tb=sub(tb,a,b)
tb=sub(tb,'    input wire reset_n,','''    input wire reset_n,
    output wire m_arvalid,input wire m_arready,output wire [31:0] m_araddr,
    output wire [7:0] m_arlen,input wire m_rvalid,output wire m_rready,
    input wire [31:0] m_rdata,input wire [1:0] m_rresp,input wire m_rlast,''')
tb=sub(tb,'    .gpu_write_ready(gpu_write_ready),.gpu_write_pending(gpu_write_pending),','''    .geometry_reg_wr(geometry_reg_wr),.geometry_reg_addr(geometry_reg_addr),
    .geometry_reg_wdata(geometry_reg_wdata),.geometry_reg_wstrb(geometry_reg_wstrb),.geometry_reg_rdata(geometry_reg_rdata),
    .gpu_write_ready(gpu_write_ready),.gpu_write_pending(gpu_write_pending),''')
tb=sub(tb,'// DUT\n','''wire geometry_reg_wr;
wire [9:0] geometry_reg_addr;
wire [31:0] geometry_reg_wdata,geometry_reg_rdata;
wire [3:0] geometry_reg_wstrb;
geometry_direct_mmio geometry (
 .clk(clk),.reset(!reset_n),.reg_wr(geometry_reg_wr),.reg_addr(geometry_reg_addr),
 .reg_wdata(geometry_reg_wdata),.reg_wstrb(geometry_reg_wstrb),.reg_rdata(geometry_reg_rdata),
 .m_arvalid(m_arvalid),.m_arready(m_arready),.m_araddr(m_araddr),.m_arlen(m_arlen),
 .m_rvalid(m_rvalid),.m_rready(m_rready),.m_rdata(m_rdata),.m_rresp(m_rresp),.m_rlast(m_rlast),
 .geo_request(geo_request),.geo_words(geo_words),.geo_grant(geo_grant),
 .geo_valid(geo_valid),.geo_data(geo_data),.geo_ready(geo_ready),
 .geo_abort(geo_abort),.geo_owned(geo_owned),.geo_done(geo_done)
);
// DUT
''')
(OUT/'tb_axi_periph.v').write_text(tb)
cpp=(NATIVE/'phase10_gpu_direct_test.cpp').read_text()
cpp=cpp.replace('Vgeometry_direct_mmio','Vtb_axi_periph')
cpp=sub(cpp,'static void tick(){','''static bool cpu_aw,cpu_w,cpu_b,cpu_ar,cpu_r;
static uint32_t cpu_rdata;
static void tick(){''')
cpp=sub(cpp,''' d.geo_owned=ring_owned;d.geo_grant=d.geo_request&&!ring_owned&&rng()%4!=0;
 d.geo_ready=ring_owned&&rng()%4!=0;d.eval();''',''' d.eval();
 cpu_aw=d.s_axi_awvalid&&d.s_axi_awready;cpu_w=d.s_axi_wvalid&&d.s_axi_wready;
 cpu_b=d.s_axi_bvalid&&d.s_axi_bready;cpu_ar=d.s_axi_arvalid&&d.s_axi_arready;
 cpu_r=d.s_axi_rvalid&&d.s_axi_rready;cpu_rdata=d.s_axi_rdata;''')
cpp=sub(cpp,' d.clk=1;d.eval();ticks++;d.geo_done=0;',' d.clk=1;d.eval();ticks++;')
cpp=sub(cpp,'ring_owned=false;d.geo_done=1;commits++;','ring_owned=false;require(d.geo_done,"actual ring publication missing");commits++;')
start=cpp.index('static void put(');stop=cpp.index('static uint32_t wait_done()',start)
cpp=cpp[:start]+'''static void axi_put(uint32_t address,uint32_t data,unsigned strobe=15){
 d.s_axi_awaddr=address;d.s_axi_awlen=0;d.s_axi_awburst=0;d.s_axi_awvalid=1;
 d.s_axi_wdata=data;d.s_axi_wstrb=strobe;d.s_axi_wlast=1;d.s_axi_wvalid=1;d.s_axi_bready=0;
 bool aw=false,w=false;for(unsigned k=0;k<100000&&(!aw||!w);k++){tick();if(cpu_aw){aw=true;d.s_axi_awvalid=0;}if(cpu_w){w=true;d.s_axi_wvalid=0;}}
 require(aw&&w,"CPU AXI write timeout");d.s_axi_bready=1;bool b=false;
 for(unsigned k=0;k<100000&&!b;k++){tick();b=cpu_b;}require(b&&d.s_axi_bresp==0,"CPU AXI B timeout");d.s_axi_bready=0;
}
static uint32_t axi_get(uint32_t address){
 d.s_axi_araddr=address;d.s_axi_arlen=0;d.s_axi_arvalid=1;d.s_axi_rready=0;bool ar=false;
 for(unsigned k=0;k<100000&&!ar;k++){tick();ar=cpu_ar;}require(ar,"CPU AXI AR timeout");d.s_axi_arvalid=0;d.s_axi_rready=1;
 for(unsigned k=0;k<100000;k++){tick();if(cpu_r){d.s_axi_rready=0;require(d.s_axi_rresp==0,"CPU AXI R error");return cpu_rdata;}}
 throw std::runtime_error("CPU AXI R timeout");
}
static void put(unsigned byte,uint32_t data,unsigned strobe=15){axi_put(0x4b000000+byte,data,strobe);}
static uint32_t get(unsigned byte){return axi_get(0x4b000000+byte);}
'''+cpp[stop:]
cpp=sub(cpp,'  d.reset=1;d.reg_wr=d.m_rvalid=d.geo_done=0;for(unsigned i=0;i<5;i++)tick();d.reset=0;tick();','''  d.reset_n=0;d.m_rvalid=0;d.mixer_ready=1;d.allow_barrier=1;
  for(unsigned i=0;i<5;i++)tick();d.reset_n=1;tick();
  axi_put(0x4a000000,4);axi_put(0x4a000000,16);''')
cpp=sub(cpp,'  require(!axi_active&&!ring_owned,"final ownership leak");','''  require(!axi_active&&!ring_owned,"final ownership leak");
  // The actual GPU decodes the complete native stream then reaches a CPU fence.
  // No material state was installed, so triangle draw commands are suppressed;
  // pixel equivalence is covered separately by full captured-vector replay.
  axi_put(0x4a000004,0x02000001);axi_put(0x4a000004,0xd1ec7001);axi_put(0x4a000000,8);
  for(unsigned k=0;k<2000000&&d.fence_reached!=0xd1ec7001;k++)tick();
  require(d.fence_reached==0xd1ec7001,"actual GPU fence did not complete");
  require(axi_get(0x4a000004)==axi_get(0x4a000010),"actual GPU ring did not drain");''')
(OUT/'test.cpp').write_text(cpp)
shutil.copy2(NATIVE/'native.bin',OUT/'native.bin')
old=json.loads((RING/'tests/build.json').read_text())['command']
cmd=[]
for x in old:
 if x==str(RING/'tests/obj'):x=str(OUT/'obj')
 elif x==str(RING/'tests/tb_axi_periph.v'):x=str(OUT/'tb_axi_periph.v')
 elif x==str(RING/'tests/test.cpp'):x=str(OUT/'test.cpp')
 elif x==str(RING/'gpu_core.v'):x=str(CORE/'common/gpu_core.v')
 elif x==str(RING/'axi_periph_slave.v'):x=str(CORE/'common/axi_periph_slave.v')
 cmd.append(x)
native=json.loads((P/'gpu-direct-core-v1/prepared.json').read_text())['input_sha256']
rtl=[CORE/'common'/p.name for p in map(Path,native) if p.suffix=='.v']
# Two source filenames are normalized by the full-core staging helper.
rtl=[CORE/'common/geometry_direct_mmio.v' if p.name=='phase10_gpu_direct_mmio.v' else CORE/'common/geometry_lighting_batch.v' if p.name=='geometry_lighting_batch_pipelined_shared.v' else p for p in rtl]
rtl=[CORE/'common/geometry_float_cull_mem.v' if p.name=='phase10_float_cull_mem.v' else p for p in rtl]
cmd += list(map(str,rtl))
with (OUT/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
sourcepaths=[Path(x) for x in cmd if x.endswith(('.v','.cpp'))]
(OUT/'build.json').write_text(json.dumps(dict(command=cmd,source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sourcepaths},recipe_sha256=sha(Path(__file__)),native_sha256=sha(OUT/'native.bin'),binary_sha256=sha(OUT/'obj/Vtb_axi_periph')),indent=2)+'\n')
with (OUT/'run.log').open('x') as f:r=subprocess.run([str(OUT/'obj/Vtb_axi_periph'),str(OUT/'native.bin')],stdout=f,stderr=subprocess.STDOUT)
print((OUT/'run.log').read_text());assert r.returncode==0,r.returncode
result=json.loads((OUT/'run.log').read_text().splitlines()[-1]);result.update(scope='Actual AXI peripheral + actual GPU parser/fence + native GDR arithmetic and packet oracle; randomized behavioral M2 read memory, no rendered fragments in this fixture',build_sha256=sha(OUT/'build.json'),whole_core_prepared_sha256=sha(P/'gpu-direct-core-v1/prepared.json'))
(OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
