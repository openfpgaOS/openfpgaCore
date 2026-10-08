#!/usr/bin/env python3
"""Actual v14/v13 pixels, cycles, and literal legacy DSP product proof."""
import json,re,sys
from pathlib import Path
import phase10_gpu_attribute_acceptance as prior
a=prior.a;review=prior.review
a.OUT=a.P/'gpu-origin-review-v14';a.CAND=a.P/'gpu-origin-default-v14/gpu_core.v'
review.OLD=a.P/'gpu-attribute-review-v13'
a.PROBE+=r'''
reg [63:0] origin_product_cycles=0,origin_live_b=0,origin_default_changed=0,origin_launches=0;
reg signed [63:0] origin_old_product,origin_new_product;
always @(negedge clk)begin
 if(reset_n)begin
  origin_old_product=$signed(gpu.proof_dsp_a_old)*$signed(gpu.dsp_b);
  origin_new_product=$signed(gpu.dsp_a)*$signed(gpu.dsp_b);
  if(origin_old_product!==origin_new_product)$fatal(1,"literal old/new DSP-A product differs");
  if(gpu.dsp_b!=0)begin
   if(gpu.dsp_a!==gpu.proof_dsp_a_old)$fatal(1,"nonzero B did not select identical A");
   origin_live_b<=origin_live_b+1;
  end
  if(gpu.dsp_a!==gpu.proof_dsp_a_old)begin
   if(gpu.dsp_b!==0)$fatal(1,"different unused A has nonzero B");
   origin_default_changed<=origin_default_changed+1;
  end
  origin_product_cycles<=origin_product_cycles+1;
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_PLANE_ORG)origin_launches<=origin_launches+1;
 end
end
final $display("ORIGIN_DEFAULT_AUDIT cycles=%0d nonzero_b=%0d unused_a_changed=%0d origin_launches=%0d",
 origin_product_cycles,origin_live_b,origin_default_changed,origin_launches);
'''
def prepare():
 # Use inherited preparation without v13's fixed-source hash assertion.
 prior.prior.prepare()
 p=a.OUT/'model/frozen/common/gpu_core.v';source=p.read_text()
 # Mirror every original registered-A writer with its exact condition/order.
 # The sole changed default mirrors legacy zero; all explicit launches are
 # copied literally, including multiline and same-line reset assignments.
 matches=list(re.finditer(r'\bdsp_a\s*<=\s*([^;]+);',source))
 assert len(matches)>20
 def mirror(m):
  expression=m.group(1)
  # Exactly one default is dv_du; the explicit origin launch has two spaces
  # before <= and is preserved. Match the selected block's indentation/text.
  default=m.group(0)=="dsp_a <= dv_du;"
  old="32'sd0"if default else expression
  return m.group(0)+' proof_dsp_a_old <= '+old+';'
 assert sum(m.group(0)=="dsp_a <= dv_du;"for m in matches)==1
 instrumented=re.sub(r'\bdsp_a\s*<=\s*([^;]+);',mirror,source)
 index=re.search(r'^reg[^\n]*\bdsp_a\b',instrumented,re.M).start()
 instrumented=instrumented[:index]+'reg signed [31:0] proof_dsp_a_old;\n'+instrumented[index:]
 p.write_text(instrumented)
 (a.OUT/'origin-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'origin-inputs.json',dict(candidate_sha256=a.sha(a.CAND),baseline_manifest_sha256=a.sha(review.OLD/'manifest.json'),model_gpu_sha256=a.sha(p),literal_A_writer_count=len(matches),mirroring_scope='All legacy A writes under identical conditions and code priority; only selected unused default remains0. Simulation-only shadow register does not feed DUT outputs.',recipe_sha256=a.sha(Path(__file__))))
def origin_result():
 logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'));assert len(logs)==16
 totals={};rows=[]
 for p in logs:
  text=p.read_text();assert'FATAL'not in text
  counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'ORIGIN_DEFAULT_AUDIT (.*)',text).group(1))}
  for k,v in counts.items():totals[k]=totals.get(k,0)+v
  rows.append(dict(log=str(p.relative_to(a.OUT)),counts=counts))
 assert all(v>0 for v in totals.values()),totals
 a.save(a.OUT/'origin-result.json',dict(passed=True,pairs=16,totals=totals,runs=rows,added_states=0,added_cycles=0,added_functional_FF=0))
if __name__=='__main__':
 for name in sys.argv[1:]:
  if name in ('prepare','origin_result'):globals()[name]()
  elif name in ('run','attribute_result'):getattr(prior,name)()
  elif name=='paths_result':prior.prior.paths_result()
  elif name=='request_result':prior.prior.prior.request_result()
  elif name=='queue_result':prior.prior.prior.q.queue_result()
  elif hasattr(review,name):getattr(review,name)()
  else:getattr(a,name)()
