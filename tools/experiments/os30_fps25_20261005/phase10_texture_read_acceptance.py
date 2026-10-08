#!/usr/bin/env python3
"""Actual GPU v16 baseline versus only the texture port-A address factor."""
import json
import re
import shutil
import sys
from pathlib import Path
import phase10_gpu_ring_acceptance as ring

a=ring.a
review=ring.review
a.OUT=a.P/'texture-read-review-v1'
a.CAND=a.P/'gpu-ring-equality-v16/gpu_core.v'
review.OLD=a.P/'gpu-ring-equality-review-v16'
SOURCE=a.P/'texture-read-address-v1/gpu_tex_cache.v'
a.PROBE+=r'''
reg[63:0]texture_address_checks=0,texture_address_reads=0,texture_address_writes=0,texture_address_idle_diff=0,texture_address_prime=0;
wire[11:0]texture_address_old_read=gpu.tex_cache.prime_a?
 {gpu.tex_cache.lat_set,gpu.tex_cache.lat_word}:{gpu.tex_cache.addr_set_a,gpu.tex_cache.addr_word_a};
wire[11:0]texture_address_new_read={gpu.tex_cache.read_set_a,gpu.tex_cache.read_word_a};
always @(posedge clk)begin
 if(reset_n)begin
  texture_address_checks<=texture_address_checks+1;
  if(gpu.tex_cache.read_en_a)begin
   if(texture_address_old_read!==texture_address_new_read)$fatal(1,"texture owned read address differs");
   texture_address_reads<=texture_address_reads+1;
  end else if(texture_address_old_read!==texture_address_new_read)texture_address_idle_diff<=texture_address_idle_diff+1;
  if(gpu.tex_cache.data_write||gpu.tex_cache.tag_write)texture_address_writes<=texture_address_writes+1;
  if(gpu.tex_cache.prime_a)texture_address_prime<=texture_address_prime+1;
 end
end
final $display("TEXTURE_READ_AUDIT checks=%0d reads=%0d writes=%0d idle_diff=%0d prime=%0d",
 texture_address_checks,texture_address_reads,texture_address_writes,texture_address_idle_diff,texture_address_prime);
'''


def prepare():
    assert json.loads((SOURCE.parent/'literal/result.json').read_text())['passed']
    ring.prepare()
    cache=a.OUT/'model/frozen/common/gpu_tex_cache.v'
    assert a.sha(cache)==a.sha(SOURCE.parent/'baseline.v')
    shutil.copy2(SOURCE,cache)
    (a.OUT/'texture-recipe.py').write_text(Path(__file__).read_text())
    a.save(a.OUT/'texture-inputs.json',dict(candidate_sha256=a.sha(SOURCE),
        reference_sha256=a.sha(SOURCE.parent/'baseline.v'),
        model_texture_sha256=a.sha(cache),gpu_sha256=a.sha(a.CAND),
        baseline_manifest_sha256=a.sha(review.OLD/'manifest.json')))


def texture_result():
    logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'))
    assert len(logs)==16
    totals={};rows=[]
    for p in logs:
        text=p.read_text();assert 'FATAL'not in text
        m=re.search(r'TEXTURE_READ_AUDIT (.*)',text);assert m,p
        counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',m[1])}
        for k,v in counts.items():totals[k]=totals.get(k,0)+v
        rows.append(dict(log=str(p.relative_to(a.OUT)),counts=counts))
    assert all(v>0 for v in totals.values()),totals
    a.save(a.OUT/'texture-result.json',dict(passed=True,pairs=16,totals=totals,runs=rows))


if __name__=='__main__':
    for name in sys.argv[1:]:
        if name in ('prepare','texture_result'):globals()[name]()
        elif name=='ring_result':ring.ring_result()
        elif name=='origin_result':ring.origin.origin_result()
        elif name in ('run','attribute_result'):getattr(ring.prior,name)()
        elif name=='paths_result':ring.prior.prior.paths_result()
        elif name=='request_result':ring.prior.prior.prior.request_result()
        elif name=='queue_result':ring.prior.prior.prior.q.queue_result()
        elif hasattr(review,name):getattr(review,name)()
        else:getattr(a,name)()
