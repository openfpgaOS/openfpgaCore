#!/usr/bin/env python3
"""Pinned private extra Quake2 workloads; original qsim, unpatched normal ELFs."""
import argparse, collections, csv, hashlib, json, os, re, shutil, struct, subprocess
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
ORIG = ROOT/'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
OUT = ROOT/'docs/measurements/quake2-comprehensive-20261008/supplemental-v1'
BASE_SHA = '7bcb432f6edb78a0d7fc85db3fd0b7bb79866382df001ad9caeb9a02c7cacdd6'
LIMITS = [
 'Inherited scalar CPU/cache estimates are not selected Vexii cycles or Pocket FPS.',
 'GPU records 3D commands, does not rasterize 3D; framebuffer hashes cover CPU output only.',
 'No real audio samples, mixer interrupt/kernel cost, physical memory/audio/GPU contention, or real input.',
 'Mixer handle allocation is synthetic; handle-active always returns 1, position returns 0, setters are no-ops; natural sample endings are not modeled.',
 'Clock returned to guest is fixed 30 Hz by presented flips; virtual_clock.final_us/advances do not reflect that override.',
 'Live workload uses normal fixedtime=33334 microseconds and busywait=0 to make bounded deterministic client/server progress with this clock; no guest code edits.',
 'Live workload starts demo1 and holds +forward via console command; it is a short deterministic spawn-area workload, not representative combat coverage.',
 'Texture upload is functionally instantaneous; no GPU vector dumps captured.']
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v): p.write_text(json.dumps(v,indent=2)+'\n')
def tab(p):
 with p.open() as f: return list(csv.DictReader(f,delimiter='\t'))
def csvrows(p):
 with p.open() as f: return list(csv.DictReader(f))
def prepare():
 assert sha(ORIG/'pass2_trio.elf')==BASE_SHA
 old=json.loads((ORIG/'prepared.json').read_text())
 assert sha(ORIG/'qsim/qsim')==old['qsim_sha256']
 for rel,h in old['inputs'].items(): assert sha(ORIG/rel)==h,rel
 OUT.mkdir(exist_ok=False)
 (OUT/'harness').mkdir(); (OUT/'elf').mkdir()
 shutil.copy2(ORIG/'qsim/qsim',OUT/'harness/qsim')
 shutil.copy2(ORIG/'pass2_trio.elf',OUT/'elf/baseline.elf')
 common='set ofgl_bisect 0\nset cl_showfps 0\nset logfile 0\n'
 configs={
  'sound_demo':common+'set timedemo 1\nset s_initsound 1\nalias d1 "demomap q2demo1.dm2"\n',
  'live_demo1':common+'set timedemo 0\nset busywait 0\nset fixedtime 33334\nset s_initsound 1\nalias d1 "map demo1; +forward"\n'}
 for name,cfg in configs.items():
  d=OUT/'inputs'/name; (d/'data').mkdir(parents=True)
  for f in ('quake2.app','quake2.ini'): shutil.copy2(ORIG/f,d/f)
  shutil.copy2(ORIG/'data/pak0.pak',d/'data/pak0.pak')
  (d/'data/config.cfg').write_text(cfg)
 pak=(ORIG/'data/pak0.pak').read_bytes(); magic,di,dl=struct.unpack_from('<4sii',pak); assert magic==b'PACK'
 maps=[]
 for n in range(di,di+dl,64):
  name,off,size=struct.unpack_from('<56sii',pak,n); name=name.split(b'\0')[0].decode()
  if name.startswith('maps/') and name.endswith('.bsp'):
   ident,version,eo,es=struct.unpack_from('<4siii',pak,off); assert ident==b'IBSP' and version==38
   ent=pak[off+eo:off+eo+es].decode(errors='replace'); counts=collections.Counter(re.findall(r'"classname"\s+"([^"]+)"',ent))
   maps.append(dict(name=name,bytes=size,entities=sum(counts.values()),monsters=sum(v for k,v in counts.items() if k.startswith('monster_')),classes=dict(sorted(counts.items()))))
 write(OUT/'maps.json',maps)
 pins={str(p.relative_to(OUT)):sha(p) for p in sorted(OUT.rglob('*')) if p.is_file()}
 write(OUT/'prepared.json',dict(recipe=str(Path(__file__).resolve()),recipe_sha256=sha(__file__),inputs=pins,original_prepared_sha256=sha(ORIG/'prepared.json'),original_qsim_sha256=sha(ORIG/'qsim/qsim'),original_pak_sha256=sha(ORIG/'data/pak0.pak'),baseline_sha256=BASE_SHA,frames=120,profile_startup_frames=20,steady_frame_indices='20..119',max_insns=4_000_000_000,workloads=list(configs),limitations=LIMITS))
 print(json.dumps(dict(prepared=str(OUT),maps=[dict(name=m['name'],monsters=m['monsters']) for m in maps])))
