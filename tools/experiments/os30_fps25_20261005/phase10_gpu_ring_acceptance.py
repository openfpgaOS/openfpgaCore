#!/usr/bin/env python3
"""GPU v16/v14 exact replay with live original ring equality/status checks."""
import json
import re
import sys
from pathlib import Path
import phase10_gpu_origin_acceptance as origin

a = origin.a
review = origin.review
prior = origin.prior
a.OUT = a.P / 'gpu-ring-equality-review-v16'
a.CAND = a.P / 'gpu-ring-equality-v16/gpu_core.v'
review.OLD = a.P / 'gpu-origin-review-v14'
a.PROBE += r'''
reg [63:0] ring_equality_cycles=0,ring_equality_empty=0,ring_equality_nonempty=0;
reg [63:0] ring_equality_status=0,ring_equality_reset=0,ring_equality_wraps=0;
reg [11:0] ring_equality_previous_rd=0,ring_equality_previous_wr=0;
wire ring_equality_reference=(gpu.ring_rdptr==gpu.command_wrptr);
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  ring_equality_reset<=ring_equality_reset+1;
  ring_equality_previous_rd<=0;ring_equality_previous_wr<=0;
 end else begin
  if(gpu.ring_empty!==ring_equality_reference)$fatal(1,"ring equality differs from original");
  ring_equality_cycles<=ring_equality_cycles+1;
  if(ring_equality_reference)ring_equality_empty<=ring_equality_empty+1;
  else ring_equality_nonempty<=ring_equality_nonempty+1;
  if(gpu.ring_rdptr<ring_equality_previous_rd||gpu.command_wrptr<ring_equality_previous_wr)
   ring_equality_wraps<=ring_equality_wraps+1;
  ring_equality_previous_rd<=gpu.ring_rdptr;ring_equality_previous_wr<=gpu.command_wrptr;
  if(gpu.reg_addr==4'd5)begin
   if(gpu.reg_rdata[1]!==ring_equality_reference||gpu.reg_rdata[0]!==gpu.busy)
    $fatal(1,"ring status output changed");
   ring_equality_status<=ring_equality_status+1;
  end
 end
end
final $display("RING_EQUALITY_AUDIT cycles=%0d empty=%0d nonempty=%0d status=%0d resets=%0d wraps=%0d",
 ring_equality_cycles,ring_equality_empty,ring_equality_nonempty,ring_equality_status,ring_equality_reset,ring_equality_wraps);
'''


def prepare():
    assert json.loads((a.P / 'gpu-ring-equality-v16/literal/result.json').read_text())['passed']
    origin.prepare()
    (a.OUT / 'ring-recipe.py').write_text(Path(__file__).read_text())
    a.save(a.OUT / 'ring-inputs.json', dict(candidate_sha256=a.sha(a.CAND),
        parent_manifest_sha256=a.sha(review.OLD / 'manifest.json'), recipe_sha256=a.sha(Path(__file__))))


def ring_result():
    logs = list(a.OUT.glob('runs/candidate-*/run.log')) + list(a.OUT.glob('boundaries-v3/candidate-*.log'))
    assert len(logs) == 16
    totals, rows = {}, []
    for p in logs:
        text = p.read_text()
        assert 'FATAL' not in text
        match = re.search(r'RING_EQUALITY_AUDIT (.*)', text)
        assert match, p
        counts = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', match.group(1))}
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        rows.append(dict(log=str(p.relative_to(a.OUT)), counts=counts))
    assert all(totals[k] > 0 for k in ('cycles', 'empty', 'nonempty', 'status', 'resets', 'wraps'))
    a.save(a.OUT / 'ring-result.json', dict(passed=True, pairs=16, totals=totals, runs=rows))


if __name__ == '__main__':
    for name in sys.argv[1:]:
        if name in ('prepare', 'ring_result'):
            globals()[name]()
        elif name == 'origin_result':
            origin.origin_result()
        elif name in ('run', 'attribute_result'):
            getattr(prior, name)()
        elif name == 'paths_result':
            prior.prior.paths_result()
        elif name == 'request_result':
            prior.prior.prior.request_result()
        elif name == 'queue_result':
            prior.prior.prior.q.queue_result()
        elif hasattr(review, name):
            getattr(review, name)()
        else:
            getattr(a, name)()
