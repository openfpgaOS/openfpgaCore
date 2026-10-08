#!/usr/bin/env python3
"""Private coupled memory LOAD probe; no production edits."""
import pathlib,json,hashlib,shutil,subprocess,os
ROOT=pathlib.Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OLD=P/'vcache-narrow-v1/model'; OUT=P/os.environ.get('LOAD_CONTENTION_LABEL','load-memory-contention-v2'); F=OUT/'frozen'
LOAD=P/os.environ.get('LOAD_COMPONENT_LABEL','raw-load-mmio-v2')/'frozen/src/fpga/common'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def prepare():
 OUT.mkdir(exist_ok=True)
 if F.exists():raise RuntimeError('refuse overwrite frozen staging')
 shutil.copytree(OLD/'frozen',F)
 names=['geometry_load_mmio.v','geometry_loaded_vertex_writer.v','geometry_raw_load.v','geometry_transform_light.v','geometry_float_batch_recip.v','geometry_lighting_batch.v','GeometryFmaProbe.v','phase10_float_recip2.v','geometry_color_rtz.v','phase10_raw_vtx_dma.v']
 for n in names:shutil.copy2(LOAD/n,F/'common'/n)
 for n in ['axi_sdram_arbiter.v','axi_sdram_slave.v','gpu_core.v']:
  assert sha(LOAD/n)==sha(F/'common'/n),(n,'retained full-core mismatch')
 t=(F/'tb_sdram_cont.v').read_text()
 t=t.replace('parameter CPU_FAIR_THRESHOLD=8,','parameter CPU_FAIR_THRESHOLD=8, parameter BRG_FAIR_THRESHOLD=12,')
 t=t.replace('.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) sdram_arb', '.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD), .BRG_FAIR_THRESHOLD(BRG_FAIR_THRESHOLD)) sdram_arb')
 t=t.replace('output wire        m2_bvalid,','output wire        m2_bvalid,\n    output wire [1:0] m2_rresp,m2_bresp,')
 t=t.replace('.m2_rresp(),','.m2_rresp(m2_rresp),').replace('.m2_bresp(),','.m2_bresp(m2_bresp),')
 (F/'tb_sdram_cont.v').write_text(t)
 t=(F/'tb_gpu.v').read_text()
 t=t.replace('parameter CPU_FAIR_THRESHOLD = 8,','parameter CPU_FAIR_THRESHOLD = 8, parameter BRG_FAIR_THRESHOLD = 12,')
 ports='''    input wire load_reg_wr,
    input wire [9:0] load_reg_addr,
    input wire [31:0] load_reg_wdata,
    output wire [31:0] load_reg_rdata,
    output wire load_arvalid,load_arready,load_rvalid,load_rready,load_rlast,
    output wire [31:0] load_araddr,
    output wire [7:0] load_arlen,
    output wire load_awvalid,load_awready,load_wvalid,load_wready,load_bvalid,load_bready,
    output wire [31:0] load_awaddr,
    output wire [7:0] load_awlen,
'''
 t=t.replace('    // MMIO register interface (driven from C++)',ports+'\n    // MMIO register interface (driven from C++)')
 inst='''wire [31:0] load_rdata,load_wdata;
wire [1:0] load_rresp,load_bresp;
wire [3:0] load_wstrb;
wire load_wlast;
geometry_load_mmio load_client(
 .clk(clk),.reset(!reset_n),.reg_wr(load_reg_wr),.reg_addr(load_reg_addr),
 .reg_wdata(load_reg_wdata),.reg_wstrb(4'hf),.reg_rdata(load_reg_rdata),
 .m_arvalid(load_arvalid),.m_arready(load_arready),.m_araddr(load_araddr),.m_arlen(load_arlen),
 .m_rvalid(load_rvalid),.m_rready(load_rready),.m_rdata(load_rdata),.m_rresp(load_rresp),.m_rlast(load_rlast),
 .m_awvalid(load_awvalid),.m_awready(load_awready),.m_awaddr(load_awaddr),.m_awlen(load_awlen),
 .m_wvalid(load_wvalid),.m_wready(load_wready),.m_wdata(load_wdata),.m_wstrb(load_wstrb),.m_wlast(load_wlast),
 .m_bvalid(load_bvalid),.m_bready(load_bready),.m_bresp(load_bresp));
'''
 t=t.replace('tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (',inst+'tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (')
 start=t.index('    .m2_arvalid(1\'b0),');end=t.index('    .m3_arvalid',start)
 mappings={x:'load_'+x for x in ['arvalid','arready','arlen','rvalid','rdata','rlast','rready','awvalid','awready','awlen','wvalid','wready','wdata','wstrb','wlast','bvalid','rresp','bresp']}
 mappings['araddr']="{6'd0,load_araddr[25:0]}";mappings['awaddr']="{6'd0,load_awaddr[25:0]}"
 t=t[:start]+''.join(f'    .m2_{k}({v}),\n' for k,v in mappings.items())+t[end:]
 t=t.replace('tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD)) memory (','tb_sdram_cont #(.CPU_FAIR_THRESHOLD(CPU_FAIR_THRESHOLD), .BRG_FAIR_THRESHOLD(BRG_FAIR_THRESHOLD)) memory (')
 t=t.replace('reg [31:0] rd_count;',"always @(posedge clk) if(reset_n) begin\n if(gpu_rd_arvalid && gpu_rd_araddr[25:0]>=26'h3e00000) $fatal(1,\"GPU read overlaps private LOAD storage\");\n if(gpu_wr_awvalid && gpu_wr_awaddr[25:0]>=26'h3e00000) $fatal(1,\"GPU write overlaps private LOAD storage\");\nend\nreg [31:0] rd_count;")
 (F/'tb_gpu.v').write_text(t)
 shutil.copy2(ROOT/'tools/experiments/os30_fps25_20261005/phase10_load_contention_driver.hpp',F/'load_driver.hpp')
 shutil.copy2(P/'raw-load-v1/native.bin',F/'native.bin')
 t=(F/'replay.cpp').read_text()
 t=t.replace('static void tick(int n = 1)', '#include "load_driver.hpp"\n\nstatic void tick(int n = 1)')
 t=t.replace('tb->clk = 0; tb->eval();\n        observe_tick();','tb->clk = 0; load_drive(); tb->eval();\n        load_sample();\n        observe_tick();')
 t=t.replace('hard_reset();','load_active=false; hard_reset();',1)
 t=t.replace('    const uint64_t start_cycles = cycles, start_busy = busy_cycles;','    load_begin();\n    const uint64_t start_cycles = cycles, start_busy = busy_cycles;')
 t=t.replace('            require((size_t(len) + 3)', '            load_check_range(addr,len);\n            require((size_t(len) + 3)')
 t=t.replace('            for (uint32_t i = 0; i < len; i++) mem_wr8(addr + i, (uint8_t)gv_rand_next(&st));','            load_check_range(addr,len);\n            for (uint32_t i = 0; i < len; i++) mem_wr8(addr + i, (uint8_t)gv_rand_next(&st));')
 t=t.replace('uint32_t w = sdram_word(i);','uint32_t w = load_owned(i*4)?0:sdram_word(i);')
 t=t.replace('    if (!ended || pos != buf.size()) rc = 2;','    if (!ended || pos != buf.size()) rc = 2;\n    const uint64_t gpu_finished_cycles=cycles;\n    while(load_active && load_job<load_jobs.size()){tick();if(cycles-gpu_finished_cycles>2000000)throw std::runtime_error("unfinished LOAD at end");}\n    load_report();')
 t=t.replace('(unsigned long long)(cycles - start_cycles),','(unsigned long long)(gpu_finished_cycles - start_cycles),')
 t=t.replace('    Verilated::commandArgs(argc, argv);','    Verilated::commandArgs(argc, argv);\n    load_options(argc,argv);')
 (F/'replay.cpp').write_text(t)
 cmd=json.loads((OLD/'build.json').read_text())['command']
 cmd=[s.replace(str(OLD),str(OUT)) for s in cmd]
 cmd += [str(F/'common'/n) for n in names]
 cmd += ['-GBRG_FAIR_THRESHOLD=12']
 # Frozen native path in C++ is independent of current working directory.
 cmd[cmd.index('-CFLAGS')+1]+=' -DLOAD_OUTPUT_STRIDE='+os.environ.get('LOAD_OUTPUT_STRIDE','36')
 cmd[cmd.index('-CFLAGS')+1]+=' -DLOAD_NATIVE_PATH=\\"'+str(F/'native.bin')+'\\"'
 (OUT/'prepare.json').write_text(json.dumps({'component':str(LOAD),'output_stride':int(os.environ.get('LOAD_OUTPUT_STRIDE','36')),'generator_sha256':sha(pathlib.Path(__file__))},indent=2)+'\n')
 (OUT/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
 manifest={str(p.relative_to(OUT)):sha(p) for p in F.rglob('*') if p.is_file()}
 (OUT/'inputs.json').write_text(json.dumps(manifest,indent=2)+'\n')
def build():
 cmd=json.loads((OUT/'build-command.json').read_text())
 with (OUT/'build.log').open('w') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 (OUT/'build.json').write_text(json.dumps({'command':cmd,'simulator_sha256':sha(OUT/'obj/Vtb_gpu')},indent=2)+'\n')
if __name__=='__main__':
 import sys
 for action in sys.argv[1:]:globals()[action]()