def verify():
 p=json.loads((OUT/'prepared.json').read_text()); assert sha(__file__)==p['recipe_sha256']
 for rel,h in p['inputs'].items(): assert sha(OUT/rel)==h,rel
 assert sha(ORIG/'qsim/qsim')==p['original_qsim_sha256']
 assert sha(ORIG/'prepared.json')==p['original_prepared_sha256']
 return p

def profile(dest):
 rows=tab(dest/'profile.tsv'); calls=tab(dest/'profile_calls.tsv')
 services=collections.Counter()
 for row in calls:
  if row['kind']=='service': services[row['callee']]+=int(row['calls'])
 selected={r['function']:{k:int(r[k]) for k in ('calls','tailcalls','self_insns','self_cycles')} for r in rows if re.search(r'(^G_RunFrame$|^SV_Frame|^SV_RunGameFrame|^ClientThink|^Pmove|^PM_|^monster_think|^M_MoveFrame|^ai_|^S_Update|^S_Spatialize|^S_LoadSound|^S_StartSound|^SDL_|^SNDDMA|^snd_)',r['function'])}
 return dict(services=dict(services),sound_services={k:v for k,v in services.items() if 'mixer_' in k or 'audio_' in k},selected_functions=selected)
def run(label,elf,workload=None):
 p=verify(); assert re.fullmatch('[a-zA-Z0-9_-]+',label)
 source=elf.resolve(); frozen=OUT/'elf'/(label+'.elf')
 if frozen.exists(): assert sha(frozen)==sha(source)
 else: shutil.copy2(source,frozen)
 env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_'))}
 workloads=[workload] if workload else p['workloads']
 for name in workloads:
  assert name in p['workloads']; work=OUT/'inputs'/name; checks=[]
  for suffix,frames in (('-startup',20),('',120)):
   dest=OUT/'runs'/name/(label+suffix); dest.parent.mkdir(parents=True,exist_ok=True); assert not dest.exists()
   cmd=[str(OUT/'harness/qsim'),str(frozen),'--out',str(dest),'--app',str(work/'quake2.app'),'--data-dir',str(work/'data'),'--profile','--frames',str(frames),'--max-insns','4e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp','--label',name+'-'+label+suffix]
   with Path(str(dest)+'.stdout.log').open('w') as out,Path(str(dest)+'.stderr.log').open('w') as err:
    result=subprocess.run(cmd,cwd=work,env=env,stdout=out,stderr=err,timeout=90)
   summ=json.loads((dest/'summary.json').read_text()) if (dest/'summary.json').exists() else {}
   console=(dest/'console.log').read_text() if (dest/'console.log').exists() else ''
   stderr=Path(str(dest)+'.stderr.log').read_text()
   flags=[line for line in stderr.splitlines() if re.search('warning|fatal|error|unimplemented|unhandled',line,re.I)]
   checked=dict(command=cmd,returncode=result.returncode,frames=summ.get('frames'),stop_reason=summ.get('stop_reason'),stderr_flags=flags,console_flags=[line for line in console.splitlines() if re.search('error:|ERROR:|unable to allocate|failed|unknown command',line,re.I)])
   checked['valid']=(result.returncode==0 and summ.get('frames')==frames and summ.get('stop_reason')=='frame limit' and summ.get('elf_sha256')==sha(frozen) and summ.get('env',{}).get('unmapped_mmio')==0 and summ.get('gpu_sync',{}).get('ring_overflows')==0 and 'GEOMETRY=0' in console)
   if (dest/'profile_calls.tsv').exists(): checked['profile']=profile(dest)
   checks.append(checked)
   if not checked['valid']: break
  r=dict(label=label,workload=name,source_elf=str(source),elf_sha256=sha(frozen),source_unchanged=sha(source)==sha(frozen),prepared_sha256=sha(OUT/'prepared.json'),checks=checks,valid=len(checks)==2 and all(c['valid'] for c in checks),limitations=LIMITS)
  if r['valid']:
   dest=OUT/'runs'/name/label; rows=csvrows(dest/'frames.csv')[20:120]
   r.update(steady_frames=len(rows),modeled_cycles_per_frame=sum(int(x['est_cycles']) for x in rows)/len(rows),instructions_per_frame=sum(int(x['insns']) for x in rows)/len(rows),command_words_per_frame=sum(int(x['gpu_cmd_words']) for x in rows)/len(rows))
   r['steady_sound_services']={k:v-checks[0]['profile']['sound_services'].get(k,0) for k,v in checks[1]['profile']['sound_services'].items()}
   r['steady_selected_functions']={k:{metric:v-checks[0]['profile']['selected_functions'].get(k,{}).get(metric,0) for metric,v in vals.items()} for k,vals in checks[1]['profile']['selected_functions'].items()}
  write(OUT/(name+'-'+label+'-result.json'),r)
  print(json.dumps({k:r[k] for k in ('workload','label','valid','elf_sha256')}|{k:r[k] for k in ('modeled_cycles_per_frame','steady_sound_services') if k in r}),flush=True)
 verify()
