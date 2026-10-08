#!/usr/bin/env python3
"""Stage an isolated geometry writer on the frozen narrow-cache GPU ring."""
import difflib, hashlib, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
P = ROOT / 'docs/measurements/os30-fps25-20261005/phase10'
OUT = P / 'gpu-direct-ring-v1'
BASE = P / 'vcache-narrow-v1/fit-seed2/frozen/src/fpga/common'

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def sub(s, a, b):
    assert s.count(a) == 1, (a, s.count(a))
    return s.replace(a, b)

OUT.mkdir(exist_ok=False)
g = original_g = (BASE / 'gpu_core.v').read_text()
assert sha(BASE/'gpu_core.v') == '3cdfed3fa2709fb4ae70d11647ea7a8f4cc654bf44bd24e576f9e8f8341637e2'
g = sub(g, '    parameter INCLUDE_CPU_RING = 0,', '''    parameter INCLUDE_CPU_RING = 0,
    // Private prototype: mutually exclusive CPU/geometry ring producers.
    // Requires the matching axi_periph_slave pending/ready interlock.
    parameter INCLUDE_GEOMETRY_INGRESS = 0,''')
g = sub(g, '    input  wire        reg_wr,', '''    // Geometry commits reserve the complete unpublished batch. Request and
    // word count remain stable until grant; data remains stable until ready.
    // Final accepted word publishes atomically. Abort discards all unpublished
    // words and releases ownership, without a success pulse.
    input  wire        geo_request,
    input  wire [12:0] geo_words,
    output wire        geo_grant,
    input  wire        geo_valid,
    input  wire [31:0] geo_data,
    output wire        geo_ready,
    input  wire        geo_abort,
    output reg         geo_done,
    output reg         geo_owned,
    input  wire        gpu_cpu_pending,
    output wire        gpu_cpu_ready,

    input  wire        reg_wr,''')
g = sub(g, '            || geometry_worker_busy', '            || geometry_worker_busy || geo_owned')
g = sub(g, 'wire cpu_ring_write = cpu_ring_req && !cpu_ring_full && !dma_busy;', '''wire cpu_ring_write = cpu_ring_req && !cpu_ring_full && !dma_busy && !geo_owned;

// CPU priority at the ownership boundary: a latched write, the registered
// MMIO pulse, and a GPU AW arriving this cycle must drain before grant. CPU
// writes arriving while owned are held by the peripheral FSM, not dropped.
// One ring slot remains empty; this modular subtraction includes all already
// published CPU work. The consumer continues during reservation and upload.
wire [11:0] geo_free_words = upload_rdptr - ring_wr_addr - 12'd1;
reg [11:0] geo_words_left;
assign gpu_cpu_ready = !geo_owned;
assign geo_grant = INCLUDE_GEOMETRY_INGRESS && INCLUDE_CPU_RING && active
    && cpu_ring_mode && !geo_owned && !dma_busy && !soft_reset && !ring_reset
    && !gpu_cpu_pending && !reg_wr && !geo_abort && geo_request
    && (ring_wr_addr == ring_wrptr) && (geo_words != 13'd0)
    && !geo_words[12] && (geo_words[11:0] <= geo_free_words);
assign geo_ready = INCLUDE_GEOMETRY_INGRESS && active && geo_owned && !geo_abort;
wire geo_ring_write = geo_valid && geo_ready;''')
g = sub(g, '''    if (dma_ring_wr_raw || cpu_ring_write)
        ring_bram[ring_wr_addr] <= cpu_ring_write ? reg_wdata : dma_ring_wdata_raw;''', '''    if (dma_ring_wr_raw || cpu_ring_write || geo_ring_write)
        ring_bram[ring_wr_addr] <= geo_ring_write ? geo_data :
                                 (cpu_ring_write ? reg_wdata : dma_ring_wdata_raw);''')
g = sub(g, '\n        cpu_ring_overflow <= 0;', '''
        cpu_ring_overflow <= 0;
        geo_owned       <= 0;
        geo_done        <= 0;
        geo_words_left  <= 0;''')
