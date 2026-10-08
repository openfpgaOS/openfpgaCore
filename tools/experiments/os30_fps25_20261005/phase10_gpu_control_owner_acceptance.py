#!/usr/bin/env python3
"""GPU owner factoring: original DSP-A shadow and live lookahead induction."""
import json
import re
import sys
from pathlib import Path
import phase10_gpu_recip_acceptance as prior
import phase10_gpu_control_owner as component

a = prior.a
review = prior.review
a.OUT = a.P / 'gpu-control-owner-review-v1'
a.CAND = component.OUT / 'gpu_core.v'
prior_review = a.P / 'gpu-recip-write-review-v2'
# All inherited replay/cycle oracles stay the exact reviewed source. The new
# original-A shadow independently restores the literal immediate-parent sites.
a.PROBE += '''
reg owner_checked=0;
reg[63:0] owner_cycles=0,owner_live_la=0,owner_ready=0,owner_handoffs=0;
reg[63:0] owner_hard_resets=0,owner_soft_resets=0,owner_flush=0,owner_pss_writes=0;
always @(posedge clk)begin
 owner_checked<=1;
 owner_cycles<=owner_cycles+1;
 if(!reset_n)owner_hard_resets<=owner_hard_resets+1;
 else begin
  if(gpu.soft_reset)owner_soft_resets<=owner_soft_resets+1;
  if(gpu.span_la!=gpu.LA_IDLE)begin
   if(!gpu.cmd_is_tri_walker)$fatal(1,"lookahead active without original tri command owner");
   owner_live_la<=owner_live_la+1;
  end
  if(gpu.span_la==gpu.LA_READY)owner_ready<=owner_ready+1;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.span_la==gpu.LA_READY&&gpu.span_la_eligible
     &&gpu.src_done&&!gpu.p0a_valid&&!gpu.p0_valid&&!gpu.persp_active
     &&!gpu.persp_swap_pending&&gpu.persp_pss==gpu.PSS_IDLE)begin
   if(!gpu.cmd_is_tri_walker)$fatal(1,"new handoff differs from original owner gate");
   owner_handoffs<=owner_handoffs+1;
  end
  if(gpu.state==gpu.S_FB_FLUSH)owner_flush<=owner_flush+1;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.pss_dsp_a_write)
   owner_pss_writes<=owner_pss_writes+1;
 end
end
always @(negedge clk)if(owner_checked&&gpu.dsp_a!==gpu.proof_dsp_a_control_old)
 $fatal(1,"DSP-A next-state differs from literal original full writer chain");
final $display("CONTROL_OWNER_AUDIT cycles=%0d live_la=%0d ready=%0d handoffs=%0d hard_resets=%0d soft_resets=%0d flush=%0d pss_writes=%0d",
 owner_cycles,owner_live_la,owner_ready,owner_handoffs,owner_hard_resets,owner_soft_resets,owner_flush,owner_pss_writes);
'''


def prepare():
    assert component.sha(component.BASE) == component.BASE_SHA
    assert component.sha(a.CAND) == json.loads((component.OUT / 'prepared.json').read_text())['candidate_sha256']
    prior.prepare()
    p = a.OUT / 'model/frozen/common/gpu_core.v'
    source = p.read_text()
    # Copy every surviving original DSP-A assignment under its exact original
    # enclosing conditions and priority; omit only the new extracted writer.
    def mirror(match):
        if match.group(1).strip() == 'pss_dsp_a_data':
            return match.group(0)
        return match.group(0) + ' proof_dsp_a_control_old <= ' + match.group(1) + ';'
    source = re.sub(r'\bdsp_a\s*<=\s*([^;]+);', mirror, source)
    index = re.search(r'^reg[^\n]*\bdsp_a\b', source, re.M).start()
    source = source[:index] + 'reg signed [31:0] proof_dsp_a_control_old;\n' + source[index:]
    old = component.BASE.read_text()
    begin = '            if (!persp_active) begin\n                persp_pss <= PSS_IDLE;'
    end = '            if (persp_active && ((persp_pss == PSS_ADV_TAIL_COMMIT)'
    original = old[old.index(begin):old.index(end, old.index(begin))]
    assert len(re.findall(r'\bdsp_a\s*<=', original)) == 8
    restored = re.sub(r'\bdsp_a(?=\s*<=)', 'proof_dsp_a_control_old', original)
    lo = source.index(begin)
    hi = source.index(end, lo)
    assert not re.search(r'\bdsp_a\s*<=', source[lo:hi])
    # The original and selected non-A statements in this region are identical.
    stripped = re.sub(r'^[ \t]*dsp_a\s*<=[^;]+;\n', '', original, flags=re.M)
    assert stripped == source[lo:hi]
    source = source[:lo] + restored + source[hi:]
    p.write_text(source)
    (a.OUT / 'control-owner-recipe.py').write_text(Path(__file__).read_text())
    a.save(a.OUT / 'control-owner-inputs.json', dict(
        parent_sha256=component.sha(component.BASE), candidate_sha256=component.sha(a.CAND),
        model_gpu_sha256=component.sha(p), original_pss_sites=8,
        sources={str(f.relative_to(a.OUT)): a.sha(f) for f in (a.OUT / 'model/frozen').rglob('*') if f.is_file()},
        scope='Simulation-only original DSP-A register copies all surviving old writers and restores the eight literal removed writes at their original positions. Shadow does not feed DUT logic. Per-edge lookahead ownership and old/new handoff predicate assertions.'))


def owner_result():
    logs = list(a.OUT.glob('runs/candidate-*/run.log')) + list(a.OUT.glob('boundaries-v3/candidate-*.log'))
    assert len(logs) == 16
    totals = {}
    rows = []
    for p in logs:
        text = p.read_text()
        assert 'FATAL' not in text
        match = re.search(r'CONTROL_OWNER_AUDIT (.*)', text)
        assert match, p
        counts = {k: int(v) for k, v in re.findall(r'(\w+)=(\d+)', match[1])}
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v
        rows.append(dict(path=str(p.relative_to(a.OUT)), counts=counts))
    assert all(totals[k] > 0 for k in ('cycles', 'live_la', 'ready', 'handoffs', 'hard_resets', 'flush', 'pss_writes'))
    a.save(a.OUT / 'owner-result.json', dict(passed=True, pairs=16, totals=totals, runs=rows,
        original_full_dsp_a_writer_chain_checked_each_edge=True,
        lookahead_owner_and_old_handoff_gate_checked_each_edge=True,
        soft_reset_live_coverage=totals['soft_resets'] > 0,
        limits=['Any zero soft-reset count is a coverage gap for these replays, not an assertion that active abort was tested. The source induction and separate abort test must cover it.']))


if __name__ == '__main__':
    for name in sys.argv[1:]:
        if name in ('prepare', 'owner_result'):
            globals()[name]()
        elif name == 'origin_result':
            prior.origin.origin_result()
        elif name == 'recip_result':
            prior.recip_result()
        elif name in ('run', 'attribute_result'):
            getattr(prior.prior, name)()
        elif name == 'paths_result':
            prior.prior.prior.paths_result()
        elif name == 'request_result':
            prior.prior.prior.prior.request_result()
        elif name == 'queue_result':
            prior.prior.prior.prior.q.queue_result()
        elif hasattr(review, name):
            getattr(review, name)()
        else:
            getattr(a, name)()
