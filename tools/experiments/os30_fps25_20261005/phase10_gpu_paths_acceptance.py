#!/usr/bin/env python3
"""Actual GPU v11 versus v10 cycles/pixels plus ownership proofs."""
import json,re,sys
from pathlib import Path
import phase10_gpu_request_acceptance as prior
a=prior.a;review=prior.review
a.OUT=a.P/'gpu-paths-review-v11'
a.CAND=a.P/'gpu-paths-v11/gpu_core.v'
review.OLD=a.P/'gpu-request-review-v10d'
a.PROBE+=r'''
reg xy_ready=0,xy_check_dsp=0;
reg [127:0] xy_expected_dsp;
reg [63:0] xy_prepares=0,xy_uses=0;
reg signed [15:0] xy_sx0,xy_sx1,xy_sx2,xy_sy0,xy_sy1,xy_sy2;
reg signed [16:0] xy_e1x,xy_e2x,xy_e1y,xy_e2y;
always @(posedge clk) begin
 if(!reset_n||gpu.soft_reset)begin xy_ready<=0;xy_check_dsp<=0;end
 else begin
  if(xy_check_dsp&&{gpu.dsp_a,gpu.dsp_b,gpu.dsp2_a,gpu.dsp2_b}!==xy_expected_dsp)
   $fatal(1,"registered XY changed determinant DSP launch");
  xy_check_dsp<=0;
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_PROD_L0)begin
   if(xy_ready)$fatal(1,"XY precompute overwritten before determinant");
   xy_ready<=1;xy_prepares<=xy_prepares+1;
  end
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_DELTA)begin
   if(!xy_ready)$fatal(1,"determinant consumed XY before precompute");
   xy_sx0=gpu.dvx[gpu.dv_ord[0]];xy_sx1=gpu.dvx[gpu.dv_ord[1]];xy_sx2=gpu.dvx[gpu.dv_ord[2]];
   xy_sy0=gpu.dvy[gpu.dv_ord[0]];xy_sy1=gpu.dvy[gpu.dv_ord[1]];xy_sy2=gpu.dvy[gpu.dv_ord[2]];
   xy_e1x={xy_sx1[15],xy_sx1}-{xy_sx0[15],xy_sx0};
   xy_e2x={xy_sx2[15],xy_sx2}-{xy_sx0[15],xy_sx0};
   xy_e1y={xy_sy1[15],xy_sy1}-{xy_sy0[15],xy_sy0};
   xy_e2y={xy_sy2[15],xy_sy2}-{xy_sy0[15],xy_sy0};
   if({gpu.dd1x,gpu.dd2x,gpu.dd1y,gpu.dd2y,gpu.dd_x0,gpu.dd_y0}!==
      {xy_e1x,xy_e2x,xy_e1y,xy_e2y,xy_sx0,xy_sy0})
    $fatal(1,"early XY differs from original live DELTA expression");
   xy_expected_dsp<={{{15{xy_e1x[16]}},xy_e1x},{{15{xy_e2y[16]}},xy_e2y},
                     {{15{xy_e2x[16]}},xy_e2x},{{15{xy_e1y[16]}},xy_e1y}};
   xy_ready<=0;xy_check_dsp<=1;xy_uses<=xy_uses+1;
  end
 end
end
reg zpre_live=0,zpre_fold=0,zpre_read=0,zpre_valid=0;
reg [REQUEST_ADDR_W-1:0] zpre_old,zpre_p2b,zpre_p3,zpre_pending,zexpected;
reg [63:0] zowned_checks=0,zfold_checks=0,zread_checks=0,zpending_checks=0,zunowned=0;
always @(posedge clk)begin
 zpre_live<=reset_n&&!gpu.soft_reset;
 zpre_valid<=gpu.z_acc_valid;
 zpre_old<=gpu.z_acc_addr;zpre_p2b<=gpu.p2b_z_word_addr_w;
 zpre_p3<={gpu.p3_z_addr[REQUEST_ADDR_W-1:2],2'b0};
 zpre_pending<=gpu.z_src_pending_addr;
 zpre_fold<=gpu.state==gpu.S_FRAG_PIPE&&!gpu.fp_pipe_stall&&
   gpu.p2b_zf_fold&&gpu.p2b_zf_pass&&gpu.p2b_z_write&&!gpu.p2b_zf_acc_hit;
 zpre_read<=gpu.state==gpu.S_FRAG_PIPE&&gpu.fbss==gpu.FBSS_ZTEST_ACC_EVAL&&
   gpu.p3_z_value>=gpu.ztest_acc_old_half&&gpu.p3_z_write&&gpu.ztest_acc_from_read;
end
always @(negedge clk)if(zpre_live)begin
 zexpected=zpre_old;
 if(zpre_fold)begin
  if(zpre_valid)$fatal(1,"window fold acquired a different owned Z address");
  zexpected=zpre_p2b;zfold_checks<=zfold_checks+1;
 end
 if(zpre_read)begin zexpected=zpre_p3;zread_checks<=zread_checks+1;end
 if(gpu.main_fsm.z_src_pending_applied)begin zexpected=zpre_pending;zpending_checks<=zpending_checks+1;end
 if(gpu.z_acc_valid)begin
  if(!zpre_valid&&!zpre_fold&&!zpre_read&&!gpu.main_fsm.z_src_pending_applied)
   $fatal(1,"Z address became owned without a legacy producer");
  if(gpu.z_acc_addr!==zexpected)$fatal(1,"owned Z address differs from legacy ordered writers");
  zowned_checks<=zowned_checks+1;
 end
 if(!zpre_valid)zunowned<=zunowned+1;
end
final $display("PATHS_AUDIT xy_prepares=%0d xy_uses=%0d zowned=%0d zfold=%0d zread=%0d zpending=%0d zunowned=%0d",
 xy_prepares,xy_uses,zowned_checks,zfold_checks,zread_checks,zpending_checks,zunowned);
'''
def prepare():
 prior.prepare()
 (a.OUT/'paths-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'paths-inputs.json',dict(recipe_sha256=a.sha(Path(__file__)),
  candidate_sha256=a.sha(a.CAND),baseline_manifest_sha256=a.sha(review.OLD/'manifest.json')))
def paths_result():
 logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'))
 assert len(logs)==16
 totals={};runs=[]
 for p in logs:
  s=p.read_text();assert 'FATAL' not in s
  counts={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'PATHS_AUDIT (.*)',s).group(1))}
  assert counts['xy_prepares']==counts['xy_uses']
  for k,v in counts.items():totals[k]=totals.get(k,0)+v
  runs.append(dict(log=str(p.relative_to(a.OUT)),counts=counts))
 assert all(v>0 for v in totals.values()),totals
 a.save(a.OUT/'paths-result.json',dict(passed=True,pairs=16,totals=totals,runs=runs))
if __name__=='__main__':
 for action in sys.argv[1:]:
  if action in ('prepare','paths_result'):globals()[action]()
  elif action=='queue_result':prior.q.queue_result()
  elif action=='request_result':prior.request_result()
  elif hasattr(review,action):getattr(review,action)()
  else:getattr(a,action)()
