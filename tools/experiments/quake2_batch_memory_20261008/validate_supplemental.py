#!/usr/bin/env python3
"""P4 sound/live paired validation; reuse immutable P3 baseline captures."""
import argparse,hashlib,json,os,re,shutil,subprocess,sys
from pathlib import Path
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/quake2-batch-memory-20261008';O=P/'supplemental-v1'
T3=R/'tools/experiments/quake2_comprehensive_20261008';sys.path.insert(0,str(T3))
import supplemental as old
import supplemental_proof as proof
import frame_pixels_v2 as fp
BASE='40c9b219592e0a2781988492afbe50aa6d05e86404b801ed8f9274eb946aa982'
WORKLOADS=('sound_demo','live_demo1');FIELDS=('frame','host_framecount','buffer','gpu_cmd_words','hash_cmd','hash_px','hash_z','hash_pal','mem_hazards')
sha=old.sha;put=old.write

def verify_parent():
 proof.verify();assert sha(P/'work/base/.obj/quake2/app.elf')==BASE
 manifest=old.OUT/'observer-v2/combined6-vs-baseline/manifest.json'
 j=json.loads(manifest.read_text());assert j['passed']
 for rel,h in j['evidence_sha256'].items():assert sha(R/rel)==h,rel
 return manifest

def prepare():
 manifest=verify_parent();O.mkdir(exist_ok=False)
 pins=[manifest,old.OUT/'prepared.json',Path(old.__file__),Path(proof.__file__),Path(fp.__file__),fp.O/'prepared.json',fp.O/'qsim/qsim',old.OUT/'harness/qsim',P/'before.json']
 for w in WORKLOADS:
  report=old.OUT/(w+'-combined6-result.json');j=json.loads(report.read_text());assert j['valid'] and j['elf_sha256']==BASE
  pins+=[report,old.OUT/'observer-v2'/w/'combined6-result.json',old.OUT/'observer-v2'/w/'combined6.commands',old.OUT/'observer-v2'/w/'combined6.pixels']
  for suffix in ('-startup',''):
   pins.extend(x for x in (old.OUT/'runs'/w/('combined6'+suffix)).iterdir() if x.is_file())
  pins.extend(x for x in (old.OUT/'inputs'/w).rglob('*') if x.is_file())
 put(O/'prepared.json',dict(baseline_elf_sha256=BASE,baseline_label='combined6',baseline_source='P3 supplemental-v1 immutable normal/observer runs',recipe_sha256=sha(__file__),inputs_sha256={str(x.relative_to(R)):sha(x) for x in sorted(set(pins))},limitations=old.LIMITS+['Original hash_px covers only left160 RGB565 columns; passive observer compares all320 columns.']))
 print('prepared',O,flush=True)

def verify():
 j=json.loads((O/'prepared.json').read_text());assert sha(__file__)==j['recipe_sha256']
 for rel,h in j['inputs_sha256'].items():assert sha(R/rel)==h,rel
 return j

def flags(path):
 return [x for x in path.read_text().splitlines() if re.search(r'warning|fatal|error|unimplemented|unhandled',x,re.I)]

