#!/usr/bin/env python3
"""Private v10 vs v8 request ownership, queue, cycle and pixel proof."""
import json,re,sys
from pathlib import Path
import phase10_gpu_queue_acceptance as q

review=q.review
a=review.a
a.OUT=a.P/'gpu-request-review-v10d'
a.CAND=a.P/'gpu-request-mux-v10/gpu_core.v'
review.OLD=a.P/'gpu-queue-review-v8'
# The original observer compared only the low 24 address bits. This fixture's
# GPU_ADDR_W is 26; qualify every address bit in both queue and request monitors.
a.PROBE=a.rep(a.PROBE,'reg [59:0] queue_reference',
 'localparam REQUEST_ADDR_W=$bits(gpu.fbwq_req_addr);\nreg [REQUEST_ADDR_W+35:0] queue_reference')
a.PROBE=a.rep(a.PROBE,'reg [23:0] queue_expected_aw;','reg [31:0] queue_expected_aw;')
a.PROBE=a.rep(a.PROBE,'gpu.m_wr_awaddr[23:0]!==queue_expected_aw','gpu.m_wr_awaddr!==queue_expected_aw')
a.PROBE=a.rep(a.PROBE,'queue_reference[gpu.fbwq_rd_ptr][59:36]',
 "{{(32-REQUEST_ADDR_W){1'b0}},queue_reference[gpu.fbwq_rd_ptr][REQUEST_ADDR_W+35:36]}")
a.PROBE+=r'''
// Snapshot the producer registers BEFORE the active edge. Inspect the selected
// request AFTER nonblocking assignments, on the inactive edge, avoiding races
// with the main FSM's blocking combinational temporaries.
reg request_pre_live=0,request_pre_valid=0,request_pre_can_push=0;
reg [REQUEST_ADDR_W+35:0] request_pre_payload;
reg [REQUEST_ADDR_W+35:0] request_sources[0:3];
reg [63:0] request_accepts=0,request_stalls=0,request_unowned=0;
reg [63:0] request_source_count[0:3];
integer request_i;
function [3:0] request_clear_mask(input [1:0] lane,input [15:0] remaining);
 integer n;
 begin
  n=4-lane;if(remaining<n)n=remaining;
  request_clear_mask=((16'h1<<n)-1)<<lane;
 end
endfunction
initial for(request_i=0;request_i<4;request_i=request_i+1)request_source_count[request_i]=0;
always @(posedge clk)begin
 request_pre_live<=reset_n&&!gpu.soft_reset;
 request_pre_valid<=gpu.fbwq_req_valid;
 request_pre_can_push<=gpu.fbwq_can_push;
 request_pre_payload<={gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data};
 request_sources[0]<={gpu.z_flush_addr,gpu.z_flush_strb,gpu.z_flush_data};
 request_sources[1]<={gpu.z_acc_addr,gpu.z_acc_mask,gpu.z_acc_data};
 request_sources[2]<={gpu.fb_acc_addr,gpu.fb_acc_mask,gpu.fb_acc_data};
 request_sources[3]<={gpu.cr_addr[REQUEST_ADDR_W-1:2],2'b00,request_clear_mask(gpu.cr_addr[1:0],gpu.cr_w_remaining),
                      gpu.cr_color,gpu.cr_color,gpu.cr_color,gpu.cr_color};
end
always @(negedge clk)begin
 if(request_pre_live)begin
  if(request_pre_valid&&!request_pre_can_push)begin
   if(!gpu.fbwq_req_valid||{gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data}!==request_pre_payload)
    $fatal(1,"owned request payload changed while stalled");
   request_stalls<=request_stalls+1;
  end
  if(gpu.main_fsm.fbwq_push_req)begin
   if(!request_pre_can_push)$fatal(1,"request producer accepted into an owned slot");
   if(!gpu.fbwq_req_valid||{gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data}!==request_sources[gpu.fbwq_push_source])
    $fatal(1,"new request differs from selected pre-edge producer src=%0d valid=%0d can=%0d actual=%h expected=%h state=%0d",gpu.fbwq_push_source,gpu.fbwq_req_valid,request_pre_can_push,{gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data},request_sources[gpu.fbwq_push_source],gpu.state);
   request_accepts<=request_accepts+1;
   request_source_count[gpu.fbwq_push_source]<=request_source_count[gpu.fbwq_push_source]+1;
  end else if(request_pre_can_push)begin
   if(gpu.fbwq_req_valid)$fatal(1,"unpublished payload acquired ownership");
   request_unowned<=request_unowned+1;
  end
 end
end
final $display("REQUEST_AUDIT accepts=%0d stalls=%0d unowned=%0d zflush=%0d zacc=%0d fbacc=%0d clear=%0d",
 request_accepts,request_stalls,request_unowned,request_source_count[0],request_source_count[1],request_source_count[2],request_source_count[3]);
'''

def prepare():
 q.prepare()
 (a.OUT/'request-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'request-inputs.json',dict(recipe_sha256=a.sha(Path(__file__)),
  candidate_sha256=a.sha(a.CAND),baseline_manifest_sha256=a.sha(review.OLD/'manifest.json'),
  inherited_recipe_sha256=a.sha(Path(q.__file__)),scope='v10 versus v8; v9 request mux plus v10 divider arithmetic'))

def request_result():
 logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'))
 assert len(logs)==16
 totals={};runs=[]
 for p in logs:
  s=p.read_text();assert 'FATAL' not in s
  line=re.search(r'REQUEST_AUDIT (.*)',s).group(1)
  counts={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',line)}
  queue=int(re.search(r'QUEUE_AUDIT pushes=(\d+)',s).group(1))
  assert counts['accepts']==queue,(p,counts,queue)
  for k,v in counts.items():totals[k]=totals.get(k,0)+v
  runs.append(dict(log=str(p.relative_to(a.OUT)),counts=counts))
 assert all(totals[k]>0 for k in ('accepts','stalls','unowned','zflush','zacc','fbacc','clear')),totals
 a.save(a.OUT/'request-result.json',dict(passed=True,pairs=16,totals=totals,runs=runs,
  scope='Pre-edge source payload and post-edge request ownership, all four sources, queue records, AXI stalls, exact output and cycle comparison. Selected os30 parameters only; optional write-combine/skid-swap reviewed statically.'))

if __name__=='__main__':
 for action in sys.argv[1:]:
  if action in ('prepare','request_result'):globals()[action]()
  elif action=='queue_result':q.queue_result()
  elif hasattr(review,action):getattr(review,action)()
  else:getattr(a,action)()
