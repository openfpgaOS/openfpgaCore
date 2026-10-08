#!/usr/bin/env python3
"""Paired exact instructions and modeled cycles, separate color/depth replay."""
from common import *
import argparse,csv,re,struct
rows=lambda p:list(csv.DictReader(p.open()))
def metrics(a,b,start,end):
 result={}
 for key in ['insns','est_cycles','gpu_cmd_words','icache_miss_sdram','dcache_miss','mispredict']:
  x,y=sum(int(r[key]) for r in a[start:end]),sum(int(r[key]) for r in b[start:end]);result[key]={'baseline':x,'candidate':y,'saving_percent':100*(1-y/x) if x else None}
 return {'first_frame':start,'last_frame':end-1,'frames':end-start,'metrics':result}
def worddiff(a,b,color=False):
 assert len(a)==len(b)
 aa=struct.unpack('<'+'H'*(len(a)//2),a);bb=struct.unpack('<'+'H'*(len(b)//2),b)
 result={'different_pixels':sum(x!=y for x,y in zip(aa,bb)),'total_pixels':len(aa),'maximum_word_delta':max(abs(x-y) for x,y in zip(aa,bb)),'mean_word_delta':sum(abs(x-y) for x,y in zip(aa,bb))/len(aa)}
 if color:
  def rgb(v):return (((v>>11)*255+15)//31,(((v>>5)&63)*255+31)//63,((v&31)*255+15)//31)
  errors=[tuple(abs(p-q) for p,q in zip(rgb(x),rgb(y))) for x,y in zip(aa,bb)]
  result['rgb8_max_channel_error']=max(max(e) for e in errors)
  result['rgb8_mean_absolute_channel_error']=sum(sum(e) for e in errors)/(3*len(errors))
  result['pixels_max_channel_error_gt']={str(t):sum(max(e)>t for e in errors) for t in [8,32,64]}
 return result
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('label');p.add_argument('--baseline',default='baseline-normal');p.add_argument('--require-replay',action='store_true');a=p.parse_args()
 before,after=capture_path(a.baseline),capture_path(a.label);bm,cm=check_capture(before),check_capture(after)
 def inputfiles(m):return {Path(k).name:v for k,v in m['sha256'].items() if Path(k).name!='sm64.elf'}
 assert inputfiles(bm)==inputfiles(cm)
 assert (before/'scene.csv').read_bytes()==(after/'scene.csv').read_bytes(),'Gameplay state changed'
 x,y=rows(before/'frames.csv'),rows(after/'frames.csv');assert len(x)==len(y)==721
 result={'candidate':a.label,'baseline':a.baseline,'exact_inputs_except_app':True,'scene_identical':True,'baseline_elf_sha256':sha(before/'sm64.elf'),'candidate_elf_sha256':sha(after/'sm64.elf'),'gameplay':metrics(x,y,360,721),'windows':[metrics(x,y,f,min(721,f+60)) for f in range(360,721,60)],'scope':'Actual guest instruction count; estimated single-issue CPU cycles. GPU replay is separate, without CPU/GPU overlap. No device FPS claim.'}
 index={}
 for path in OUT.glob('replay-results-*.json'):
  for r in read(path):
   key=(r['label'],r['mode'],r['frame']);assert key not in index;index[key]=r
 comparisons=[]
 for (label,mode,frame),r in index.items():
  if label!=a.label:continue
  base=index.get((a.baseline,mode,frame))
  if base is None:continue
  assert r['hardware_sha256']==base['hardware_sha256']
  aa=(ROOT/base['binary']).read_bytes();bb=(ROOT/r['binary']).read_bytes()
  comparisons.append({'mode':mode,'frame':frame,'color':worddiff(aa[:153600],bb[:153600],True),'depth':worddiff(aa[153612:],bb[153612:]),'baseline_gpu_cycles':base['stats']['cycles'],'candidate_gpu_cycles':r['stats']['cycles'],'baseline_commands':base['commands'],'candidate_commands':r['commands']})
 result['gpu_comparisons']=comparisons
 if a.require_replay:assert len(comparisons)==16,len(comparisons)
 if comparisons:result['all_replayed_pixels_depth_exact']=all(v['color']['different_pixels']==v['depth']['different_pixels']==0 for v in comparisons)
 result['evidence_sha256']={str(q.relative_to(ROOT)):sha(q) for q in [before/'inputs.json',after/'inputs.json',OUT/'inputs.json',Path(__file__)]}
 (OUT/('results-'+a.label+'.json')).write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k in ['candidate','gameplay','all_replayed_pixels_depth_exact']},indent=2))
if __name__=='__main__':main()
