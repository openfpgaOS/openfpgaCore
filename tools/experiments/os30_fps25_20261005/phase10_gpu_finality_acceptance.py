#!/usr/bin/env python3
"""Exact v15/v14 replay and independent queue-finality/ownership checks."""
import json,re,sys
from pathlib import Path
import phase10_gpu_origin_acceptance as origin
a=origin.a;review=origin.review;prior=origin.prior
a.OUT=a.P/'gpu-queue-finality-review-v15'
a.CAND=a.P/'gpu-queue-finality-v15/gpu_core.v'
review.OLD=a.P/'gpu-origin-review-v14'
a.PROBE+=r'''
reg [63:0] finality_cycles=0,finality_true=0,finality_ahead=0,finality_gather=0;
wire finality_reference=(gpu.fbwq_start_burst_words==4'd8)||
                       (gpu.fbwq_count>{1'b0,gpu.fbwq_start_burst_words});
always @(posedge clk)if(reset_n&&!gpu.soft_reset)begin
 if(gpu.fbwq_head_chain_final!==finality_reference)$fatal(1,"live queue finality differs");
 if(gpu.fbwq_count>16)$fatal(1,"queue count outside capacity");
 finality_cycles<=finality_cycles+1;
 if(finality_reference)finality_true<=finality_true+1;
 if(gpu.fbwq_aw_ahead_issue)finality_ahead<=finality_ahead+1;
 if(gpu.fbwq_gather_prefix)finality_gather<=finality_gather+1;
end
final $display("FINALITY_AUDIT cycles=%0d final=%0d ahead=%0d gather=%0d",finality_cycles,finality_true,finality_ahead,finality_gather);
'''
def prepare():
 assert json.loads((a.P/'gpu-queue-finality-v15/literal/result.json').read_text())['passed']
 origin.prepare()
 (a.OUT/'finality-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'finality-inputs.json',dict(candidate_sha256=a.sha(a.CAND),parent_manifest_sha256=a.sha(review.OLD/'manifest.json'),recipe_sha256=a.sha(Path(__file__))))
def finality_result():
 logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'));assert len(logs)==16
 rows=[];totals={}
 for p in logs:
  text=p.read_text();assert 'FATAL'not in text
  c={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'FINALITY_AUDIT (.*)',text).group(1))}
  for k,v in c.items():totals[k]=totals.get(k,0)+v
  rows.append(dict(log=str(p.relative_to(a.OUT)),counts=c))
 assert totals['cycles']>0 and totals['final']>0 and totals['ahead']>0
 a.save(a.OUT/'finality-result.json',dict(passed=True,totals=totals,pairs=16,runs=rows))
if __name__=='__main__':
 for name in sys.argv[1:]:
  if name in ('prepare','finality_result'):globals()[name]()
  elif name=='origin_result':origin.origin_result()
  elif name in ('run','attribute_result'):getattr(prior,name)()
  elif name=='paths_result':prior.prior.paths_result()
  elif name=='request_result':prior.prior.prior.request_result()
  elif name=='queue_result':prior.prior.prior.q.queue_result()
  elif hasattr(review,name):getattr(review,name)()
  else:getattr(a,name)()
