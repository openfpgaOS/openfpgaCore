#!/usr/bin/env python3
"""Private Q2 sampled captures and exact selected phase10 GPU/worker replay.
qsim only emits commands and memory; it does not rasterize 3D.  The replay
uses real selected GPU/cache/controller RTL with a synthetic ring producer.
Cycles from this harness are not whole-device timing or hardware FPS.
"""
from pathlib import Path
import argparse,collections,concurrent.futures,hashlib,importlib.util,json,os,re,shutil,struct,subprocess
R=Path(__file__).resolve().parent
Q=R/'capture-qsim/qsim';M=R/'replay'
FRAMES=[20,80,180,300]
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def capture(label,elf,mode):
 d=R/'captures'/label;d.mkdir(parents=True,exist_ok=False)
 src=Path('/tmp/quake2-perf-20261011/bench/inputs/main')
 i=d/'input';shutil.copytree(src,i)
 e=d/'app.elf';shutil.copy2(elf,e)
 env={k:v for k,v in os.environ.items() if not k.startswith(('SM64_','QSIM_','Q2_TEST_'))}
 extra={'QSIM_COMMAND_TRACE':str(d/'commands.bin'),'QSIM_FRAME_PIXELS':str(d/'pixels.bin')}
 if mode in ('inline','worker'):extra['SM64_GEOMETRY_CAP']='1'
 if mode=='worker':extra['Q2_TEST_GEOMETRY_WORKER_CAP']='1'
 env.update(extra)
 cmd=[str(Q),str(e),'--out',str(d/'out'),'--app',str(i/'quake2.app'),'--data-dir',str(i/'data'),'--frames','302','--max-insns','4e9','--gpu-sched','eager','--mem','flat','--no-normalize-build-stamp','--label',label,'--gpuvec',','.join(map(str,FRAMES))]
 put(d/'command.json',dict(command=cmd,cwd=str(i),environment=extra,elf_sha256=sha(e),qsim_sha256=sha(Q),input_files={str(p.relative_to(i)):sha(p)for p in i.rglob('*')if p.is_file()}))
 with (d/'stdout.log').open('w') as out,(d/'stderr.log').open('w')as err:subprocess.run(cmd,cwd=i,env=env,stdout=out,stderr=err,timeout=360,check=True)
 summary=json.loads((d/'out/summary.json').read_text());assert summary['frames']==302
 assert summary['env']['unmapped_mmio']==summary['gpu_sync']['ring_overflows']==0
 counts=collections.Counter();data=(d/'commands.bin').read_bytes();pos=0
 while pos<len(data):
  f,h=struct.unpack_from('<QI',data,pos);pos+=8+4*((h&8191)+1);counts[f'0x{h>>24:02x}']+=1
 assert pos==len(data)
 put(d/'captured.json',dict(passed=True,mode=mode,elf_sha256=sha(e),qsim_sha256=sha(Q),command_counts=counts,raw_vectors={str(f):sha(d/'out/gpuvec'/f'frame_{f:05}.vec')for f in FRAMES},cpu_framebuffers_sha256=sha(d/'pixels.bin'),limits=__doc__))
 print('Captured',label,dict(counts),flush=True)
def vector(source,dest):
 data=source.read_bytes();pos=0;parts=[];removed=[];commands=[];zaddr=zstride=zstep=0;clears=[]
 while pos<len(data):
  start=pos;tag=data[pos:pos+4];pos+=4;keep=True
  if tag in (b'MEM1',b'TEX1'):
   a,n=struct.unpack_from('<II',data,pos);pos+=8+((n+3)&~3)
   if tag==b'MEM1' and a<0x300000:
    assert a+n<=0x300000;keep=False;removed.append(dict(address=a,length=n,sha256=hashlib.sha256(data[start:pos]).hexdigest()))
  elif tag==b'CMDS':
   n=struct.unpack_from('<I',data,pos)[0];pos+=4;words=struct.unpack_from('<'+'I'*n,data,pos);pos+=4*n;i=0
   while i<n:
    op=words[i]>>24;count=words[i]&8191;assert i+count+1<=n
    commands.append(list(words[i:i+count+1]))
    if op==0x4a and count==17:zaddr,zstride,zstep=words[i+12:i+15]
    if op==0x11:clears.append((words[i+1]&0x3ffffff,len(commands)-1))
    i+=count+1
  elif tag==b'TLUT':pos+=32768
  elif tag==b'PALB':pos+=4
  elif tag in (b'MMIO',b'DUMP'):pos+=8
  elif tag==b'RAND':pos+=12
  elif tag in (b'FLSH',b'FENC',b'HASH'):pass
  elif tag==b'END.':assert pos==len(data);break
  else:raise AssertionError((tag,pos))
  assert pos<=len(data)
  if keep:parts.append(data[start:pos])
 assert zaddr and zstride and zstep
 lastclear=[ci for a,ci in clears if a==(zaddr&0x3ffffff)][-1]
 draws=[i for i,c in enumerate(commands)if c[0]>>24 in (0x4e,0x54,0x5b)]
 assert draws and lastclear<min(draws), 'Different depth lifetime; do not hash a cleared Z buffer'
 assert any(c[0]>>24==0x02 for c in commands[max(draws)+1:])
 dest.write_bytes(b''.join(parts)+b'DUMP'+struct.pack('<II',zaddr,zstride*240)+b'END.')
 put(dest.with_suffix('.filter.json'),dict(raw_sha256=sha(source),filtered_sha256=sha(dest),removed_cpu_framebuffer_snapshots=removed,zaddr=zaddr,zstride=zstride,zstep=zstep,commands=commands,command_order_and_payload_unchanged=True,depth_dump_timing='end of rendered frame, before next frame clear',last_z_clear_command=lastclear))
 return zstride,zstep
