#!/usr/bin/env python3
"""Exact v7 GPU replay with an independent original reciprocal next-state model."""
import json,re,sys
from pathlib import Path
import phase10_gpu_origin_acceptance as origin
import phase10_gpu_recip_write as component
a=origin.a;review=origin.review;prior=origin.prior
a.OUT=a.P/'gpu-recip-write-review-v2'
a.CAND=a.P/'gpu-recip-write-v1/gpu_core.v'
review.OLD=a.P/'gpu-origin-review-v14'
old=component.BASE.read_text()
body=component.reference_body(old,'recip_original_next').replace('OLD_WE','recip_original_we')
for name in ['persp_pss','PSS_TOP8','PSS_RECIP_SHIFT','PSS_NR_CAPTURE','pss_use_q29','pss_recip_cache_hit','pss_recip_cache_value','pss_q29_recip_shift','persp_clz','recip_rd_data','dsp_p','sp_zinv_step_zero','sp_zinv']:
    body=re.sub(r'\b'+name+r'\b','gpu.'+name,body)
PROBE='''
// Literal original four assignment sites collapsed only for this independent
// reference. It reads pre-edge original inputs, not the new write-data wire.
reg[31:0] recip_original_next,recip_expected;
reg recip_original_we,recip_checked=0;
reg[5:0] recip_shamt;
reg[44:0] recip_funnel;
reg signed[63:0] recip_shifted;
always @*begin
 recip_original_next=gpu.recip_q16_r;
 recip_original_we=0;recip_shamt=0;recip_funnel=0;recip_shifted=0;
 if(reset_n&&!gpu.soft_reset&&gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active)begin
'''+body+''' end
end
reg[63:0] recip_cycles=0,recip_holds=0,recip_seed=0,recip_nr=0,recip_hits=0;
reg[63:0] recip_bias=0,recip_q29=0,recip_consumers=0,recip_clamps=0,recip_resets=0;
always @(posedge clk)begin
 recip_expected<=recip_original_next;
 recip_checked<=1;
 recip_cycles<=recip_cycles+1;
 if(recip_original_we !== (reset_n&&!gpu.soft_reset&&gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.recip_q16_write_enable))
  $fatal(1,"original reciprocal write enable differs");
 if(!reset_n||gpu.soft_reset)recip_resets<=recip_resets+1;
 if(!recip_original_we)recip_holds<=recip_holds+1;
 else begin
  if(gpu.persp_pss==gpu.PSS_RECIP_SHIFT)recip_seed<=recip_seed+1;
  if(gpu.persp_pss==gpu.PSS_NR_CAPTURE)begin
   recip_nr<=recip_nr+1;
   if(!gpu.pss_use_q29&&gpu.sp_zinv_step_zero&&gpu.sp_zinv!=32'sh10000)recip_bias<=recip_bias+1;
  end
  if(gpu.persp_pss==gpu.PSS_TOP8)recip_hits<=recip_hits+1;
  if(gpu.pss_use_q29)recip_q29<=recip_q29+1;
 end
 if(reset_n&&!gpu.soft_reset&&gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active)begin
  if(gpu.persp_pss==gpu.PSS_NR_MUL_X||gpu.persp_pss==gpu.PSS_NR_MUL_Y||gpu.persp_pss==gpu.PSS_MUL||gpu.persp_pss==gpu.PSS_FINAL)
   recip_consumers<=recip_consumers+1;
  if(gpu.pss_zinv_clamp_r)recip_clamps<=recip_clamps+1;
 end
end
always @(negedge clk)if(recip_checked&&gpu.recip_q16_r!==recip_expected)
 $fatal(1,"original reciprocal next-state differs after edge");
final $display("RECIP_WRITE_AUDIT cycles=%0d holds=%0d seed=%0d nr=%0d hits=%0d bias=%0d q29=%0d consumers=%0d clamps=%0d resets=%0d",
 recip_cycles,recip_holds,recip_seed,recip_nr,recip_hits,recip_bias,recip_q29,recip_consumers,recip_clamps,recip_resets);
'''
# Assert every cycle, including no-write states, reset/soft reset and consumers.
a.PROBE+=PROBE

