#!/usr/bin/env python3
"""Independent v7 dead-product-bit relay proof against cycle-identical v6."""
import sys
from pathlib import Path
import phase10_span_resume_acceptance as review

review.a.OUT = review.a.P/'span-lookahead-review-v5'
review.a.CAND = review.a.P/'span-lookahead-v7/gpu_core.v'
review.OLD = review.a.P/'span-lookahead-review-v4'
review.a.PROBE += r'''
reg [63:0] depth_relay_checks=0,depth_relay_stores=0;
always @(posedge clk)begin
 if(reset_n&&!gpu.soft_reset&&gpu.dstate==gpu.DRV_ORG_FORM)begin
  if(gpu.drv_depth_du_relay!==gpu.dv_du)
   $fatal(1,"depth relay differs from original dv_du");
  depth_relay_checks<=depth_relay_checks+1;
  if(gpu.dv_attr==6)depth_relay_stores<=depth_relay_stores+1;
 end
end
final $display("DEPTH_RELAY checks=%0d stores=%0d",depth_relay_checks,depth_relay_stores);
'''

def prepare():
    review.prepare()
    out=review.a.OUT
    (out/'relay-recipe.py').write_text(Path(__file__).read_text())
    review.a.save(out/'relay-inputs.json',dict(recipe_sha256=review.a.sha(Path(__file__)),
        inherited_recipe_sha256=review.a.sha(Path(review.__file__)),
        candidate_sha256=review.a.sha(review.a.CAND),
        baseline_manifest_sha256=review.a.sha(review.OLD/'manifest.json')))

if __name__=='__main__':
    for action in sys.argv[1:]:
        if action=='prepare':prepare()
        elif hasattr(review,action):getattr(review,action)()
        else:getattr(review.a,action)()
