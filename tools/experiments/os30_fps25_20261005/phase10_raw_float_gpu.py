#!/usr/bin/env python3
"""Prepare private narrow-GPU integration of the raw-float command processor."""
import difflib,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
TOOLS=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'vcache-narrow-v1/gpu_core.v'
OUT=P/'raw-float-gpu-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def one(text,old,new):
 assert text.count(old)==1,old
 return text.replace(old,new)
OUT.mkdir(exist_ok=False)
old=BASE.read_text();s=old
s=one(s,'localparam GEOMETRY_WORKER = `ifdef INCLUDE_GPU_GEOMETRY_WORKER 1 `else 0 `endif;',
'''localparam GEOMETRY_WORKER = `ifdef INCLUDE_GPU_GEOMETRY_WORKER 1 `else 0 `endif;
localparam RAW_FLOAT_WORKER = `ifdef INCLUDE_GPU_RAW_FLOAT_WORKER 1 `else 0 `endif;
localparam COMMAND_PROCESSOR = GEOMETRY_WORKER || RAW_FLOAT_WORKER;
wire raw_float_fault;''')
s=one(s,'generate if (GEOMETRY_WORKER) begin : g_geometry_worker',
'''generate if (RAW_FLOAT_WORKER) begin : g_raw_float_worker
    wire [RING_ADDR_BITS-1:0] address;
    reg [31:0] data;
    always @(posedge clk) data <= ring_bram[address];
    wire cmd_valid, cmd_ready, result_valid, result_ready;
    wire result_commit, result_abort, processor_busy;
    wire [31:0] cmd_data, result_data;
    geometry_raw_float_ring #(.ADDR_BITS(RING_ADDR_BITS), .QUEUE_BITS(6)) transport (
        .clk(clk), .reset_n(reset_n), .enable(active),
        .soft_reset(soft_reset), .ring_reset(ring_reset),
        .input_wrptr(ring_wrptr), .input_rdptr(upload_rdptr),
        .input_addr(address), .input_data(data),
        .cmd_valid(cmd_valid), .cmd_data(cmd_data), .cmd_ready(cmd_ready),
        .result_valid(result_valid), .result_data(result_data), .result_ready(result_ready),
        .result_commit(result_commit), .result_abort(result_abort),
        .processor_busy(processor_busy), .output_rdptr(ring_rdptr), .output_addr(ring_rd_addr),
        .output_data(ring_rd_data), .output_wrptr(command_wrptr), .busy(geometry_worker_busy)
    );
    geometry_raw_float_processor processor (
        .clk(clk), .reset(!reset_n || soft_reset || ring_reset),
        .cmd_valid(cmd_valid), .cmd_data(cmd_data), .cmd_ready(cmd_ready),
        .result_valid(result_valid), .result_data(result_data), .result_ready(result_ready),
        .result_commit(result_commit), .result_abort(result_abort),
        .fault(raw_float_fault), .processor_busy(processor_busy)
    );
end else if (GEOMETRY_WORKER) begin : g_geometry_worker
    assign raw_float_fault = 1'b0;''')
s=one(s,'end else begin : g_direct_commands\n    reg [31:0] data;',
        'end else begin : g_direct_commands\n    assign raw_float_fault = 1\'b0;\n    reg [31:0] data;')
old_status="4'd5:    reg_rdata = {20'b0, (GEOMETRY_WORKER != 0),"
s=one(s,old_status,"4'd5:    reg_rdata = {18'b0, raw_float_fault, (RAW_FLOAT_WORKER != 0), (GEOMETRY_WORKER != 0),")
s=one(s,'// Bit 10 advertises the version-1 raw-vertex geometry command (0x59)',
'''// Bits 12/13 are private raw-float processor capability/sticky fault.
        // A fault contains invalid geometry and requires GPU reset; it is
        // not an asynchronous CPU-fallback indication.
        // Bit 10 advertises the version-1 raw-vertex geometry command (0x59)''')
assert s.count('if (GEOMETRY_WORKER && ring_empty)')==3
s=s.replace('if (GEOMETRY_WORKER && ring_empty)','if (COMMAND_PROCESSOR && ring_empty)')
assert s.count("GEOMETRY_WORKER ? 12'd0 : ring_wrptr")==2
s=s.replace("GEOMETRY_WORKER ? 12'd0 : ring_wrptr","COMMAND_PROCESSOR ? 12'd0 : ring_wrptr")
assert '!GEOMETRY_WORKER && (INCLUDE_VTX_CACHE != 0)' in s
assert 'if (!GEOMETRY_WORKER && ((INCLUDE_GPU_XFORM_MAC != 0)' in s
(OUT/'gpu_core.v').write_text(s)
shutil.copy2(TOOLS/'phase10_raw_float_ring.v',OUT/'geometry_raw_float_ring.v')
(OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile=str(BASE.relative_to(ROOT)),tofile='gpu_core.v')))
record={'prepared':True,'qualified':False,'arithmetic_component_not_frozen':True,
 'baseline_gpu_sha256':sha(BASE),'gpu_sha256':sha(OUT/'gpu_core.v'),
 'transport_sha256':sha(OUT/'geometry_raw_float_ring.v'),
 'macro':'INCLUDE_GPU_RAW_FLOAT_WORKER',
 'must_not_enable_simultaneously':'INCLUDE_GPU_GEOMETRY_WORKER',
 'gpu_status_bits':{'12':'raw-float capability','13':'sticky raw-float processor fault'},
 'reset_policy':'Existing GPU reset, soft_reset and ring_reset reset arithmetic and output FIFO. Soft reset skips input to published CPU write pointer, resets output pointers to zero. CPU-only reset is unchanged.',
 'preserved':['4096-word CPU ring','legacy0x56 projector','0x54 derive','all original CPU/fabric/peripheral modules'],
 'external_component_source':str((TOOLS/'phase10_raw_float_processor.v').relative_to(ROOT)),
 'recipe_sha256':sha(Path(__file__))}
(OUT/'prepared.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2))
