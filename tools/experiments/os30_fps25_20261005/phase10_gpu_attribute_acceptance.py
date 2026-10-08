#!/usr/bin/env python3
"""Independent clamp/attribute ownership, exact native pixels and cycles."""
import concurrent.futures,json,re,subprocess,sys
from pathlib import Path
import phase10_gpu_paths_acceptance as prior
a=prior.a;review=prior.review
a.OUT=a.P/'gpu-attribute-review-v13';a.CAND=a.P/'gpu-attribute-v13/gpu_core.v'
review.OLD=a.P/'gpu-paths-review-v11'
old=(a.P/'gpu-paths-v11/gpu_core.v').read_text()
case=old[old.index('    case (dv_attr)'):old.index('    deriv_a0 = deriv_attr[dv_ord[0]];')]
case=case.replace('deriv_attr','attribute_reference')
for name in ['dv_attr','dv_szi','dv_tzi','vt_zi','vt_lrow','vt_rrow','vt_brow','vt_depth','vt_Drrow','vt_Dgrow','vt_Dbrow']:
 case=re.sub(r'\b'+name+r'\b','gpu.'+name,case)
a.PROBE+='\nreg [31:0] attribute_reference[0:2];\nalways @*begin\n'+case+'end\n'
a.PROBE+=r'''
reg clamp_owned=0,clamp_capture_check=0,clamp_result_check=0;
reg [31:0] clamp_old_zi,clamp_old_abs;
reg [29:0] clamp_expected_limit;
reg clamp_expected_result;
reg [63:0] clamp_captures=0,clamp_checks=0,clamp_q29=0,clamp_legacy=0;
reg [63:0] clamp_shrink_true=0,clamp_shrink_false=0,clamp_negative_new=0,clamp_zero_threshold=0;
reg [31:0] clamp_live_abs;
reg clamp_reference_shrink,clamp_reference_cross;
reg attr_owned=0;
reg [63:0] attr_owned_cycles=0,attr_captures=0,attr_origins=0;
reg [9:0] attr_seen=0;
integer attr_k;
always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)begin
  clamp_owned<=0;clamp_capture_check<=0;clamp_result_check<=0;attr_owned<=0;
 end else begin
  if(clamp_capture_check&&gpu.pss_zinv_shrink_limit_r!==clamp_expected_limit)
   $fatal(1,"clamp captured threshold differs from legacy unsigned abs");
  if(clamp_result_check&&gpu.pss_zinv_clamp_r!==clamp_expected_result)
   $fatal(1,"clamp output differs from legacy owned comparison");
  clamp_capture_check<=0;clamp_result_check<=0;
  if(!gpu.persp_active)clamp_owned<=0;
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.persp_pss==gpu.PSS_ADV)begin
   clamp_old_zi<=gpu.sp_zinv;
   clamp_old_abs<=gpu.sp_zinv[31]?-gpu.sp_zinv:gpu.sp_zinv;
   clamp_expected_limit<=((gpu.sp_zinv[31]?-gpu.sp_zinv:gpu.sp_zinv)>>2)-32'd1;
   clamp_owned<=1;clamp_capture_check<=1;clamp_captures<=clamp_captures+1;
  end
  if(gpu.state==gpu.S_FRAG_PIPE&&gpu.persp_active&&gpu.persp_pss==gpu.PSS_ADV_CLAMP)begin
   if(!clamp_owned)$fatal(1,"clamp used reset/unowned threshold");
   clamp_live_abs=gpu.sp_zinv[31]?-gpu.sp_zinv:gpu.sp_zinv;
   clamp_reference_shrink=clamp_live_abs<(clamp_old_abs>>2);
   clamp_reference_cross=(gpu.sp_zinv[31]^clamp_old_zi[31])&&(clamp_old_zi!=0)&&(gpu.sp_zinv!=0);
   if(gpu.pss_zinv_shrunk!==clamp_reference_shrink)$fatal(1,"live shrink differs");
   if(gpu.pss_zinv_prev_negative!==clamp_old_zi[31]||gpu.pss_zinv_prev_nonzero!==(clamp_old_zi!=0))
    $fatal(1,"clamp old-sign capture differs");
   clamp_expected_result<=clamp_reference_cross||(gpu.pss_use_q29?(clamp_live_abs==0):clamp_reference_shrink);
   clamp_result_check<=1;clamp_checks<=clamp_checks+1;
   if(gpu.pss_use_q29)clamp_q29<=clamp_q29+1;else clamp_legacy<=clamp_legacy+1;
   if(clamp_reference_shrink)clamp_shrink_true<=clamp_shrink_true+1;else clamp_shrink_false<=clamp_shrink_false+1;
   if(gpu.sp_zinv[31])clamp_negative_new<=clamp_negative_new+1;
   if((clamp_old_abs>>2)==0)clamp_zero_threshold<=clamp_zero_threshold+1;
  end
  if(gpu.state!=gpu.S_TRI_DERIVE||gpu.dstate==gpu.DRV_SORT_A)attr_owned<=0;
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_RDET&&gpu.rdet_cnt==1)attr_owned<=1;
  if(gpu.state==gpu.S_TRI_DERIVE&&attr_owned)begin
   if(gpu.dv_attr>9||gpu.dv_attr_onehot!==(10'b1<<gpu.dv_attr))$fatal(1,"owned attribute onehot/counter mismatch");
   for(attr_k=0;attr_k<3;attr_k=attr_k+1)
    if(gpu.deriv_attr[attr_k]!==attribute_reference[attr_k])$fatal(1,"owned literal attribute mux differs");
   attr_owned_cycles<=attr_owned_cycles+1;attr_seen<=attr_seen|gpu.dv_attr_onehot;
  end
  if(gpu.state==gpu.S_TRI_DERIVE&&gpu.dstate==gpu.DRV_DA_SEL)begin
   if(!attr_owned)$fatal(1,"attribute captured without initialization");
   if(gpu.deriv_a0!==attribute_reference[gpu.dv_ord[0]]||gpu.deriv_a1!==attribute_reference[gpu.dv_ord[1]]||gpu.deriv_a2!==attribute_reference[gpu.dv_ord[2]])
    $fatal(1,"sorted owned attribute value differs");
   attr_captures<=attr_captures+1;
  end
  if(gpu.state==gpu.S_TRI_DERIVE&&(gpu.dstate==gpu.DRV_ORG_CAP||gpu.dstate==gpu.DRV_ORG_FORM))begin
   if(!attr_owned)$fatal(1,"attribute origin used before ownership");attr_origins<=attr_origins+1;
  end
 end
end
final $display("CLAMP_ATTRIBUTE_AUDIT captures=%0d clamps=%0d q29=%0d legacy=%0d shrink_true=%0d shrink_false=%0d negative_new=%0d zero_threshold=%0d attr_cycles=%0d attr_captures=%0d attr_origins=%0d attr_seen=%0d",
 clamp_captures,clamp_checks,clamp_q29,clamp_legacy,clamp_shrink_true,clamp_shrink_false,clamp_negative_new,clamp_zero_threshold,attr_owned_cycles,attr_captures,attr_origins,attr_seen);
'''
def prepare():
 math=a.P/'gpu-clamp-review-v12/manifest.json';assert json.loads(math.read_text())['passed']
 assert a.sha(a.CAND)=='61f4776cada85fb3d209204b82444027054f18ced59eb46e970458c783110888'
 prior.prepare()
 (a.OUT/'attribute-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'attribute-inputs.json',dict(recipe_sha256=a.sha(Path(__file__)),candidate_sha256=a.sha(a.CAND),baseline_manifest_sha256=a.sha(review.OLD/'manifest.json'),clamp_math_manifest_sha256=a.sha(math),scope='Combined v13 versus actual frozen v11; 10 new one-hot FFs and 2 fewer clamp FFs, net8. No added FSM states or cycles.'))
def run():
 jobs=[]
 for traffic,frames in [(0,[360,367]),(1,list(range(360,368))),(2,[360,367])]:
  for frame in frames:
   d=a.OUT/'runs'/f'candidate-t{traffic}-f{frame}';d.mkdir(parents=True,exist_ok=False)
   vec=a.P/f'gpu-critical-bound-v2/replay/frame_{frame:05}.vec'
   jobs.append((d,[str(a.OUT/'model/obj/Vtb_gpu'),f'+memory_traffic={traffic}','+fast_tex=1','+tagged_tex=1',str(vec),str(d/'output.bin')]))
 # Preserve actual v11 oracle logs/outputs; they are evidence-bound and do not
 # need another identical run. The inherited analyzer uses these for exact cycles.
 import shutil
 for traffic in (0,2):
  for frame in (360,367):
   dest=a.OUT/'runs'/f'baseline-t{traffic}-f{frame}';src=review.OLD/'runs'/f'candidate-t{traffic}-f{frame}';shutil.copytree(src,dest)
 def one(j):
  d,cmd=j
  with(d/'run.log').open('x')as f:r=subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT)
  a.save(d/'result.json',dict(command=cmd,returncode=r.returncode,log_sha256=a.sha(d/'run.log'),output_sha256=a.sha(d/'output.bin')if(d/'output.bin').exists()else None))
  assert r.returncode==0,(d,r.returncode)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3)as pool:list(pool.map(one,jobs))
