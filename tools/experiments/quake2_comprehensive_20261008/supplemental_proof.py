#!/usr/bin/env python3
"""Reuse pinned passive observer; bind supplemental complete commands/pixels."""
import argparse, hashlib, json, os, struct, subprocess
from pathlib import Path
import supplemental as s
import clear_trace as ct
import frame_pixels_v2 as fp
O=s.OUT/'observer-v2'
BIN_SHA='a458b651273b7bd10d8351b1689c2d60971cd170f06e4b5c26ea5d41bb52fcc2'
PREP_SHA='3b7d14d4fb84c1ef16cba0546638e8739379c517e427b20f34775b84e2d876a6'
def verify():
 s.verify(); assert s.sha(fp.O/'prepared.json')==PREP_SHA; assert s.sha(fp.O/'qsim/qsim')==BIN_SHA
 p=json.loads((fp.O/'prepared.json').read_text())
 for name,h in p['source_sha256'].items():assert s.sha(fp.O/'qsim'/name)==h
 return p

def run(label):
 verify()
 for w in ('sound_demo','live_demo1'):
  normal=s.OUT/'runs'/w/label; report=json.loads((s.OUT/(w+'-'+label+'-result.json')).read_text());assert report['valid']
  dest=O/w/label;dest.parent.mkdir(parents=True,exist_ok=True);assert not dest.exists()
  commands=Path(str(dest)+'.commands');pixels=Path(str(dest)+'.pixels');assert not commands.exists() and not pixels.exists()
  command=list(report['checks'][-1]['command']);command[0]=str(fp.O/'qsim/qsim');command[command.index('--out')+1]=str(dest)
  env={k:v for k,v in os.environ.items() if not k.startswith(('QSIM_','SM64_'))}
  env['QSIM_COMMAND_TRACE']=str(commands);env['QSIM_FRAME_PIXELS']=str(pixels)
  with Path(str(dest)+'.stdout.log').open('w') as out,Path(str(dest)+'.stderr.log').open('w') as err:
   subprocess.run(command,cwd=s.OUT/'inputs'/w,env=env,stdout=out,stderr=err,timeout=90,check=True)
  assert (dest/'frames.csv').read_bytes()==(normal/'frames.csv').read_bytes()
  a=json.loads((normal/'summary.json').read_text());b=json.loads((dest/'summary.json').read_text())
  for x in (a,b):
   for k in ('wall_seconds','guest_mips'):del x[k]
   x['env'].pop('gpuvec_captured')
  assert a==b,[k for k in a if a[k]!=b.get(k)]
  for name in ('profile.tsv','profile_calls.tsv','console.log'):
   assert (normal/name).read_bytes()==(dest/name).read_bytes(),name
  s.write(Path(str(dest)+'-result.json'),dict(passed=True,command=command,elf_sha256=report['elf_sha256'],observer_binary_sha256=BIN_SHA,observer_prepared_sha256=PREP_SHA,normal_result_sha256=s.sha(s.OUT/(w+'-'+label+'-result.json')),normal_frame_csv_sha256=s.sha(normal/'frames.csv'),every_frame_csv_column_exact=True,every_guest_summary_field_exact=True,profile_and_console_exact=True,excluded_summary_fields=['wall_seconds','guest_mips','env.gpuvec_captured'],commands_sha256=s.sha(commands),pixels_sha256=s.sha(pixels)))
  print(w,label,'observer exact original model behavior',flush=True)
 verify()

def fnv(data):
 h=0xcbf29ce484222325
 for x in data:h=((h^x)*0x100000001b3)&0xffffffffffffffff
 return f'{h:016x}'