def run(label,elf):
 prep=verify();assert re.fullmatch('[A-Za-z0-9_-]+',label);source=elf.resolve();frozen=O/(label+'.elf');assert not frozen.exists();shutil.copy2(source,frozen)
 env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_'))};results=[];evidence=[Path(__file__),O/'prepared.json',frozen]
 for w in WORKLOADS:
  original=json.loads((old.OUT/(w+'-combined6-result.json')).read_text());checks=[]
  for n,suffix in enumerate(('-startup','')):
   dest=O/'runs'/w/(label+suffix);dest.parent.mkdir(parents=True,exist_ok=True);assert not dest.exists()
   command=list(original['checks'][n]['command']);command[1]=str(frozen);command[command.index('--out')+1]=str(dest);command[command.index('--label')+1]=w+'-'+label+suffix
   with Path(str(dest)+'.stdout.log').open('w') as out,Path(str(dest)+'.stderr.log').open('w') as err:
    subprocess.run(command,cwd=old.OUT/'inputs'/w,env=env,stdout=out,stderr=err,timeout=90,check=True)
   summary=json.loads((dest/'summary.json').read_text());console=(dest/'console.log').read_text();frames=20 if suffix else 120
   assert summary['elf_sha256']==sha(frozen) and summary['frames']==frames and summary['stop_reason']=='frame limit'
   assert summary['env']['unmapped_mmio']==summary['gpu_sync']['ring_overflows']==0 and 'GEOMETRY=0' in console
   checks.append(dict(command=command,frames=frames,valid=True,stderr_flags=flags(Path(str(dest)+'.stderr.log')),profile=old.profile(dest)))
   evidence += [dest/'summary.json',dest/'frames.csv',dest/'profile.tsv',dest/'profile_calls.tsv',dest/'console.log',Path(str(dest)+'.stderr.log')]
  normal=O/'runs'/w/label;observer=O/'observer'/w/label;observer.parent.mkdir(parents=True,exist_ok=True)
  commands=Path(str(observer)+'.commands');pixels=Path(str(observer)+'.pixels')
  command=list(checks[-1]['command']);command[0]=str(fp.O/'qsim/qsim');command[command.index('--out')+1]=str(observer)
  obsenv=dict(env,QSIM_COMMAND_TRACE=str(commands),QSIM_FRAME_PIXELS=str(pixels))
  with Path(str(observer)+'.stdout.log').open('w') as out,Path(str(observer)+'.stderr.log').open('w') as err:
   subprocess.run(command,cwd=old.OUT/'inputs'/w,env=obsenv,stdout=out,stderr=err,timeout=90,check=True)
  for name in ('frames.csv','profile.tsv','profile_calls.tsv','console.log'):assert (normal/name).read_bytes()==(observer/name).read_bytes(),name
  a=json.loads((normal/'summary.json').read_text());b=json.loads((observer/'summary.json').read_text())
  for value in (a,b):
   del value['wall_seconds'],value['guest_mips'];value['env'].pop('gpuvec_captured')
  assert a==b
  rows=old.csvrows(normal/'frames.csv');base_rows=old.csvrows(old.OUT/'runs'/w/'combined6'/'frames.csv');assert len(rows)==len(base_rows)==120
  for key in FIELDS:assert [x[key] for x in rows]==[x[key] for x in base_rows],(w,key)
  assert commands.read_bytes()==(old.OUT/'observer-v2'/w/'combined6.commands').read_bytes(),(w,'complete commands')
  assert pixels.read_bytes()==(old.OUT/'observer-v2'/w/'combined6.pixels').read_bytes(),(w,'full RGB565')
  capture=fp.frames(pixels);assert len(capture)==120
  for row in rows:
   index=int(row['frame']);header,data=capture[index]
   assert header==(index,int(row['buffer']),320,240,640,3,640) and len(data)==153600
  # This byte is outside the original left-half hash, but inside our complete comparison.
  _,data=capture[119];mut=bytearray(data);mut[639]^=1;assert data!=mut and data[:320]==mut[:320]
  services={k:v-checks[0]['profile']['sound_services'].get(k,0) for k,v in checks[1]['profile']['sound_services'].items()}
  funcs={k:{m:v-checks[0]['profile']['selected_functions'].get(k,{}).get(m,0) for m,v in vals.items()} for k,vals in checks[1]['profile']['selected_functions'].items()}
  select=lambda rows:{k:{m:v[m] for m in ('calls','tailcalls')} for k,v in rows.items() if v['calls'] or v['tailcalls']}
  assert services==original['steady_sound_services'] and select(funcs)==select(original['steady_selected_functions'])
  cycles=sum(int(x['est_cycles']) for x in rows[20:120])/100
  result=dict(passed=True,workload=w,label=label,baseline_elf_sha256=BASE,candidate_elf_sha256=sha(frozen),source_elf=str(source),source_unchanged=sha(source)==sha(frozen),steady_frames=100,normal_checks=checks,observer_command=command,observer_binary_sha256=sha(fp.O/'qsim/qsim'),observer_all_CSV_and_guest_summary_fields_exact=True,observer_profile_and_console_exact=True,observer_summary_exclusions=['wall_seconds','guest_mips','env.gpuvec_captured'],frames=120,complete_commands_bytes_exact=True,full_visible_RGB565_bytes_exact=True,visible_bytes_per_frame=153600,right_half_negative_control=True,all_compared_frame_fields=list(FIELDS),steady_sound_services=services,selected_AI_physics_sound_calls_exact=True,baseline_cycles_per_frame=original['modeled_cycles_per_frame'],candidate_cycles_per_frame=cycles,modeled_cycle_change_percent=100*(cycles/original['modeled_cycles_per_frame']-1),limitations=prep['limitations'])
  path=O/(w+'-'+label+'-result.json');put(path,result);results.append({k:result[k] for k in ('workload','frames','baseline_cycles_per_frame','candidate_cycles_per_frame','modeled_cycle_change_percent','complete_commands_bytes_exact','full_visible_RGB565_bytes_exact')})
  evidence += [path,commands,pixels,observer/'frames.csv',observer/'summary.json',observer/'profile.tsv',observer/'profile_calls.tsv',observer/'console.log',Path(str(observer)+'.stderr.log')]
  print(json.dumps(results[-1]),flush=True)
 verify();dest=O/(label+'-validation');dest.mkdir(exist_ok=False);put(dest/'result.json',dict(passed=True,label=label,candidate_elf_sha256=sha(frozen),baseline_elf_sha256=BASE,results=results,limits=prep['limitations']));evidence.append(dest/'result.json')
 put(dest/'manifest.json',dict(passed=True,evidence_sha256={str(x.relative_to(R)):sha(x) for x in sorted(set(evidence))}));print('sealed',dest,'manifest',sha(dest/'manifest.json'),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','run']);p.add_argument('--label');p.add_argument('--elf',type=Path);a=p.parse_args()
 if a.action=='prepare':prepare()
 else:run(a.label,a.elf)