def prepare():
    assert a.sha(component.BASE)==a.sha(review.OLD/'model/frozen/common/gpu_core.v') or a.sha(component.BASE)==json.loads((a.P/'gpu-origin-default-v14/prepared.json').read_text())['candidate_sha256']
    origin.prepare()
    (a.OUT/'recip-recipe.py').write_text(Path(__file__).read_text())
    a.save(a.OUT/'recip-inputs.json',dict(candidate_sha256=a.sha(a.CAND),baseline_sha256=a.sha(component.BASE),baseline_manifest_sha256=a.sha(review.OLD/'manifest.json'),
        sources={str(p.relative_to(a.OUT)):a.sha(p) for p in (a.OUT/'model/frozen').rglob('*') if p.is_file()},reference_scope='Literal old next state and original enable at every edge, including all consumers and reset/hold. No candidate DATA wire feeds the oracle.'))

def recip_result():
    logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'));assert len(logs)==16
    totals={};rows=[]
    for p in logs:
        text=p.read_text();assert 'FATAL' not in text
        m=re.search(r'RECIP_WRITE_AUDIT (.*)',text);assert m,p
        c={k:int(v) for k,v in re.findall(r'(\w+)=(\d+)',m[1])}
        for k,v in c.items():totals[k]=totals.get(k,0)+v
        rows.append(dict(path=str(p.relative_to(a.OUT)),counts=c))
    assert all(totals[k]>0 for k in ['cycles','holds','seed','nr','bias','consumers','resets'])
    assert totals['q29']==totals['hits']==0
    a.save(a.OUT/'recip-result.json',dict(passed=True,pairs=16,totals=totals,runs=rows,Q29_disabled_selected=True,Q29_all_controls_covered_by_component_literal=True))

def seal():
    results={n:json.loads((a.OUT/n).read_text()) for n in ['result.json','resume-result.json','recip-result.json','origin-result.json','attribute-result.json','paths-result.json','request-result.json','queue-result.json']}
    assert all(v['passed'] for v in results.values())
    literal=json.loads((component.OUT/'literal/result.json').read_text());assert literal['passed']
    a.save(component.OUT/'result.json',dict(passed=True,candidate_sha256=a.sha(a.CAND),added_registers=0,added_cycles=0,literal=literal,native=results['recip-result.json'],exact_cycle_pixel_pairs=16,timing_not_measured=True))
    refs=[component.BASE,Path(__file__),Path(component.__file__),review.OLD/'manifest.json']
    a.save(a.OUT/'manifest.json',dict(passed=True,files={str(p.relative_to(a.OUT)):a.sha(p) for p in a.OUT.rglob('*') if p.is_file() and 'obj' not in p.parts},evidence_sha256={str(p.relative_to(a.ROOT)):a.sha(p) for p in refs}))
    (component.OUT/'recipe-final.py').write_text(Path(component.__file__).read_text())
    (component.OUT/'REPORT.txt').write_text('''Reciprocal write-data factoring: PASS
Only the existing recip_q16_r DATA/enable selection changes. Original SHIFT seed, NR refinement with legacy constant-Z bias, and optional Q29 cache-hit arithmetic remain exact. The register writes on precisely the same cycles; reset/soft-reset and all no-write states hold exactly. No new register or state.
Literal seed coverage exhausts65536 LUT words ×32 CLZ values ×2 formats. All32768 control combinations run64 payload bases, plus1m random full inputs, comparing old/new enable and next state. Selected actual GPU tests compare the literal original next state on every edge, plus all inherited ownership assertions and12native/4boundary color/depth/fence and exact cycle pairs. Q29 is disabled in the selected replay; its complete control/data cases are separately checked by the literal probe, not claimed as Q29 rendering coverage.
The change targets ROM→barrel→two DATA-selector cells. No mapper/fitter or timing gain is claimed. Production and old artifacts unchanged.
''')
    a.save(component.OUT/'manifest.json',dict(passed=True,files={str(p.relative_to(component.OUT)):a.sha(p) for p in component.OUT.rglob('*') if p.is_file() and 'obj' not in p.parts},evidence_sha256={str((a.OUT/'manifest.json').relative_to(a.ROOT)):a.sha(a.OUT/'manifest.json')}))
    print(a.sha(component.OUT/'manifest.json'))

if __name__=='__main__':
    for name in sys.argv[1:]:
        if name in ['prepare','recip_result','seal']:globals()[name]()
        elif name=='origin_result':origin.origin_result()
        elif name in ['run','attribute_result']:getattr(prior,name)()
        elif name=='paths_result':prior.prior.paths_result()
        elif name=='request_result':prior.prior.prior.request_result()
        elif name=='queue_result':prior.prior.prior.q.queue_result()
        elif hasattr(review,name):getattr(review,name)()
        else:getattr(a,name)()
