#!/usr/bin/env python3
"""Matched recycler-client frames with hot0/full-word write speculation; no FPGA fit."""
import argparse,csv,json,os,re,shutil,subprocess,sys
from pathlib import Path
from phase10_experiment import CAMP,ROOT,module,sha,write
P=CAMP/'phase10';OLD=P/'lod5-recycle-coupled-v1';OUT=P/'cache-write-lod5-coupled-v2'
COMP=P/'cache-write-speculate-v2';APP=P/'lod5-recycle-driver-v1/app.elf'
COUNTERS=r'''
integer spec_aw=0,spec_hot0=0,spec_hot1=0,spec_miss=0,spec_bypass=0,spec_wait=0,spec_wait_w=0,spec_hot1_full=0,spec_wclear_full=0;
reg spec_pending=0;
reg [25:0] spec_addr;
reg [6:0] spec_slot;
always @(posedge clk) begin
 if(gpu_render_cache.reset_n) begin
  if(gpu_render_cache.s_awvalid && gpu_render_cache.s_awready) begin
   spec_aw<=spec_aw+1;
   if(!gpu_render_cache.overlaps(gpu_render_cache.s_awaddr,gpu_render_cache.s_awlen)
       || (!gpu_render_cache.has_lines && (gpu_render_cache.write_no_allocate || gpu_render_cache.s_awlen>=7)))
    spec_bypass<=spec_bypass+1;
   else if(gpu_render_cache.hot0_hit)spec_hot0<=spec_hot0+1;
   else if(gpu_render_cache.hot1_hit)spec_hot1<=spec_hot1+1;
   else spec_miss<=spec_miss+1;
  end
  if(spec_pending) begin
   assert(gpu_render_cache.state==6);
   assert(gpu_render_cache.s_wready == (gpu_render_cache.s_wstrb==15));
   assert(gpu_render_cache.addr==spec_addr && gpu_render_cache.slot==spec_slot);
   if(gpu_render_cache.s_wvalid && gpu_render_cache.s_wready)spec_hot1_full<=spec_hot1_full+1;
   else begin
    spec_wait<=spec_wait+1;
    if(gpu_render_cache.s_wvalid)spec_wait_w<=spec_wait_w+1;
   end
  end
  if(gpu_render_cache.state==6 && !spec_pending && gpu_render_cache.s_wvalid && gpu_render_cache.s_wready)
   spec_wclear_full<=spec_wclear_full+1;
 end
 spec_pending<=gpu_render_cache.reset_n && gpu_render_cache.s_awvalid && gpu_render_cache.s_awready
   && gpu_render_cache.has_lines && !gpu_render_cache.hot0_hit && gpu_render_cache.hot1_hit;
 spec_addr<=gpu_render_cache.s_awaddr;spec_slot<=gpu_render_cache.hot_slot;
end
final begin
 $display("WRITE_SPEC aw=%0d hot0=%0d hot1=%0d miss=%0d bypass=%0d wait=%0d wait_w=%0d hot1_full=%0d wclear_full=%0d",spec_aw,spec_hot0,spec_hot1,spec_miss,spec_bypass,spec_wait,spec_wait_w,spec_hot1_full,spec_wclear_full);
end
'''
def prepare():
 assert sha(OLD/'manifest.json')=='20681e2b770a99b046df791eb0cf873a6c3431fe1ba2939ec6efab033d59ec28'
 for n,h in json.loads((OLD/'manifest.json').read_text())['files'].items():assert sha(OLD/n)==h,n
 assert sha(COMP/'manifest.json')=='d1a1ce63aaf2f38f9f170d529bd6ddf97972a0fc3e2a2f7a56bac33a0068a661'
 assert json.loads((COMP/'protocol/result.json').read_text())['passed']
 assert sha(APP)=='bb41f2088130935dc637d55a4a1dbd0afddb68f94794440ddb5a67e504d4a2c5'
 OUT.mkdir(exist_ok=False)
 shutil.copytree(OLD/'model/frozen',OUT/'model/frozen')
 shutil.copytree(OLD/'qsim',OUT/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
 shutil.copyfile(COMP/'gpu_color_depth_cache.sv',OUT/'model/frozen/common/gpu_color_depth_cache.sv')
 p=OUT/'model/frozen/tb_gpu.v';s=p.read_text();needle='.REGISTER_FILL_HAZARD(1)';assert s.count(needle)==1
 s=s.replace(needle,needle+', .SPECULATE_WRITE_HOT0(1)')
 # tb_gpu contains additional modules; inject counters only before its end.
 where=s.index('endmodule');s=s[:where]+COUNTERS+'\n'+s[where:];p.write_text(s)
 sourcebuild=P/'cache-stage-lod5-coupled-v2/build-command.json'
 cmd=[x.replace(str(sourcebuild.parent),str(OUT))for x in json.loads(sourcebuild.read_text())]
 cmd.insert(1,'--assert');write(OUT/'build-command.json',cmd)
 shutil.copyfile(__file__,OUT/'recipe.py')
 write(OUT/'prepared.json',dict(scope=__doc__,parent_manifest_sha256=sha(OLD/'manifest.json'),
  component_source_sha256=sha(COMP/'gpu_color_depth_cache.sv'),component_protocol_sha256=sha(COMP/'protocol/result.json'),
  elf_sha256=sha(APP),source_sha256={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()},
  helper_sha256=sha(Path(__file__)),quartus_invoked=False,deployment_ready=False))
def build():
 with(OUT/'build-qsim.log').open('x')as f:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o'for n in'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=OUT/'qsim',stdout=f,stderr=subprocess.STDOUT,check=True)
 cmd=json.loads((OUT/'build-command.json').read_text())
 with(OUT/'model/build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'model/build.json',dict(command=cmd,simulator_sha256=sha(OUT/'model/obj/Vtb_gpu')))
def run(window):
 m=module('cache_write_lod5_runner',ROOT/'tools/experiments/os30_overlap_20261006/run.py');m.OUT=OUT
 os.environ['IDEAL_GPU_MEMORY']='0';os.environ['LOD5_MODEL_MODE']='normal'
 sys.argv=[str(Path(__file__)),window,'--elf',str(APP),'--start','360'if window=='warm'else'234','--frames','8'];m.main()
 d=OUT/f'run-{window}';assert 'FATAL'not in(d/'run.log').read_text()
 p=d/'inputs.json';v=json.loads(p.read_text());v['environment'].update(IDEAL_GPU_MEMORY='0',LOD5_MODEL_MODE='normal');p.write_text(json.dumps(v,indent=2)+'\n')
def seal():
 prep=json.loads((OUT/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(OUT/n)==h,n
 changed=[str(p.relative_to(OLD/'model/frozen'))for p in(OLD/'model/frozen').rglob('*')if p.is_file()and sha(p)!=sha(OUT/'model/frozen'/p.relative_to(OLD/'model/frozen'))]
 assert sorted(changed)==['common/gpu_color_depth_cache.sv','tb_gpu.v']
 results={}
 for window,start in [('warm',360),('cold',234)]:
  a=OLD/f'run-{window}';b=OUT/f'run-{window}'
  for d in(a,b):
   inv=json.loads((d/'inputs.json').read_text());assert inv['success']and inv['frames']==8 and inv['start']==start and inv['traffic']==1
   assert sha(d/'sm64.elf')==prep['elf_sha256']
   for n,h in inv['results_sha256'].items():assert sha(d/n)==h,n
  for n in('scene.csv','geometry.csv','culling.csv'):assert(a/n).read_bytes()==(b/n).read_bytes(),n
  images=[]
  for i in range(start,start+8):
   n=f'frame_{i:05}.bin';assert(a/n).read_bytes()==(b/n).read_bytes(),n;images.append(dict(frame=i,sha256=sha(a/n)))
  timing={}
  for label,d in [('baseline',a),('speculative_write',b)]:
   c=json.loads((d/'coupled.json').read_text());assert c['presented']==c['frames']==8 and not c['memory_errors']and not c['cram1_errors']and not c['ring_overflow']
   cnt=list(csv.DictReader((d/'counters.csv').open()))[-1]
   timing[label]=dict(frame_ms=c['cpu_cycles']/800000,gpu_busy_ms=int(cnt['all_gpu_busy'])/800000,
                     rdptr_polls=int(cnt['rdptr_polls']),publications=int(cnt['publications']),uploaded_words=int(cnt['uploaded_words']))
  assert timing['baseline']['uploaded_words']==timing['speculative_write']['uploaded_words']
  cover=re.search(r'^WRITE_SPEC (.+)$',(b/'run.log').read_text(),re.M);assert cover
  counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',cover[1])}
  assert counts['aw']==sum(counts[k]for k in('hot0','hot1','miss','bypass'))and counts['hot1']==counts['wait']+counts['hot1_full']
  x=timing['baseline']['frame_ms'];y=timing['speculative_write']['frame_ms']
  results[window]=dict(all8_actual_color_depth_exact=True,all_histories_exact=True,images=images,timing=timing,
                      coverage=counts,hot1_extra_wait_cycles_per_frame=counts['wait']/8,
                      wclear_full_shortcuts_per_frame=counts['wclear_full']/8,
                      frame_delta_ms=y-x,frame_change_percent=(y/x-1)*100)
 write(OUT/'result.json',dict(passed=True,results=results,source_sha256=prep['component_source_sha256'],
  parameter=dict(REGISTER_READ_LOOKUP=1,REGISTER_FILL_HAZARD=1,SPECULATE_WRITE_HOT0=1),deployment_ready=False,
  limits=['Actual GPU/cache/LOAD/SDRAM/full16MiB CRAM RTL; CPU/cache timing and background traffic modeled. Not Pocket FPS.',
          'Only cache RTL, its parameter, and passive assertion/counters differ from recycler control.',
          'Underlying control uses span-v3 GPU and unified-v1 geometry. Compact-v3 zero-cycle equivalence bridges are separate.',
          'Partial hot1 waits and full WCLEAR shortcuts are not directly additive to frame time because memory/CPU/GPU overlap changes. No fit or timing result.']))
 (OUT/'REPORT.txt').write_text('Hot0 write speculation, matched recycler client.\n\n'+''.join(
  f"{w}: {r['timing']['baseline']['frame_ms']:.8f} -> {r['timing']['speculative_write']['frame_ms']:.8f} ms/frame ({r['frame_change_percent']:+.4f}%). Hot1 extra waits/frame {r['hot1_extra_wait_cycles_per_frame']:.3f}; WCLEAR shortcuts/frame {r['wclear_full_shortcuts_per_frame']:.3f}. All eight actual color/depth buffers and histories match.\n"for w,r in results.items())+
  '\nActual GPU/cache/LOAD/memory RTL with modeled CPU timing; no physical FPS, fitted timing or deployment claim.\n')
 files={str(p.relative_to(OUT)):sha(p)for p in OUT.rglob('*')if p.is_file()and'obj'not in p.parts and'build'not in p.parts}
 files['model/obj/Vtb_gpu']=sha(OUT/'model/obj/Vtb_gpu')
 write(OUT/'manifest.json',dict(passed=True,deployment_ready=False,files=files,
  evidence_sha256={str(p.relative_to(ROOT)):sha(p)for p in[OLD/'manifest.json',COMP/'manifest.json',COMP/'prepared.json',COMP/'protocol/result.json',Path(__file__)]}))
 print(json.dumps(results,indent=2));print(sha(OUT/'manifest.json'))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','build','run','seal']);p.add_argument('--window',choices=['warm','cold'],default='warm');a=p.parse_args()
 run(a.window)if a.action=='run'else globals()[a.action]()
