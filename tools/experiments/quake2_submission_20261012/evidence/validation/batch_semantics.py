#!/usr/bin/env python3
"""Batching may change progress-observation counts; fence payload/order stays exact.

Keep the original strict bench comparison receipt. This supplementary gate only
accepts its gpu_barriers differences and proves all stateful command bytes,
all CPU framebuffer bytes, all other frame fields and sound calls are exact.
Full selected-RTL RGB565/depth samples are independently required.
"""
from pathlib import Path
import argparse,collections,csv,hashlib,json,struct
R=Path(__file__).resolve().parent
FIELDS=('frame','host_framecount','buffer','gpu_cmd_words','gpu_pixels','hash_cmd','hash_px','hash_z','hash_pal','mem_hazards','gpu_barriers','gpu_wait_px')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def records(p):
 b=p.read_bytes();pos=0;result=[]
 while pos<len(b):
  frame,h=struct.unpack_from('<QI',b,pos);n=(h&8191)+1;result.append((frame,tuple(struct.unpack_from('<'+'I'*n,b,pos+8))));pos+=8+4*n
 assert pos==len(b);return result

def run(candidate,baseline):
 old=R/'comparisons'/(candidate+'-vs-'+baseline+'.json');original=json.loads(old.read_text());out=[]
 for previous in original:
  w=previous['workload'];a=R/'runs'/baseline/w;b=R/'runs'/candidate/w
  aa=list(csv.DictReader((a/'full/frames.csv').open()));bb=list(csv.DictReader((b/'full/frames.csv').open()));assert len(aa)==len(bb)
  diff=[dict(frame=i,field=k,a=x[k],b=y[k])for i,(x,y)in enumerate(zip(aa,bb))for k in FIELDS if x[k]!=y[k]]
  assert all(x['field']=='gpu_barriers'for x in diff),diff[:20]
  assert (a/'commands.bin').read_bytes()==(b/'commands.bin').read_bytes()
  assert (a/'pixels.bin').read_bytes()==(b/'pixels.bin').read_bytes()
  assert previous['sound_service_counts_exact']
  cmds=records(a/'commands.bin');fences=[c for c in cmds if c[1][0]>>24==2];assert fences
  sync=[]
  for p in (a,b):
   s=json.loads((p/'full/summary.json').read_text());gs=s['gpu_sync']
   assert gs['ring_overflows']==gs['queued_words_at_end']==s['env']['unmapped_mmio']==0
   assert gs['wait_px']==gs['wait_words']==0
   assert all(int(row['mem_hazards'])==int(row['gpu_wait_px'])==0 for row in (aa if p==a else bb))
   sync.append(gs)
  assert sync[0]['observations']['fence_read']==sync[1]['observations']['fence_read']
  assert sync[0]['barriers']['fence_read']==sync[1]['barriers']['fence_read']
  counts=collections.Counter(c[1][0]>>24 for c in cmds if c[0]>=20 and c[0]<len(aa))
  out.append(dict(workload=w,passed=True,frame_count=len(aa),all_command_bytes_frame_assignment_and_fences_exact=True,fence_count=len(fences),all_full_width_cpu_frames_exact=True,sound_service_counts_exact=True,other_frame_fields_exact=True,only_changed_frame_field='gpu_barriers',changed_barrier_frames=len(diff),baseline_sync=sync[0],candidate_sync=sync[1],steady_raw_load_packets=counts[0x59],steady_indexed_triangle_packets=counts[0x5b],change_percent=previous['change_percent'],baseline_cycles=previous['baseline_cycles_per_frame'],candidate_cycles=previous['candidate_cycles_per_frame']))
 raster=R/'raster'/(baseline+'-vs-'+candidate+'.json');r=json.loads(raster.read_text());assert r['passed']
 result=dict(passed=True,baseline=baseline,candidate=candidate,original_strict_receipt=str(old),original_strict_sha256=sha(old),original_strict_passed=all(x['passed']for x in original),raster_receipt=str(raster),raster_receipt_sha256=sha(raster),results=out,diagnosis='qsim gpu.c q_observe counts progress observations with new work, including GPU ring read-pointer samples. Coarser reserve sizes change SDK stream flush/ensure boundaries. All fence words and fence observations are equal; command execution semantics and all actual pixels/depth are checked separately.',limitations=['Scalar OS25-derived model with OS30 cache sizes, not Vexii native or Pocket FPS.','Standalone raster maximal-rate producer excludes actual CPU-paced overlap/audio/display contention.','No reduction in GPU command words: packet reservation/copy bookkeeping only.'])
 (R/'comparisons'/(candidate+'-vs-'+baseline+'-batch-semantics.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(dict(passed=True,results=[{k:x[k]for k in('workload','change_percent','steady_raw_load_packets','steady_indexed_triangle_packets')}for x in out]),indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('candidate');p.add_argument('--baseline',default='baseline');a=p.parse_args();run(a.candidate,a.baseline)
