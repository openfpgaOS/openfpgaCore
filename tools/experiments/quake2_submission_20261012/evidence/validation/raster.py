#!/usr/bin/env python3
"""Four exact command-composed color/depth RTL samples; no coupled CPU timing."""
from pathlib import Path
import argparse,concurrent.futures,hashlib,importlib.util,json,os,shutil,struct,subprocess
R=Path(__file__).resolve().parent; D=R/'raster'; OLD=Path('/tmp/quake2-gpu-20261011/rtl-tests'); FRAMES=[20,80,180,300]
def sha(p):
 with Path(p).open('rb')as f:return hashlib.file_digest(f,'sha256').hexdigest()
def put(p,o):p.write_text(json.dumps(o,indent=2)+'\n')
def module(name,p):
 s=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def prepare():
 D.mkdir(exist_ok=False);(D/'frozen').mkdir()
 for source,target in [(OLD/'capture-qsim/qsim',D/'frozen/capture-qsim'),(OLD/'timing-replay/obj/Vtb_gpu',D/'frozen/Vtb_gpu'),(OLD/'capture_replay_timing.py',D/'frozen/capture_helpers.py'),(Path('/home/alberto/Repos/Quake2/docs/measurements/quake2-gpu-next-20261011/gpu/replay_frame_scope.py'),D/'frozen/scope_helpers.py')]:shutil.copy2(source,target)
 shutil.copytree(OLD/'timing-replay/frozen',D/'frozen/rtl')
 shutil.copy2(OLD/'timing-replay/command.json',D/'frozen/original-build-command.json')
 shutil.copy2(OLD/'timing-replay/source-pins.json',D/'frozen/original-source-pins.json')
 put(D/'prepared.json',dict(parent=str(OLD),pins={str(p.relative_to(D)):sha(p)for p in sorted((D/'frozen').rglob('*'))if p.is_file()},limits=[__doc__,'GPU maximal-rate producer, no competing CPU/audio/scanout traffic; not physical timing or FPS.','Resident textures, cold reset cache; CPU HUD omitted from RTL image and independently checked in all-frame functional capture.']))
def verify():
 for n,h in json.loads((D/'prepared.json').read_text())['pins'].items():assert sha(D/n)==h,n

def capture(label,elf):
 verify();d=D/'captures'/label;d.mkdir(parents=True,exist_ok=False);shutil.copy2(elf,d/'app.elf')
 src=R/'inputs/main';env={k:v for k,v in os.environ.items()if not k.startswith(('SM64_','QSIM_','Q2_GPU_','Q2_TEST_'))};extra={'QSIM_COMMAND_TRACE':str(d/'commands.bin'),'QSIM_FRAME_PIXELS':str(d/'pixels.bin'),'SM64_GEOMETRY_CAP':'1','Q2_TEST_GEOMETRY_WORKER_CAP':'1'};env.update(extra)
 cmd=[str(D/'frozen/capture-qsim'),str(d/'app.elf'),'--out',str(d/'out'),'--app',str(src/'quake2.app'),'--data-dir',str(src/'data'),'--frames','302','--max-insns','4e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp','--label',label,'--gpuvec',','.join(map(str,FRAMES))]
 put(d/'command.json',dict(argv=cmd,cwd=str(src),environment=extra,elf_sha256=sha(d/'app.elf')))
 with(d/'run.log').open('w')as log:subprocess.run(cmd,cwd=src,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
 s=json.loads((d/'out/summary.json').read_text());assert s['frames']==302 and s['env']['unmapped_mmio']==s['gpu_sync']['ring_overflows']==0
 put(d/'result.json',dict(passed=True,elf_sha256=sha(d/'app.elf'),command_sha256=sha(d/'commands.bin'),limits=__doc__))
 print('captured',label,flush=True)

def render(label):
 verify();d=D/'replays'/label;d.mkdir(parents=True,exist_ok=False);cap=D/'captures'/label
 raw=module('raw',D/'frozen/capture_helpers.py');scope=module('scope',D/'frozen/scope_helpers.py')
 expected={f:[]for f in FRAMES};b=(cap/'commands.bin').read_bytes();pos=0
 while pos<len(b):
  frame,h=struct.unpack_from('<QI',b,pos);n=(h&8191)+1
  if frame in expected:expected[frame].append(list(struct.unpack_from('<'+'I'*n,b,pos+8)))
  pos+=8+n*4
 assert pos==len(b)
 def one(f):
  base=d/f'frame_{f:05}';unscoped=base.with_suffix('.envelope.vec');stride,step=raw.vector(cap/'out/gpuvec'/f'frame_{f:05}.vec',unscoped)
  removed=scope.filter_vector(unscoped,base.with_suffix('.vec'),expected[f]);cmd=[str(D/'frozen/Vtb_gpu'),'+fast_tex=1','+tagged_tex=1','+memory_traffic=0',str(base.with_suffix('.vec')),str(base.with_suffix('.bin'))]
  with base.with_suffix('.log').open('w')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=360)
  b=base.with_suffix('.bin').read_bytes();assert len(b)==153612+stride*240
  color=b[:153600];z=b[153612:];depth=b''.join(z[y*stride+x*step:y*stride+x*step+2]for y in range(240)for x in range(320));assert len(depth)==153600
  base.with_suffix('.rgb565').write_bytes(color);base.with_suffix('.depth').write_bytes(depth)
  result=dict(passed=True,frame=f,argv=cmd,color_sha256=sha(base.with_suffix('.rgb565')),depth_sha256=sha(base.with_suffix('.depth')),vector_sha256=sha(base.with_suffix('.vec')),removed_next_frame_clear=removed,frame_tagged_commands_exact=True)
  put(base.with_suffix('.json'),result);print('rendered',label,f,flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as p:results=list(p.map(one,FRAMES))
 put(d/'result.json',dict(passed=True,frames=results,limits=__doc__))
def compare(a,b):
 results=[]
 for f in FRAMES:
  row=dict(frame=f)
  for ext in ('rgb565','depth'):
   aa=(D/'replays'/a/f'frame_{f:05}.{ext}').read_bytes();bb=(D/'replays'/b/f'frame_{f:05}.{ext}').read_bytes();assert len(aa)==len(bb)==153600;row[ext]=dict(exact=aa==bb,different_pixels=sum(aa[i:i+2]!=bb[i:i+2]for i in range(0,len(aa),2)))
  results.append(row)
 result=dict(passed=all(x[e]['exact']for x in results for e in ('rgb565','depth')),baseline=a,candidate=b,frames=results,limits=__doc__);put(D/(a+'-vs-'+b+'.json'),result);print(json.dumps(result,indent=2));assert result['passed']
if __name__=='__main__':
 p=argparse.ArgumentParser();s=p.add_subparsers(dest='action',required=True);s.add_parser('prepare');c=s.add_parser('capture');c.add_argument('label');c.add_argument('elf',type=Path);v=s.add_parser('render');v.add_argument('label');c=s.add_parser('compare');c.add_argument('baseline');c.add_argument('candidate');a=p.parse_args()
 if a.action=='prepare':prepare()
 elif a.action=='capture':capture(a.label,a.elf)
 elif a.action=='render':render(a.label)
 else:compare(a.baseline,a.candidate)
