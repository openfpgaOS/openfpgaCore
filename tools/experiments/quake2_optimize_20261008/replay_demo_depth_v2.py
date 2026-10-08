#!/usr/bin/env python3
"""Paired sampled replay on selected GPU/cache RTL with full physical CRAM."""
from pathlib import Path
import argparse, concurrent.futures, difflib, hashlib, json, re, shutil, struct, subprocess
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
O=R/'docs/measurements/quake2-optimize-20261008/rtl-demo-depth-v2'
B=P/'gpu-endpoint-replay-v1/model'
S=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def prepare():
 O.mkdir(exist_ok=False)
 old=O.parent/'rtl-demo-v1'
 built=json.loads((old/'built.json').read_text())
 assert sha(old/'prepared.json')==built['prepared_sha256']
 assert sha(old/'obj/Vtb_gpu')==built['executable_sha256']
 shutil.copytree(old/'frozen',O/'frozen');(O/'obj').mkdir()
 shutil.copy2(old/'obj/Vtb_gpu',O/'obj/Vtb_gpu');shutil.copy2(__file__,O/'recipe.py')
 prep=json.loads((old/'prepared.json').read_text())
 prep['recipe_sha256']=sha(__file__)
 prep['parent_model_prepared_sha256']=sha(old/'prepared.json')
 prep['model_rebuilt']=False
 prep['filter']+=' Snapshot rendered Z immediately before last Z-clear, after preceding original frame fence; split only that CMDS envelope, commands/payload/order unchanged. Readback drains the pre-clear GPU work. Final cleared depth remains separately recorded.'
 put(O/'prepared.json',prep)
 put(O/'built.json',dict(passed=True,prepared_sha256=sha(O/'prepared.json'),executable_sha256=sha(O/'obj/Vtb_gpu'),model_reused_from=str(old)))

def vector(source,dest):
 data=source.read_bytes();pos=0;records=[];removed=[];commands=[];zaddr=zstride=zstep=0;clears=[]
 while pos<len(data):
  start=pos;tag=data[pos:pos+4];pos+=4;keep=True;words=None
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
    if op==0x11:clears.append((len(records),i,words[i+1]&0x3ffffff,len(commands)-1))
    i+=count+1
  elif tag==b'TLUT':pos+=32768
  elif tag==b'PALB':pos+=4
  elif tag in (b'MMIO',b'DUMP'):pos+=8
  elif tag==b'RAND':pos+=12
  elif tag in (b'FLSH',b'FENC',b'HASH'):pass
  elif tag==b'END.':assert pos==len(data);break
  else:raise AssertionError((tag,pos))
  assert pos<=len(data)
  if keep:records.append((tag,data[start:pos],words))
 assert zaddr and zstride and zstep
 target=[v for v in clears if v[2]==(zaddr&0x3ffffff)][-1]
 ri,wi,_,ci=target
 assert records[ri][0]==b'CMDS'
 # There must be rendered triangles and a frame fence before the final clear.
 assert any(c[0]>>24==0x4e for c in commands[:ci])
 assert any(c[0]>>24==0x02 for c in commands[max(0,ci-8):ci])
 parts=[]
 for i,(tag,raw,words)in enumerate(records):
  if i!=ri:parts.append(raw);continue
  if wi:parts.append(b'CMDS'+struct.pack('<I',wi)+struct.pack('<'+'I'*wi,*words[:wi]))
  parts.append(b'DUMP'+struct.pack('<II',zaddr,zstride*240))
  after=words[wi:]
  parts.append(b'CMDS'+struct.pack('<I',len(after))+struct.pack('<'+'I'*len(after),*after))
 dest.write_bytes(b''.join(parts)+b'DUMP'+struct.pack('<II',zaddr,zstride*240)+b'END.')
 put(dest.with_suffix('.filter.json'),dict(raw_sha256=sha(source),filtered_sha256=sha(dest),removed_cpu_framebuffer_snapshots=removed,zaddr=zaddr,zstride=zstride,zstep=zstep,commands=commands,rendered_depth_before_clear_command_index=ci,command_order_and_payload_unchanged=True))
 return zstride,zstep

def run(label):
 prep=json.loads((O/'prepared.json').read_text());assert sha(__file__)==prep['recipe_sha256']
 for n,h in prep['files'].items():assert sha(O/n)==h,n
 assert sha(O/'obj/Vtb_gpu')==json.loads((O/'built.json').read_text())['executable_sha256']
 dest=O/label;dest.mkdir(exist_ok=False)
 def one(frame):
  stem=dest/f'frame_{frame:05}';source=S/label/'gpuvec'/f'frame_{frame:05}.vec'
  stride,step=vector(source,stem.with_suffix('.vec'))
  cmd=[str(O/'obj/Vtb_gpu'),'+fast_tex=1','+tagged_tex=1','+memory_traffic=0',str(stem.with_suffix('.vec')),str(stem.with_suffix('.bin'))]
  with stem.with_suffix('.log').open('w')as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=300)
  assert r.returncode==0,(frame,r.returncode)
  b=stem.with_suffix('.bin').read_bytes();assert len(b)==153612+2*stride*240,(frame,len(b),stride)
  depth=b[:stride*240];canonical=b''.join(depth[y*stride+x*step:y*stride+x*step+2]for y in range(240)for x in range(320))
  result=dict(passed=True,frame=frame,command=cmd,color_sha256=hashlib.sha256(b[stride*240:stride*240+153600]).hexdigest(),rendered_depth_nonzero_pixels=sum(canonical[i:i+2]!=b'\0\0' for i in range(0,len(canonical),2)),final_depth_clear=not any(b[-stride*240:]),depth_sha256=hashlib.sha256(canonical).hexdigest(),stats={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',stem.with_suffix('.log').read_text())},output_sha256=sha(stem.with_suffix('.bin')))
  put(stem.with_suffix('.json'),result);print(label,frame,result['stats'].get('cycles'),flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:rows=list(pool.map(one,[20,80,180,300]))
 put(dest/'result.json',dict(passed=True,frames=rows,prepared_sha256=sha(O/'prepared.json')))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run']);a.add_argument('--label');n=a.parse_args()
 if n.action=='prepare':prepare()
 else:assert n.label;run(n.label)