def replay(label,model):
 d=R/'replays'/(label+'-'+model);d.mkdir(parents=True,exist_ok=True)
 exe=R/'timing-replay/obj/Vtb_gpu' if model=='timing-worker' else M/('obj/Vtb_gpu' if model=='worker' else 'baseline-Vtb_gpu')
 def one(f):
  stem=d/f'frame_{f:05}';source=R/'captures'/label/'out/gpuvec'/f'frame_{f:05}.vec'
  stride,step=vector(source,stem.with_suffix('.vec'))
  cmd=[str(exe),'+fast_tex=1','+tagged_tex=1','+memory_traffic=0',str(stem.with_suffix('.vec')),str(stem.with_suffix('.bin'))]
  with stem.with_suffix('.log').open('w')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=360,check=True)
  b=stem.with_suffix('.bin').read_bytes();assert len(b)==153612+stride*240,(f,len(b),stride)
  depth=b[153612:];canonical=b''.join(depth[y*stride+x*step:y*stride+x*step+2]for y in range(240)for x in range(320))
  color=b[:153600]
  stem.with_suffix('.depth').write_bytes(canonical);stem.with_suffix('.rgb565').write_bytes(color)
  result=dict(passed=True,frame=f,command=cmd,executable_sha256=sha(exe),color_sha256=sha(stem.with_suffix('.rgb565')),depth_sha256=sha(stem.with_suffix('.depth')),rendered_depth_nonzero_pixels=sum(canonical[i:i+2]!=b'\0\0'for i in range(0,len(canonical),2)),depth_dump_timing='end of rendered frame, before next frame clear',stats={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',stem.with_suffix('.log').read_text())})
  put(stem.with_suffix('.json'),result);print(label,model,f,result['stats'].get('cycles'),flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:rows=list(pool.map(one,FRAMES))
 put(d/'result.json',dict(passed=True,frames=rows,limits=__doc__))
def compare(a,b):
 A=R/'replays'/a;B=R/'replays'/b;rows=[]
 for f in FRAMES:
  item=dict(frame=f)
  for ext in ('rgb565','depth'):
   aa=(A/f'frame_{f:05}.{ext}').read_bytes();bb=(B/f'frame_{f:05}.{ext}').read_bytes();assert len(aa)==len(bb)==153600
   dif=[i//2 for i in range(0,len(aa),2)if aa[i:i+2]!=bb[i:i+2]]
   item[ext]=dict(equal=aa==bb,differing_pixels=len(dif),percent=100*len(dif)/76800,first_pixels=dif[:16])
  rows.append(item)
 report=dict(a=a,b=b,frames=rows,all_exact=all(all(r[e]['equal']for e in ('rgb565','depth'))for r in rows))
 put(R/'replays'/f'{a}_vs_{b}.json',report);print(json.dumps(report,indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
 a=sub.add_parser('capture');a.add_argument('label');a.add_argument('elf',type=Path);a.add_argument('mode',choices=['cpu','inline','worker'])
 a=sub.add_parser('replay');a.add_argument('label');a.add_argument('model',choices=['baseline','worker','timing-worker'])
 a=sub.add_parser('compare');a.add_argument('a');a.add_argument('b')
 n=p.parse_args()
 if n.action=='capture':capture(n.label,n.elf,n.mode)
 elif n.action=='replay':replay(n.label,n.model)
 else:compare(n.a,n.b)
