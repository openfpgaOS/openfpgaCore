#!/usr/bin/env python3
"""Summarize unchanged full-F5 code on actual A; never equate qsim differences."""
import csv,hashlib,json,re,struct,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[4];OUT=ROOT/'docs/measurements/os30-fps25-20261005/phase3/vertex-cpu'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def clean(text):
 text=text.split('=== UART BEGIN ===')[0]
 return re.sub(r'\n?\[c=\d+\][^\n]*\n    m1_ar:[^\n]*\n    arb_st=[^\n]*\n','',text)

def owner(offset):
 if offset<104:return ('rdp','nonfloat')
 if offset<3176:return ('lighting_memo','color_or_metadata')
 if offset<6564:
  x=offset-3176
  if 708<=x<772:return ('MVP_matrix','float')
  if 876<=x<924:return ('lighting_or_lookat_coefficients','float')
  if x>=940:
   slot,member=divmod(x-940,36)
   if member<28:return ('vertex_'+('x','y','z','w','w_inv','u','v')[member//4],'float')
   return ('vertex_color' if member==28 else 'vertex_clip_or_padding','nonfloat')
  return ('other_rsp','nonfloat')
 return ('globals','nonfloat')

def parse(mode):
 raw=(OUT/(mode+'-reference.log')).read_text()
 text=clean(raw)
 assert 'ACTUAL_A_REPEATABILITY_PASS' in text and 'KERNEL PASS HAL init' in text
 assert 'KERNEL FAIL' not in text
 assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in raw
 pattern=(r'RUN ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) @#\s*'
          r'HOST_KERNEL retired=(\d+) cycles=(\d+) freeze=(\d+) hazards=(\d+)\s*'
          r'DIV_FREEZE fdiv=(\d+) intdiv=(\d+)([^\n]*)\s*'
          r'TIMER ([0-9a-f]{8}) ([0-9a-f]{8})')
 rows=[]
 for m in re.finditer(pattern,text):
  t=m.groups();cold,job,n=map(lambda x:int(x,16),t[:3]);ret,cyc,fr,haz,fd,di=map(int,t[3:9]);timer,h=int(t[10],16),t[11]
  rows.append(dict(cold_prepared=bool(cold),job=job,vertices=n,retired=ret,marker_cycles=cyc,execute_freeze=fr,dispatch_hazards=haz,fdiv_freeze=fd,intdiv_freeze=di,fdiv_pc={k:int(v)for k,v in re.findall(r'pc_([0-9a-f]{8})=(\d+)',t[9])},timer_cycles=timer,actual_state_fnv32=h))
 assert len(rows)==400,(mode,len(rows))
 assert len({(r['cold_prepared'],r['job'])for r in rows})==400
 diffs=[]
 for m in re.finditer(r'QSIM_DIFF ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8})',text):
  job,offset,actual,expected=map(lambda x:int(x,16),m.groups());member,kind=owner(offset)
  rec=dict(job=job,state_offset=offset,owner=member,kind=kind,actual=f'{actual:08x}',qsim=f'{expected:08x}')
  if kind=='float':
   rec['bit_distance']=abs(actual-expected);rec['actual_float']=struct.unpack('<f',struct.pack('<I',actual))[0];rec['qsim_float']=struct.unpack('<f',struct.pack('<I',expected))[0]
  diffs.append(rec)
 total=int(re.search(r'QSIM_DIFFERENCE_WORDS ([0-9a-f]{8})',text)[1],16);assert len(diffs)==total
 return rows,diffs

def main():
 qrows=list(csv.DictReader((OUT/'capture-f5-state-v2/vertex-state.csv').open()))
 qsum={k:sum(int(x[k])for x in qrows)for k in ['count','insns','est_cycles','dependency','fdiv','icache','dcache','branch']}
 result={'scope':'Actual retained A CPU/fabric/SDRAM RTL executing original F5 gfx_sp_vertex2656bytes plus original helper/text/rodata.200captured calls,2586vertices. Isolated cache preparations; not full-game FPS or Pocket memory-arbitration history.','strict_qsim_state_equivalence_passed':False,'actual_A_repeatability_passed':True,'qsim_totals':qsum,'runs':{},'limits':['Each timed call matches all6604post-state bytes against its preceding actual-A reference, with no numerical tolerance. Across outer cache-mode groups only32-bit FNV hashes are compared, not full byte images.','Qsim uses IEEE fused arithmetic while this retained A implements reduced-accuracy FMA. Numeric differences are preserved below and are NOT waived into a qsim correctness pass.','Qsim model samples retain game cache/predictor history; actual fixture restores6.6KiB state and input, altering cache residency. Counts therefore characterize this isolated workload, not absolute whole-game speed.','Timer and marker counts contain common wrapper/timer overhead; per-job retirement deltas reveal its magnitude. UART boundary occasionally changes a poll instruction. Freeze and dispatch-hazard counters overlap and must not be added.',
'Scanout here is heavy synthetic stress (period700 CPU cycles,128-halfword burst,480active lines per525); it is not Pocket320RGB565 scanout cadence or concurrent GPU rendering.','Captured pre-state is from unchanged F5 qsim, not a device RAM dump; this benchmark does not model any propagation of hardware rounding differences through earlier game frames.']}
 for mode in ['quiet','scanout']:
  rows,diffs=parse(mode);summary={}
  for cold in [False,True]:
   r=[x for x in rows if x['cold_prepared']==cold];keys=['retired','marker_cycles','timer_cycles','execute_freeze','dispatch_hazards','fdiv_freeze','intdiv_freeze'];s={k:sum(x[k]for x in r)for k in keys}
   s['cycles_per_vertex']=s['timer_cycles']/qsum['count'];s['retired_per_vertex']=s['retired']/qsum['count'];s['timer_vs_model_percent']=100*(s['timer_cycles']/qsum['est_cycles']-1)
   s['retirement_minus_qsim_per_job']=dict(collections.Counter(x['retired']-int(qrows[x['job']]['insns'])for x in r))
   s['fdiv_freeze_percent_of_marker_cycles']=100*s['fdiv_freeze']/s['marker_cycles'];s['execute_freeze_percent_of_marker_cycles']=100*s['execute_freeze']/s['marker_cycles'];s['dispatch_hazard_percent_of_marker_cycles']=100*s['dispatch_hazards']/s['marker_cycles']
   summary['cold_prepared' if cold else 'restore_prepared']=s
  result['runs'][mode]={'summary':summary,'rows':rows,'qsim_difference_words':len(diffs),'qsim_difference_jobs':len({x['job']for x in diffs}),'qsim_difference_owners':dict(collections.Counter(x['owner']for x in diffs)),'largest_float_bit_distance':max((x.get('bit_distance',0)for x in diffs),default=0),'qsim_differences':diffs}
 assert result['runs']['quiet']['qsim_differences']==result['runs']['scanout']['qsim_differences']
 result['first_difference']=result['runs']['quiet']['qsim_differences'][0]
 result['evidence_sha256']={str(p.relative_to(ROOT)):sha(p)for p in [OUT/'host-manifest.json',OUT/'firmware-manifest.json',OUT/'firmware-reference-manifest.json',OUT/'quiet.log',OUT/'quiet-reference.log',OUT/'scanout-reference.log',OUT/'capture-proof-v2.json',Path(__file__)]}
 (OUT/'results.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:{'summary':v['summary'],'qsim_difference_words':v['qsim_difference_words'],'qsim_difference_owners':v['qsim_difference_owners'],'largest_float_bit_distance':v['largest_float_bit_distance']}for k,v in result['runs'].items()},indent=2))
if __name__=='__main__':main()