def compare(a,b):
 verify(); results=[]
 for name in ('sound_demo','live_demo1'):
  ra=json.loads((OUT/(name+'-'+a+'-result.json')).read_text()); rb=json.loads((OUT/(name+'-'+b+'-result.json')).read_text())
  assert ra['valid'] and rb['valid']
  aa=csvrows(OUT/'runs'/name/a/'frames.csv'); bb=csvrows(OUT/'runs'/name/b/'frames.csv'); assert len(aa)==len(bb)==120
  keys=('host_framecount','buffer','gpu_cmd_words','hash_cmd','hash_px','hash_z','hash_pal','mem_hazards')
  mismatches=[dict(frame=i,key=k,a=x[k],b=y[k]) for i,(x,y) in enumerate(zip(aa,bb)) for k in keys if x[k]!=y[k]]
  r=dict(workload=name,baseline=a,candidate=b,baseline_elf_sha256=ra['elf_sha256'],candidate_elf_sha256=rb['elf_sha256'],modeled_cycles_per_frame_baseline=ra['modeled_cycles_per_frame'],modeled_cycles_per_frame_candidate=rb['modeled_cycles_per_frame'],modeled_cycle_change_percent=100*(rb['modeled_cycles_per_frame']/ra['modeled_cycles_per_frame']-1),exact_command_and_cpu_output_all_120_frames=not mismatches,mismatches=mismatches,steady_sound_services_equal=ra['steady_sound_services']==rb['steady_sound_services'],limitations=LIMITS)
  results.append(r)
 write(OUT/(b+'-vs-'+a+'.json'),results)
 print(json.dumps(results,indent=2))
if __name__=='__main__':
 ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['prepare','run','compare']); ap.add_argument('--label');ap.add_argument('--elf',type=Path);ap.add_argument('--workload');ap.add_argument('--baseline',default='baseline');ap.add_argument('--candidate');a=ap.parse_args()
 if a.action=='prepare': prepare()
 elif a.action=='run': run(a.label,a.elf,a.workload)
 else: compare(a.baseline,a.candidate)
