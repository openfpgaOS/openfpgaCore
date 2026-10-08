#!/usr/bin/env python3
"""Independent pending-chunk ownership checks plus the v2 acceptance corpus."""
import json, re, sys
from pathlib import Path
import phase10_span_acceptance as a

a.OUT=a.P/'span-lookahead-review-v2'
a.CAND=a.P/'span-lookahead-v3/gpu_core.v'
a.PROBE=a.rep(a.PROBE,'reg [1:0] audit_old_index;',
    'reg [1:0] audit_old_index;\nreg audit_old_chunk;\nreg [2:0] audit_old_count;')
a.PROBE=a.rep(a.PROBE,'   if(gpu.spanprod_idx!=audit_old_index+2\'d1)$fatal(1,"handoff index did not advance once");',
'''   if(audit_old_chunk)begin
    if(gpu.spanprod_idx!=0||gpu.spanprod_record_count!=audit_old_count
       ||gpu.spanprod_records_left!=audit_old_count||gpu.tri_fill_idx!=0||gpu.span_la_chunk)
      $fatal(1,"chunk handoff was not atomic");
   end else if(gpu.spanprod_idx!=audit_old_index+2'd1)$fatal(1,"handoff index did not advance once");''')
a.PROBE=a.rep(a.PROBE,'audit_old_constants<=audit_constants;audit_old_index<=gpu.spanprod_idx;',
'''audit_old_constants<=audit_constants;audit_old_index<=gpu.spanprod_idx;
   audit_old_chunk<=gpu.span_la_chunk;audit_old_count<=gpu.tri_fill_idx;''')
a.PROBE=a.rep(a.PROBE,'gpu.spanprod_direct_affine||gpu.spanprod_idx>=gpu.spanprod_last_idx)',
    'gpu.spanprod_direct_affine||(!gpu.span_la_chunk&&gpu.spanprod_idx>=gpu.spanprod_last_idx))')
a.PROBE+=r'''
// Independent FIFO records each walker handshake, then compares the exact
// record later installed by serial EMIT or overlapping source handoff.
reg [47:0] chunk_records[0:31];
reg [4:0] chunk_wr=0,chunk_rd=0;
reg [63:0] chunk_accepted=0,chunk_emitted=0;
reg [63:0] chunk_counts[0:7];
reg chunk_previous_owned=0,chunk_previous_commit=0;
reg [1:0] chunk_previous_index;
reg [2:0] chunk_previous_visible_count;
wire chunk_take=gpu.tri_rec_valid&&gpu.tri_rec_ready;
wire chunk_emit=gpu.cmd_is_tri_walker&&
 ((gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_cur_nonzero)||audit_handoff);
integer ch;
initial for(ch=0;ch<8;ch=ch+1)chunk_counts[ch]=0;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  chunk_wr<=0;chunk_rd<=0;chunk_accepted<=0;chunk_emitted<=0;
  chunk_previous_owned<=0;chunk_previous_commit<=0;
 end else begin
  if(chunk_previous_owned&&!chunk_previous_commit)begin
   if(gpu.spanprod_idx!=chunk_previous_index
      ||gpu.spanprod_record_count!=chunk_previous_visible_count)
    $fatal(1,"pending chunk overwrote visible index/count");
  end
  chunk_previous_owned<=gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_chunk;
  chunk_previous_commit<=audit_handoff;
  chunk_previous_index<=gpu.spanprod_idx;
  chunk_previous_visible_count<=gpu.spanprod_record_count;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la_chunk)begin
   if(gpu.tri_fill_idx>4)$fatal(1,"pending chunk overflow");
   if(gpu.span_la==gpu.LA_FILL&&gpu.tri_fill_idx>=4)$fatal(1,"accepted after full chunk");
   if(gpu.span_la==gpu.LA_READY&&(gpu.tri_fill_idx==0||gpu.tri_fill_idx>4))
    $fatal(1,"empty/invalid chunk became ready");
  end
  if(chunk_take)begin
   if(chunk_accepted-chunk_emitted>=31)$fatal(1,"audit FIFO overflow");
   chunk_records[chunk_wr]<={gpu.tri_rec_u,gpu.tri_rec_v,gpu.tri_rec_count};
   chunk_wr<=chunk_wr+1'b1;chunk_accepted<=chunk_accepted+1;
   if(gpu.state==gpu.S_FRAG_PIPE)chunk_counts[6]<=chunk_counts[6]+1;
  end
  if(chunk_emit)begin
   if(chunk_accepted==chunk_emitted)$fatal(1,"span emitted without walker record");
   if(chunk_records[chunk_rd]!=={gpu.spanprod_cur_u,gpu.spanprod_cur_v,gpu.spanprod_cur_count})
    $fatal(1,"span record lost/reordered across chunk");
   chunk_rd<=chunk_rd+1'b1;chunk_emitted<=chunk_emitted+1;
  end
  if(audit_handoff&&gpu.span_la_chunk)begin
   chunk_counts[0]<=chunk_counts[0]+1;
   chunk_counts[gpu.tri_fill_idx]<=chunk_counts[gpu.tri_fill_idx]+1;
   if(gpu.tri_walker_done)chunk_counts[7]<=chunk_counts[7]+1;
  end
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la==gpu.LA_FILL
     &&gpu.tri_walker_done&&gpu.tri_fill_idx==0)chunk_counts[5]<=chunk_counts[5]+1;
 end
end
final begin
 if(chunk_accepted!=chunk_emitted)$fatal(1,"final walker records stranded");
 $display("CHUNK_RECORDS accepted=%0d emitted=%0d",chunk_accepted,chunk_emitted);
 for(ch=0;ch<8;ch=ch+1)$display("CHUNK_AUDIT %0d %0d",ch,chunk_counts[ch]);
end
'''

def prepare():
    a.prepare()
    (a.OUT/'chunk-recipe.py').write_text(Path(__file__).read_text())
    a.save(a.OUT/'chunk-inputs.json',dict(recipe_sha256=a.sha(Path(__file__)),
        inherited_recipe_sha256=a.sha(Path(a.__file__)),candidate_sha256=a.sha(a.CAND)))
def analyze():
    a.analyze()
    records=[];counts=[0]*8
    for p in sorted((a.OUT/'runs').glob('candidate-*/run.log')):
        n={int(k):int(v) for k,v in re.findall(r'CHUNK_AUDIT (\d+) (\d+)',p.read_text())}
        for k,v in n.items():counts[k]+=v
        records.append(dict(path=str(p.relative_to(a.OUT)),counts=n))
    assert all(counts[k]>0 for k in (0,1,2,3,4,6,7)),counts
    a.save(a.OUT/'chunk-result.json',dict(passed=True,counters=counts,runs=records))
if __name__=='__main__':
    for action in sys.argv[1:]:
        if action in ('prepare','analyze'):globals()[action]()
        else:getattr(a,action)()
