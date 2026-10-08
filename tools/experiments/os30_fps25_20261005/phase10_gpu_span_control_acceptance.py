#!/usr/bin/env python3
"""Exact v17/v16 GPU replays with original count-mask/terminal shadows."""
import json,re,sys
from pathlib import Path
import phase10_gpu_ring_acceptance as ring
a=ring.a;review=ring.review;prior=ring.prior
a.OUT=a.P/'gpu-span-control-review-v17';a.CAND=a.P/'gpu-span-control-v17/gpu_core.v'
review.OLD=a.P/'gpu-ring-equality-review-v16'
a.PROBE+=r'''
reg[63:0] span_control_checks=0,span_control_select_zero=0,span_control_select_nonzero=0;
reg[63:0] span_control_terminal=0,span_control_nonterminal=0,span_control_resets=0;
wire[15:0] span_control_original_count=(gpu.spanprod_cur_valid&&gpu.proof_cur_count_valid_old)?gpu.spanprod_cur_count_raw:16'd0;
always@(posedge clk)begin
 if(!reset_n||gpu.soft_reset)span_control_resets<=span_control_resets+1;
 else begin
  if(gpu.spanprod_cur_count!==span_control_original_count)$fatal(1,"original masked span count differs");
  if(gpu.spanprod_cur_nonzero!==(span_control_original_count!=16'd0))$fatal(1,"original span nonzero differs");
  if(gpu.spanprod_calc_terminal!==(gpu.spanprod_next_calc(gpu.spanprod_calc_step)==gpu.SPANPROD_STEP_NONE))
   $fatal(1,"original encoded terminal differs");
  span_control_checks<=span_control_checks+1;
  if(gpu.state==gpu.S_SPANPROD_SETUP)begin
   if(gpu.spanprod_cur_nonzero)span_control_select_nonzero<=span_control_select_nonzero+1;
   else span_control_select_zero<=span_control_select_zero+1;
  end
  if(gpu.state==gpu.S_SPANPROD_CAPTURE||gpu.span_la==gpu.LA_CAPTURE)begin
   if(gpu.spanprod_calc_terminal)span_control_terminal<=span_control_terminal+1;
   else span_control_nonterminal<=span_control_nonterminal+1;
  end
 end
end
final $display("SPAN_CONTROL_AUDIT checks=%0d selected_zero=%0d selected_nonzero=%0d terminal=%0d nonterminal=%0d resets=%0d writes_zero=%0d writes_nonzero=%0d reused=%0d clears=%0d",
 span_control_checks,span_control_select_zero,span_control_select_nonzero,span_control_terminal,span_control_nonterminal,span_control_resets,
 gpu.proof_count_zero_writes,gpu.proof_count_nonzero_writes,gpu.proof_count_reused,gpu.proof_count_clears);
'''
def prepare():
 assert json.loads((a.P/'gpu-span-control-v17/literal/result.json').read_text())['passed']
 ring.prepare()
 p=a.OUT/'model/frozen/common/gpu_core.v';s=p.read_text()
 marker='reg [3:0]  spanprod_cnt_valid;'
 s=a.rep(s,marker,marker+'''
reg[3:0] proof_cnt_valid_old;
reg proof_cur_count_valid_old;
reg[63:0]proof_count_zero_writes=0,proof_count_nonzero_writes=0,proof_count_reused=0,proof_count_clears=0;
''')
 # Mirror every original valid-mask write at the exact original priority/site.
 def mirror(m):
  addr,value=m.group(1),m.group(2)
  if addr is None:
   assert value.strip()=="4'b0000",value
   return m.group(0)+" proof_cnt_valid_old <= 4'b0000; proof_count_clears <= proof_count_clears+1;"
  return m.group(0)+" proof_cnt_valid_old"+addr+" <= 1'b1;"
 s,n=re.subn(r'\bspanprod_cnt_valid(\[[^\n]+?\])?\s*<=\s*([^;]+);',mirror,s);assert n==14,n
 line='spanprod_cur_count_valid <= spanprod_cnt_valid[spanprod_read_idx];'
 s=a.rep(s,line,line+'\n        proof_cur_count_valid_old <= proof_cnt_valid_old[spanprod_read_idx];')
 line='    if (spanprod_count_we)\n        spanprod_count[spanprod_count_wa] <= spanprod_count_wd;'
 s=a.rep(s,line,'''    if (spanprod_count_we) begin
        if(spanprod_select)$fatal(1,"count RAM read/write ownership collision");
        if(spanprod_count_wd==0)proof_count_zero_writes<=proof_count_zero_writes+1;
        else proof_count_nonzero_writes<=proof_count_nonzero_writes+1;
        if(proof_cnt_valid_old[spanprod_count_wa])proof_count_reused<=proof_count_reused+1;
        spanprod_count[spanprod_count_wa] <= spanprod_count_wd;
    end''')
 p.write_text(s)
 # Update final instrumented-source binding without rewriting inherited recipe records.
 (a.OUT/'span-control-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'span-control-inputs.json',dict(candidate_sha256=a.sha(a.CAND),parent_manifest_sha256=a.sha(review.OLD/'manifest.json'),model_gpu_sha256=a.sha(p),mask_write_sites=n,recipe_sha256=a.sha(Path(__file__))))
def control_result():
 logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'))
 assert len(logs)==16
 logs+=list(a.OUT.glob('zero-records/candidate-*.log'))
 totals={};rows=[]
 for p in logs:
  s=p.read_text();assert'FATAL'not in s
  m=re.search(r'SPAN_CONTROL_AUDIT (.*)',s);assert m,p
  counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',m.group(1))}
  for k,v in counts.items():totals[k]=totals.get(k,0)+v
  rows.append(dict(log=str(p.relative_to(a.OUT)),counts=counts))
 assert all(v>0 for v in totals.values()),totals
 a.save(a.OUT/'span-control-result.json',dict(passed=True,pairs=len(logs),totals=totals,runs=rows))
def scope_resume_counts():
 # The inherited generic parser can match the new SPAN_CONTROL_AUDIT checks
 # token. Keep its original output and replace only documentary resume counts
 # using the dedicated line; exact cycle/output comparisons are unchanged.
 for rel in ['resume-result.json','boundaries-v3/resume-result.json']:
  p=a.OUT/rel;j=json.loads(p.read_text());original=p.with_name('unscoped-'+p.name)
  assert not original.exists();original.write_bytes(p.read_bytes())
  if rel=='resume-result.json':
   for row in j['exact_cycle_and_output_pairs']:
    log=(a.OUT/'runs'/row['run']/'run.log').read_text()
    row['resume']=dict(re.findall(r'(checks|affine|perspective|combine)=(\d+)',re.search(r'RESUME_AUDIT (.*)',log).group(1)))
  else:
   for row in j['pairs']:
    log=(a.OUT/'boundaries-v3'/row['run']).read_text()
    row['counts']={k:int(v)for k,v in re.findall(r'(checks|affine|perspective|combine)=(\d+)',re.search(r'RESUME_AUDIT (.*)',log).group(1))}
   for target,key in [('affine_handoffs','affine'),('perspective_handoffs','perspective'),('combine_handoffs','combine')]:j[target]=sum(row['counts'][key]for row in j['pairs'])
  p.write_text(json.dumps(j,indent=2)+'\n')

if __name__=='__main__':
 for name in sys.argv[1:]:
  if name in ('prepare','control_result','scope_resume_counts'):globals()[name]()
  elif name=='ring_result':ring.ring_result()
  elif name=='origin_result':ring.origin.origin_result()
  elif name in ('run','attribute_result'):getattr(prior,name)()
  elif name=='paths_result':prior.prior.paths_result()
  elif name=='request_result':prior.prior.prior.request_result()
  elif name=='queue_result':prior.prior.prior.q.queue_result()
  elif hasattr(review,name):getattr(review,name)()
  else:getattr(a,name)()
