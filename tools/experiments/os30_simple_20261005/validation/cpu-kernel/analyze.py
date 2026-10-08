#!/usr/bin/env python3
"""Record paired actual RTL component results, preserving scope limits."""
import sys,re,statistics
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from common import *
out=OUT/'cpu-kernel';result={'passed':True,'scope':'ActualA single33895e9a CPU/fabric/SDRAM model, actual extracted clipping/mask functions and299 captured triangle inputs. Component cycles; not whole-gameFPS. Visible submitted triangles underrepresent discarded/offscreen vertex classification.','runs':{},'summary':{}}
for mode in ['quiet','scanout']:
 log=(out/(mode+'.log')).read_text().split('=== UART BEGIN ===')[0]
 assert 'KERNEL PASS HAL init' in log and 'KERNEL FAIL' not in log
 pattern=r'RUN ([0-9a-f]{8}) ([0-9a-f]{8}) ([0-9a-f]{8}) @.*?HOST_KERNEL retired=(\d+) cycles=(\d+) freeze=(\d+) hazards=(\d+)\s*.*?TIMER ([0-9a-f]{8}) ([0-9a-f]{8})'
 rs=[]
 for v in re.findall(pattern,log,re.S):
  cold,order,variant=map(lambda s:int(s,16),v[:3]);retired,cycles,freeze,hazards=map(int,v[3:7]);timer,check=map(lambda s:int(s,16),v[7:])
  rs.append(dict(cold_prepared=bool(cold),order=order,variant=variant,retired_with_common_marker_overhead=retired,cycles_with_marker_overhead=cycles,execute_freeze_cycles=freeze,dispatch_hazard_cycles=hazards,timer_cycles=timer,hash=f'{check:08x}'))
 assert len(rs)==16 and len({r['hash'] for r in rs})==1,len(rs)
 result['runs'][mode]=rs;groups={}
 for cold in [False,True]:
  subsets={i:[r for r in rs if r['cold_prepared']==cold and r['variant']==i] for i in range(4)}
  assert all(len(s)==2 for s in subsets.values())
  base=statistics.mean(r['timer_cycles'] for r in subsets[0]);base_ret=statistics.mean(r['retired_with_common_marker_overhead'] for r in subsets[0])
  groups['cold_prepared' if cold else 'warm']={str(i):dict(timer_cycles_mean=statistics.mean(r['timer_cycles'] for r in rows),cycle_saving_percent=100*(1-statistics.mean(r['timer_cycles'] for r in rows)/base),retired_mean=statistics.mean(r['retired_with_common_marker_overhead'] for r in rows),retired_saving_percent=100*(1-statistics.mean(r['retired_with_common_marker_overhead'] for r in rows)/base_ret),execute_freeze_mean=statistics.mean(r['execute_freeze_cycles'] for r in rows),dispatch_hazard_mean=statistics.mean(r['dispatch_hazard_cycles'] for r in rows)) for i,rows in subsets.items()}
 result['summary'][mode]=groups
 result['runs'][mode+'_memory_scoreboard_clean']='WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in (out/(mode+'.log')).read_text();assert result['runs'][mode+'_memory_scoreboard_clean']
result['variants']={'0':'baseline','1':'clip-exact','2':'vertex-flags','3':'combined'}
result['correctness']='Before timing: independent flag comparison, exact original/candidate return/fan count, all32 meaningful payload bytes for every output vertex. All timed hashes6ffe99f6. Same input and actual SDK musl routines in each variant.'
result['measurement']='Two orders forward/reverse. Warm primes selected variant; cold preparation scans256KiB and fence.i invalidates I-cache. No predictor reset; order variation retained. UART-marked retired/freeze/hazard windows include equal common timer/marker overhead. Guest MMIO timer brackets batch only.'
result['limitations']=['Kernel wraps only classification and clipping; does not include surrounding transform code or compiler register allocation. In particular flag results cannot establish whole gfx_sp_vertex benefit.','Captured triangles exclude many transformed vertices rejected before submission, and do not exercise CLIP_NEAR in this path (observed OR0x3e). Separate32172-case host fixture covers all64 masks.','Initial runs hit the legacy1.5Mcycle no-SDRAM watchdog despite continued UART progress; initial-watchdog logs retained. Only that host watchdog was extended; no RTL changed.']
result['evidence_sha256']={str(p.relative_to(ROOT)):sha(p) for p in [out/'manifest.json',out/'quiet.log',out/'scanout.log',out/'initial-watchdog-manifest.json',out/'initial-watchdog-quiet.log',out/'initial-watchdog-scanout.log',Path(__file__)]}
(out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result['summary'],indent=2))
