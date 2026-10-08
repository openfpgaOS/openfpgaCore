#!/usr/bin/env python3
"""Private v8 exact frame/cycle and independent write-queue record observer."""
import re,sys
from pathlib import Path
import phase10_span_resume_acceptance as review
review.a.OUT=review.a.P/'gpu-queue-review-v8'
review.a.CAND=review.a.P/'gpu-queue-pack-v8/gpu_core.v'
review.OLD=review.a.P/'span-lookahead-review-v4'
review.a.PROBE+=r'''
// Independent queue records are captured at original req/stage producer ports.
// They do not read the packed storage and never alter GPU state.
reg [59:0] queue_reference[0:15];
reg [15:0] queue_reference_valid=0;
reg queue_check_w=0,queue_check_aw=0,queue_reset_check=0;
reg [35:0] queue_expected_w;
reg [23:0] queue_expected_aw;
reg [63:0] queue_pushes=0,queue_reads=0,queue_aw_reads=0,queue_ahead=0;
reg [63:0] queue_partial=0,queue_w_stalls=0,queue_aw_stalls=0,queue_resets=0;
reg queue_stalled_w=0,queue_stalled_aw=0;
reg [36:0] queue_held_w;
reg [39:0] queue_held_aw;
integer qi;
always @(posedge clk) begin
 if(!reset_n||gpu.soft_reset)begin
  queue_reference_valid<=0;queue_check_w<=0;queue_check_aw<=0;
  queue_stalled_w<=0;queue_stalled_aw<=0;queue_reset_check<=1;
  queue_resets<=queue_resets+1;
 end else begin
  if(queue_reset_check&&(gpu.fbwq_count!=0||gpu.m_wr_wvalid||gpu.m_wr_awvalid))
   $fatal(1,"write queue reset ownership not empty");
  queue_reset_check<=0;
  if(queue_check_w&&{gpu.m_wr_wstrb,gpu.m_wr_wdata}!==queue_expected_w)
   $fatal(1,"packed write queue data/byte-strobe differs from producer");
  if(queue_check_aw&&gpu.m_wr_awaddr[23:0]!==queue_expected_aw)
   $fatal(1,"common AW queue read differs from producer");
  if(queue_stalled_w&&(!gpu.m_wr_wvalid||{gpu.m_wr_wlast,gpu.m_wr_wstrb,gpu.m_wr_wdata}!==queue_held_w))
   $fatal(1,"W changed under backpressure");
  if(queue_stalled_aw&&(!gpu.m_wr_awvalid||{gpu.m_wr_awlen,gpu.m_wr_awaddr}!==queue_held_aw))
   $fatal(1,"AW changed under backpressure");
  queue_stalled_w<=gpu.m_wr_wvalid&&!gpu.m_wr_wready;
  queue_stalled_aw<=gpu.m_wr_awvalid&&!gpu.m_wr_awready;
  queue_held_w<={gpu.m_wr_wlast,gpu.m_wr_wstrb,gpu.m_wr_wdata};
  queue_held_aw<={gpu.m_wr_awlen,gpu.m_wr_awaddr};
  if(gpu.m_wr_wvalid&&!gpu.m_wr_wready)queue_w_stalls<=queue_w_stalls+1;
  if(gpu.m_wr_awvalid&&!gpu.m_wr_awready)queue_aw_stalls<=queue_aw_stalls+1;
  queue_check_w<=gpu.fbwq_start_now||gpu.fbwq_continue_now||gpu.fbwq_w_chain_start;
  queue_check_aw<=gpu.fbwq_start_now||gpu.fbwq_aw_ahead_issue;
  if(gpu.fbwq_start_now||gpu.fbwq_continue_now||gpu.fbwq_w_chain_start)begin
   if(!queue_reference_valid[gpu.fbwq_rd_ptr])$fatal(1,"queue read unwritten/reset slot");
   queue_expected_w<=queue_reference[gpu.fbwq_rd_ptr][35:0];
   queue_reference_valid[gpu.fbwq_rd_ptr]<=0;
   queue_reads<=queue_reads+1;
   if(queue_reference[gpu.fbwq_rd_ptr][35:32]!=15)queue_partial<=queue_partial+1;
  end
  if(gpu.fbwq_start_now||gpu.fbwq_aw_ahead_issue)begin
   if(!queue_reference_valid[gpu.fbwq_rd_ptr])$fatal(1,"AW read unwritten/reset slot");
   queue_expected_aw<=queue_reference[gpu.fbwq_rd_ptr][59:36];
   queue_aw_reads<=queue_aw_reads+1;
   if(gpu.fbwq_aw_ahead_issue)queue_ahead<=queue_ahead+1;
  end
  if(gpu.fbwq_swap_now||gpu.fbwq_stage_drain_now)begin
   if(queue_reference_valid[gpu.fbwq_wr_ptr])$fatal(1,"queue overwrote owned slot");
   if(gpu.fbwq_swap_now)
    queue_reference[gpu.fbwq_wr_ptr]<={gpu.fbwq_req_addr,gpu.fbwq_req_strb,gpu.fbwq_req_data};
   else queue_reference[gpu.fbwq_wr_ptr]<={gpu.fbwq_stage_addr,gpu.fbwq_stage_strb,gpu.fbwq_stage_data};
   queue_reference_valid[gpu.fbwq_wr_ptr]<=1;
   queue_pushes<=queue_pushes+1;
  end
 end
end
final $display("QUEUE_AUDIT pushes=%0d reads=%0d aw=%0d ahead=%0d partial=%0d w_stall=%0d aw_stall=%0d resets=%0d",queue_pushes,queue_reads,queue_aw_reads,queue_ahead,queue_partial,queue_w_stalls,queue_aw_stalls,queue_resets);
'''
def prepare():
 review.prepare()
 (review.a.OUT/'queue-recipe.py').write_text(Path(__file__).read_text())
 review.a.save(review.a.OUT/'queue-inputs.json',dict(recipe_sha256=review.a.sha(Path(__file__)),
  candidate_sha256=review.a.sha(review.a.CAND),baseline_manifest_sha256=review.a.sha(review.OLD/'manifest.json')))
def queue_result():
 logs=list(review.a.OUT.glob('runs/candidate-*/run.log'))+list(review.a.OUT.glob('boundaries-v3/candidate-*.log'))
 assert len(logs)==16
 totals={}
 for p in logs:
  line=re.search(r'QUEUE_AUDIT (.*)',p.read_text()).group(1)
  counts={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',line)}
  assert counts['pushes']==counts['reads']
  for k,v in counts.items():totals[k]=totals.get(k,0)+v
 assert all(totals[k]>0 for k in ['ahead','partial','w_stall','aw_stall','resets'])
 review.a.save(review.a.OUT/'queue-result.json',dict(passed=True,totals=totals,
  pairs=16,scope='Independent producer-to-W/AW queue record proof plus exact native/boundary pixel/cycle comparison; reset entries start empty. No active-reset cancellation protocol change.'))
if __name__=='__main__':
 for action in sys.argv[1:]:
  if action in ('prepare','queue_result'):globals()[action]()
  elif hasattr(review,action):getattr(review,action)()
  else:getattr(review.a,action)()
