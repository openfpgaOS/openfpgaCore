#!/usr/bin/env python3
"""Stage mesh frontend into the previously validated prepared rasterizer."""
import hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
OUT=ROOT/'docs/measurements/os30-mesh-20261004'
def replace(s,old,new):
    assert s.count(old)==1,(old[:120],s.count(old))
    return s.replace(old,new,1)
def stage(dest,compact=0,enable_dot=1,extended_isa=1,code_pages=3,queue_bits=8,small_mlabs=False):
    dest.mkdir(parents=True,exist_ok=False)
    previous=ROOT/'docs/measurements/os30-parallel-20261004/edges-rtl'
    for name in ['gpu_core.v','gpu_edge_walker.v','gpu_span_walker.v','audio_mixer.v']:
        shutil.copy2(previous/name,dest/name)
    for directory in ['engine','dma','kernel']:
        for p in (HERE/directory).glob('gpu_mesh*.v'):shutil.copy2(p,dest/p.name)
    p=dest/'gpu_mesh_engine.v';s=p.read_text()
    s=replace(s,'parameter ENABLE_DOT=1','parameter ENABLE_DOT='+str(int(enable_dot)))
    s=replace(s,'parameter EXTENDED_ISA=1','parameter EXTENDED_ISA='+str(int(extended_isa)));p.write_text(s)
    p=dest/'gpu_core.v';s=p.read_text()
    if small_mlabs:
        # Preserve synchronous ports/latency; move only six shallow memories.
        for name in ('spanprod_pl_origin','spanprod_pl_du','spanprod_pl_dv',
                     'spanprod_u','spanprod_v','spanprod_count'):
            lines=s.splitlines(keepends=True)
            matches=[i for i,line in enumerate(lines) if 'reg ' in line and name+' ' in line
                     and 'ramstyle = "M10K, no_rw_check"' in line]
            assert len(matches)==1,(name,matches)
            i=matches[0];lines[i]=lines[i].replace('M10K, no_rw_check','MLAB, no_rw_check')
            s=''.join(lines)
    s=replace(s,'    input wire clk,','''    input wire mesh_bus_reset_n,
    output wire mesh_arvalid, input wire mesh_arready,
    output wire [31:0] mesh_araddr, output wire [7:0] mesh_arlen,
    input wire mesh_rvalid, output wire mesh_rready,
    input wire [31:0] mesh_rdata, input wire [1:0] mesh_rresp,
    input wire mesh_rlast,
    input wire clk,''')
    s=replace(s,'wire geometry_worker_busy;','wire geometry_worker_busy;\nwire [2:0] mesh_fault;')
    s=replace(s,'localparam GEOMETRY_WORKER = `ifdef INCLUDE_GPU_GEOMETRY_WORKER 1 `else 0 `endif;',
              'localparam GEOMETRY_WORKER = 1; // Private mesh frontend also permits partial FIFO payloads.')
    start=s.index('    gpu_geometry_worker #(')
    end=s.index('\nend else begin : g_direct_commands',start)
    s=s[:start]+'''    gpu_mesh_frontend #(.ADDR_BITS(RING_ADDR_BITS), .QUEUE_BITS(MESH_QUEUE_BITS), .COMPACT_LAYOUT(MESH_LAYOUT),
                       .ENABLE_DOT(MESH_DOT), .EXTENDED_ISA(MESH_EXTENDED), .CODE_PAGES(MESH_CODE_PAGES)) worker (
        .clk(clk), .reset_n(reset_n), .bus_reset_n(mesh_bus_reset_n), .enable(active),
        .soft_reset(soft_reset), .ring_reset(ring_reset),
        .input_wrptr(ring_wrptr), .input_rdptr(upload_rdptr),
        .input_addr(address), .input_data(data),
        .output_rdptr(ring_rdptr), .output_addr(ring_rd_addr),
        .output_data(ring_rd_data), .output_wrptr(command_wrptr),
        .busy(geometry_worker_busy), .fault(mesh_fault),
        .m_arvalid(mesh_arvalid), .m_arready(mesh_arready),
        .m_araddr(mesh_araddr), .m_arlen(mesh_arlen),
        .m_rvalid(mesh_rvalid), .m_rready(mesh_rready),
        .m_rdata(mesh_rdata), .m_rresp(mesh_rresp), .m_rlast(mesh_rlast)
    );'''+s[end:]
    # Private status bits14=fault,13=mesh. Do not advertise old worker opcodes.
    s=replace(s,"{19'b0, (VERTEX_ATTRIBUTES != 0), (GEOMETRY_WORKER != 0), (SM64_GEOMETRY != 0),",
              "{17'b0, (mesh_fault != 0), 1'b1, 1'b0, 1'b0, (SM64_GEOMETRY != 0),")
    s=s.replace('MESH_LAYOUT',str(int(compact))).replace('MESH_DOT',str(int(enable_dot))).replace('MESH_EXTENDED',str(int(extended_isa))).replace('MESH_CODE_PAGES',str(int(code_pages)))
    s=s.replace('MESH_QUEUE_BITS',str(int(queue_bits)))
    p.write_text(s)
    p=dest/'core_top.v';s=(ROOT/'src/fpga/targets/pocket/core_top.v').read_text()
    old='''        .m2_arvalid(1'b0), .m2_arready(),
        .m2_araddr(32'd0), .m2_arlen(8'd0),
        .m2_rvalid(), .m2_rdata(), .m2_rresp(), .m2_rlast(),
        .m2_rready(1'b1),'''
    new='''        .m2_arvalid(mesh_arvalid), .m2_arready(mesh_arready),
        .m2_araddr(mesh_araddr), .m2_arlen(mesh_arlen),
        .m2_rvalid(mesh_rvalid), .m2_rdata(mesh_rdata),
        .m2_rresp(mesh_rresp), .m2_rlast(mesh_rlast), .m2_rready(mesh_rready),'''
    s=replace(s,old,new)
    s=replace(s,'// GPU enable (from MMIO GPU_CTRL bit 0, directly in gpu_core)', '''// Private mesh M2 uses the same clk_cpu domain as the arbiter and GPU.
wire mesh_arvalid, mesh_arready, mesh_rvalid, mesh_rready, mesh_rlast;
wire [31:0] mesh_araddr, mesh_rdata;
wire [7:0] mesh_arlen;
wire [1:0] mesh_rresp;
// GPU enable (from MMIO GPU_CTRL bit 0, directly in gpu_core)''')
    s=replace(s,'    .m_rd_arvalid(gpu_rd_arvalid),','''    .mesh_bus_reset_n(sdram_cpu_reset_n),
    .mesh_arvalid(mesh_arvalid), .mesh_arready(mesh_arready),
    .mesh_araddr(mesh_araddr), .mesh_arlen(mesh_arlen),
    .mesh_rvalid(mesh_rvalid), .mesh_rready(mesh_rready),
    .mesh_rdata(mesh_rdata), .mesh_rresp(mesh_rresp), .mesh_rlast(mesh_rlast),
    .m_rd_arvalid(gpu_rd_arvalid),''')
    p.write_text(s)
    (dest/'sources.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in dest.glob('*.v')},indent=2)+'\n')
    return dest
if __name__=='__main__':stage(OUT/'rtl')
