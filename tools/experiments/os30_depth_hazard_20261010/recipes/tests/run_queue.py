#!/usr/bin/env python3
"""Extract the unmodified actual depth-hazard block and verify logical FIFO safety."""
from pathlib import Path
import argparse,hashlib,json,subprocess
HERE=Path(__file__).resolve().parent
GPU=HERE.parent/'work/gpu_core.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
parser=argparse.ArgumentParser();parser.add_argument('--out',default='queue-v1');args=parser.parse_args()
out=HERE/args.out;out.mkdir(exist_ok=False)
sources=[Path(__file__).resolve(),HERE/'test_depth_queue.cpp',GPU]
before={str(p):sha(p) for p in sources}
text=GPU.read_text();begin='// DEPTH_HAZARD_QUEUE_BEGIN';end='// DEPTH_HAZARD_QUEUE_END'
assert text.count(begin)==text.count(end)==1
block=text[text.index(begin):text.index(end)+len(end)]
wrapper='''module depth_queue(
input clk, reset_n, soft_reset, ring_reset,
input [25:0] read_addr, fbwq_req_addr, fbwq_stage_addr,
input [3:0] fbwq_wr_ptr, fbwq_rd_ptr,
input fbwq_swap_now, fbwq_stage_drain_now, fbwq_pop_now,
input z_flush_valid, z_src_pending_valid, wc_busy, fbwq_req_valid, fbwq_stage_valid,
output safe, pending_valid, output [3:0] pending_index);
localparam GPU_ADDR_W=26;
localparam FBWQ_DEPTH=16;
'''+block+'''
// Test harness drives the query tag; production assigns it with p3 payload.
always @* depth_read_tag=depth_hazard_tag(read_addr);
assign safe=depth_read_safe;
assign pending_valid=depth_pending_valid;
assign pending_index=depth_pending_index;
endmodule
'''
v=out/'depth_queue.v';v.write_text(wrapper)
command=['verilator','--cc','--exe','--build','--assert','-j','3','-Wno-fatal',
 '--top-module','depth_queue','--Mdir',str(out/'obj'),'-CFLAGS','-std=c++17 -O2',str(v),str(HERE/'test_depth_queue.cpp')]
with (out/'build.log').open('w') as f:subprocess.run(command,stdout=f,stderr=subprocess.STDOUT,check=True)
exe=out/'obj/Vdepth_queue';run=subprocess.run([str(exe)],capture_output=True,text=True)
(out/'run.log').write_text(run.stdout+run.stderr);assert run.returncode==0,run.stdout+run.stderr
result=json.loads(run.stdout.strip().splitlines()[-1]);assert result['pass']
assert before=={str(p):sha(p) for p in sources}
record={'status':'PASS_ACTUAL_EXTRACTED_DEPTH_QUEUE','inputs_sha256':before,'extracted_block_sha256':hashlib.sha256(block.encode()).hexdigest(),
 'generated_wrapper_sha256':sha(v),'executable_sha256':sha(exe),'command':command,'result':result,
 'scope':'Unmodified source-marked queue/pending block with independent full-address FIFO model. Legal producer/pop controls and query tag supplied by harness; full GPU FSM/tag pipeline wiring and downstream cache ordering tested in separate layers. Hash aliases conservatively wait; masks do not bypass hazards. Ring reset intentionally leaves queue/tracker intact.'}
(out/'manifest.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(result))
