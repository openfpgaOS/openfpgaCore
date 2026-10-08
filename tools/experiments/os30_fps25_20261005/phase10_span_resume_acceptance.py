#!/usr/bin/env python3
"""Independent v5 resume-state invariants and differential v3 pixels/cycles."""
import inspect,json,re,sys
from pathlib import Path
import phase10_span_chunk_acceptance as chunk
a=chunk.a
a.OUT=a.P/'span-lookahead-review-v3'
a.CAND=a.P/'span-lookahead-v5/gpu_core.v'
OLD=a.P/'span-lookahead-review-v2'

DROPPED=['cb_inv_a6','sp_B_step','sp_Db_step','sp_Dg_step','sp_Dr_step','sp_R_step',
 'sp_a6','sp_blend','sp_cd_combine','sp_clamp_enable','sp_colormap_id','sp_const_alpha',
 'sp_fastpath','sp_light_step','sp_persp_q29_mode','sp_q29_z_enable','sp_rgb',
 'sp_s_max_mirrored','sp_s_min_mirrored','sp_t_max_mirrored','sp_t_min_mirrored',
 'sp_tex_addr','sp_tex_h_octave','sp_tex_w_octave','sp_tex_width','sp_truecolor',
 'sp_z_step','sp_z_test_enable','sp_z_value_step','sp_z_write_enable','sp_zc_step',
 'sp_zinv_step','sp_zinv_step_zero']

a.PROBE+='''
// Every field omitted by resume must retain the initial EMIT value. Separate
// bitwise assertions identify an omitted field rather than only a pixel mismatch.
reg resume_after_emit=0;
reg [63:0] resume_checks=0,resume_affine=0,resume_persp=0,resume_combine=0;
'''
for name in DROPPED:
 a.PROBE+=f'reg [$bits(gpu.{name})-1:0] resume_initial_{name};\n'
a.PROBE+='''always @(posedge clk)begin
 if(!reset_n||gpu.soft_reset)resume_after_emit<=0;
 else begin
  resume_after_emit<=gpu.state==gpu.S_SPANPROD_EMIT&&gpu.spanprod_cur_nonzero;
'''
for name in DROPPED:
 a.PROBE+=f'  if(resume_after_emit)resume_initial_{name}<=gpu.{name};\n'
a.PROBE+='''  if(audit_handoff)begin
   resume_checks<=resume_checks+1;
   if(gpu.spanprod_attr_persp)resume_persp<=resume_persp+1;
   else resume_affine<=resume_affine+1;
   if(gpu.sp_cd_combine)resume_combine<=resume_combine+1;
'''
for name in DROPPED:
 a.PROBE+=f'   if(gpu.{name}!==resume_initial_{name})$fatal(1,"resume changed constant {name}");\n'
a.PROBE+='''  end
 end
end
final $display("RESUME_AUDIT checks=%0d affine=%0d perspective=%0d combine=%0d",resume_checks,resume_affine,resume_persp,resume_combine);
'''

def prepare():
 s=a.CAND.read_text()
 def targets(name):
  t=s.split('task '+name+';')[1].split('endtask')[0]
  return set(re.findall(r'\b(\w+)\s*(?:\[[^\]]+\])?\s*<=',t))
 assert targets('spanprod_load_generated_span')-targets('spanprod_resume_generated_span')==set(DROPPED)
 chunk.prepare()
 (a.OUT/'resume-recipe.py').write_text(Path(__file__).read_text())
 a.save(a.OUT/'resume-inputs.json',dict(recipe_sha256=a.sha(Path(__file__)),
  chunk_recipe_sha256=a.sha(Path(chunk.__file__)),candidate_sha256=a.sha(a.CAND),
  old_manifest_sha256=a.sha(OLD/'manifest.json'),omitted_targets=DROPPED))

def analyze():
 chunk.analyze()
 pairs=[]
 for p in sorted((a.OUT/'runs').glob('candidate-*/run.log')):
  old=OLD/'runs'/p.parent.name/'run.log'
  newstats=re.search(r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)',p.read_text()).groups()
  oldstats=re.search(r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)',old.read_text()).groups()
  assert newstats==oldstats,(p,newstats,oldstats)
  assert (p.parent/'output.bin').read_bytes()==(old.parent/'output.bin').read_bytes()
  counts=dict(re.findall(r'(checks|affine|perspective|combine)=(\d+)',p.read_text()))
  pairs.append(dict(run=p.parent.name,cycles=int(newstats[0]),resume=counts))
 a.save(a.OUT/'resume-result.json',dict(passed=True,exact_cycle_and_output_pairs=pairs,omitted_targets_checked=len(DROPPED)))

def boundaries():
 # Use the reviewed generator with additional actual wire-format combinations.
 source=inspect.getsource(a.boundaries)
 source=a.rep(source,"d=OUT/'boundaries-v2'","d=OUT/'boundaries-v3'")
 source=a.rep(source,'for shape in range(12):','for shape_id in range(48):\n        shape=shape_id%12;combine=bool(shape_id&16);affine=bool(shape_id&32) and zmode==0')
 source=a.rep(source,'ctl=flags|1<<12|axis<<16|zmode<<24|1<<31','ctl=flags|(0 if affine else 1<<12)|axis<<16|zmode<<24|1<<31|(int(combine)<<30)')
 source=a.rep(source,'words+=cmd(0x4e,tri)','if combine:tri += [0x07e0f800,0x001f]\n        words+=cmd(0x4e,tri)')
 source=a.rep(source,'cases.append(dict(axis=axis,zmode=zmode,shape=shape,alpha=alpha,blend=blend))','cases.append(dict(axis=axis,zmode=zmode,shape=shape,alpha=alpha,blend=blend,combine=combine,affine=affine))')
 source=source.replace("('baseline',BASE/'obj/Vtb_gpu')","('baseline',OLD/'model/obj/Vtb_gpu')")
 ns=dict(a.__dict__,OLD=OLD);exec(source,ns);ns['boundaries']()
 d=a.OUT/'boundaries-v3';pairs=[];affine=persp=combine=0
 for p in sorted(d.glob('candidate-*.log')):
  old=d/p.name.replace('candidate-','baseline-')
  f=lambda p:re.search(r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)',p.read_text()).groups()
  assert f(p)==f(old),(p,f(p),f(old))
  n={k:int(v) for k,v in re.findall(r'(checks|affine|perspective|combine)=(\d+)',p.read_text())}
  affine+=n['affine'];persp+=n['perspective'];combine+=n['combine'];pairs.append(dict(run=p.name,counts=n))
 assert affine and persp and combine,(affine,persp,combine)
 a.save(d/'resume-result.json',dict(passed=True,cycles_equal=True,affine_handoffs=affine,perspective_handoffs=persp,combine_handoffs=combine,pairs=pairs))

if __name__=='__main__':
 for action in sys.argv[1:]:
  if action in ('prepare','analyze','boundaries'):globals()[action]()
  else:getattr(a,action)()