def compare(label):
 verify(); results=[];evidence=[Path(__file__),Path(s.__file__),Path(ct.__file__),Path(fp.__file__),fp.O/'prepared.json',fp.O/'source.patch',s.OUT/'prepared.json']
 for w in ('sound_demo','live_demo1'):
  cc={};pp={};rr={}
  for name in ('baseline',label):
   dest=O/w/name;report=Path(str(dest)+'-result.json');r=json.loads(report.read_text());assert r['passed']
   commands=Path(str(dest)+'.commands');pixels=Path(str(dest)+'.pixels')
   assert s.sha(commands)==r['commands_sha256'] and s.sha(pixels)==r['pixels_sha256']
   cc[name]=ct.trace(commands);pp[name]=fp.frames(pixels);rr[name]=s.csvrows(dest/'frames.csv');assert len(rr[name])==120
   grouped={}
   for frame,cmd in cc[name]:grouped.setdefault(frame,bytearray()).extend(struct.pack('<'+'I'*len(cmd),*cmd))
   assert len(pp[name])==120
   for row in rr[name]:
    frame=int(row['frame']);data=grouped.get(frame,b'')
    assert len(data)//4==int(row['gpu_cmd_words']) and fnv(data)==row['hash_cmd']
    header,pix=pp[name][frame];assert header==(frame,int(row['buffer']),320,240,640,3,640)
    legacy=b''.join(pix[y*640:y*640+320] for y in range(240));assert fnv(legacy)==row['hash_px']
   evidence += [report,commands,pixels,dest/'frames.csv',dest/'summary.json',s.OUT/(w+'-'+name+'-result.json')]
  removed=ct.prove(cc['baseline'],cc[label]);assert removed
  assert pp['baseline']==pp[label]
  for k in ('frame','host_framecount','buffer','hash_px','hash_pal','mem_hazards'):
   assert [x[k] for x in rr['baseline']]==[x[k] for x in rr[label]],k
  raw=json.loads((s.OUT/(label+'-vs-baseline.json')).read_text());metric=next(x for x in raw if x['workload']==w)
  a=json.loads((s.OUT/(w+'-baseline-result.json')).read_text());b=json.loads((s.OUT/(w+'-'+label+'-result.json')).read_text())
  assert a['steady_sound_services']==b['steady_sound_services']
  calls=lambda r:{k:{m:v[m] for m in ('calls','tailcalls')} for k,v in r['steady_selected_functions'].items() if v['calls'] or v['tailcalls']}
  assert calls(a)==calls(b)
  # Rightmost byte is outside legacy half-row coverage: negative control.
  _,pix=pp['baseline'][119];mut=bytearray(pix);mut[639]^=1
  assert pix!=mut and pix[:320]==mut[:320] and hashlib.sha256(pix).digest()!=hashlib.sha256(mut).digest()
  results.append(dict(workload=w,baseline_elf_sha256=a['elf_sha256'],candidate_elf_sha256=b['elf_sha256'],frames=120,steady_frames=100,full_visible_rgb565_bytes_exact=True,visible_bytes_per_frame=153600,complete_command_proof=True,removed_identical_color_clears=len(removed),all_other_commands_and_frame_assignment_exact=True,depth_clears_retained=True,original_qsim_frame_count_hash_bindings=120,observer_does_not_change_guest_or_modeled_work=True,sound_service_counts_exact=True,selected_AI_physics_sound_calls_exact=True,baseline_cycles_per_frame=a['modeled_cycles_per_frame'],candidate_cycles_per_frame=b['modeled_cycles_per_frame'],modeled_cycle_change_percent=metric['modeled_cycle_change_percent'],right_half_negative_control_rejected=True,limitations=s.LIMITS+['Full RGB565 equality covers CPU output; inherited qsim hash_px covers only the left 160 RGB565 columns. Actual GPU 3D rendering is outside both CPU framebuffer oracles.','qsim hash_z includes RGB565 clears through a fallback size heuristic; clear-only command proof retains all real depth clears.']))
 dest=O/(label+'-vs-baseline');dest.mkdir(exist_ok=False);s.write(dest/'result.json',dict(passed=True,results=results));evidence.append(dest/'result.json')
 s.write(dest/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(s.ROOT)):s.sha(p) for p in sorted(set(evidence))},observer_binary_sha256=BIN_SHA))
 print(json.dumps(dict(passed=True,output=str(dest),results=[{k:r[k] for k in ('workload','frames','removed_identical_color_clears','modeled_cycle_change_percent')} for r in results])))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['run','compare']);p.add_argument('--label',required=True);a=p.parse_args()
 if a.action=='run':run(a.label)
 else:compare(a.label)