g = sub(g, '''    end else begin
        tex_flush_req <= 0;''', '''    end else begin
        geo_done <= 0;
        if (geo_grant) begin
            geo_owned <= 1;
            geo_words_left <= geo_words[11:0];
        end
        if (geo_owned && geo_abort) begin
            // The published pointer has not moved; no decoder-visible state
            // needs undoing. Subsequent CPU/geometry writes overwrite scratch.
            ring_wr_addr <= ring_wrptr;
            geo_owned <= 0;
            geo_words_left <= 0;
        end
        if (geo_ring_write) begin
            geo_words_left <= geo_words_left - 12'd1;
            if (geo_words_left == 12'd1) begin
                ring_wrptr <= ring_wr_addr + 12'd1;
                geo_owned <= 0;
                geo_done <= 1;
            end
        end
        tex_flush_req <= 0;''')
g = sub(g, '''        if (dma_ring_wr_raw || cpu_ring_write)
            ring_wr_addr <= ring_wr_addr + 1'b1;''', '''        if (dma_ring_wr_raw || cpu_ring_write || geo_ring_write)
            ring_wr_addr <= ring_wr_addr + 1'b1;''')
# The interlock is the enforcement point for every GPU MMIO side effect,
# including other always blocks: do not silently drop writes in the GPU.

p = original_p = (BASE/'axi_periph_slave.v').read_text()
p = sub(p, '    output reg         gpu_reg_wr,', '''    // Exclusive geometry writer interlock. All GPU writes stall while busy;
    // GPU reads and unrelated peripherals retain their existing behavior.
    input  wire        gpu_write_ready,
    output wire        gpu_write_pending,
    output reg         gpu_reg_wr,''')
# Default disabled to preserve non-experimental instantiations.
p = sub(p, 'module axi_periph_slave #(', '''module axi_periph_slave #(
    parameter INCLUDE_GEOMETRY_INGRESS = 0,''')
p = sub(p, 'wire req_is_gpu    = (req_region == REGION_GPU);', '''wire req_is_gpu    = (req_region == REGION_GPU);
assign gpu_write_pending = INCLUDE_GEOMETRY_INGRESS &&
    (gpu_reg_wr ||
     (req_is_gpu && (state == S_PERIPH_WR || state == S_WR_NEXT)) ||
     (state == S_IDLE && s_axi_awvalid && aw_region == REGION_GPU));''')
p = sub(p, '''                  && (|req_wstrb) && !mix_voice_ready)) begin''', '''                  && (|req_wstrb) && !mix_voice_ready)
                && !(INCLUDE_GEOMETRY_INGRESS && req_is_gpu && !gpu_write_ready)) begin''')

for name, text in [('gpu_core.v',g),('axi_periph_slave.v',p)]:
    (OUT/name).write_text(text)
patch = ''.join(difflib.unified_diff(original_g.splitlines(True),g.splitlines(True),fromfile='narrow/gpu_core.v',tofile='ingress/gpu_core.v'))
patch += ''.join(difflib.unified_diff(original_p.splitlines(True),p.splitlines(True),fromfile='retained/axi_periph_slave.v',tofile='ingress/axi_periph_slave.v'))
(OUT/'candidate.patch').write_text(patch)
record = dict(scope='Private ring transport prototype; no production edits or fit',
    recipe_sha256=sha(Path(__file__)),
    baseline_sha256={str((BASE/n).relative_to(ROOT)):sha(BASE/n) for n in ['gpu_core.v','axi_periph_slave.v']},
    candidate_sha256={n:sha(OUT/n) for n in ['gpu_core.v','axi_periph_slave.v']},
    contract=dict(parameter='INCLUDE_GEOMETRY_INGRESS=1 on GPU and peripheral slave',
        claim='geo_request && geo_grant, stable geo_words 1..4095; waits for CPU publication and posted writes',
        upload='geo_valid && geo_ready; exactly reserved count; complete command packets only',
        success='Final accepted word publishes batch; geo_done pulses and geo_owned drops',
        abort='geo_abort wins, rewinds unpublished append, drops owned; no geo_done',
        reset='Common GPU/controller fabric reset only; CPU-only reset must drain ownership',
        wiring='slave gpu_write_pending -> GPU gpu_cpu_pending; GPU gpu_cpu_ready -> slave gpu_write_ready',
        cpu='of_gpu_kick_now before COMMIT, no concurrent software GPU writes, resync SDK _gpu_wrptr from GPU_RING_WRPTR after completion/abort; staging empty',
        zero='Zero-output precheck must bypass claim; zero word request never grants'))
(OUT/'prepared.json').write_text(json.dumps(record,indent=2)+'\n')
print(OUT)