def attribute_result():
 logs=list(a.OUT.glob('runs/candidate-*/run.log'))+list(a.OUT.glob('boundaries-v3/candidate-*.log'));assert len(logs)==16
 totals={};rows=[];seen=0
 for p in logs:
  text=p.read_text();assert'FATAL'not in text
  c={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'CLAMP_ATTRIBUTE_AUDIT (.*)',text).group(1))}
  for k,v in c.items():totals[k]=totals.get(k,0)+v
  seen|=c['attr_seen'];rows.append(dict(log=str(p.relative_to(a.OUT)),counts=c))
 assert all(totals[k]>0 for k in ('captures','clamps','legacy','attr_cycles','attr_captures','attr_origins'))
 # The selected os30 raster fixture explicitly excludes PARAM_SPAN_Q29.
 # Its zero-check algebra is covered by the separate literal RTL unit.
 assert '-GINCLUDE_PARAM_SPAN_Q29=0' in json.loads((a.OUT/'model/command.json').read_text())
 assert totals['q29']==0
 assert seen==1023,seen
 (a.OUT/'analysis-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'attribute-result.json',dict(passed=True,pairs=16,totals=totals,attribute_seen_mask=seen,runs=rows,Q29_disabled_in_selected_configuration=True,scope='Reset/capture lifetime, original literal live mux, sorted operands and original clamp predicate on all owned consumers. Exceptional predicate inputs and Q29 zero equivalence independently covered by literal clamp unit/math proof.'))
if __name__=='__main__':
 for name in sys.argv[1:]:
  if name in ('prepare','run','attribute_result'):globals()[name]()
  elif name=='paths_result':prior.paths_result()
  elif name=='request_result':prior.prior.request_result()
  elif name=='queue_result':prior.prior.q.queue_result()
  elif hasattr(review,name):getattr(review,name)()
  else:getattr(a,name)()
