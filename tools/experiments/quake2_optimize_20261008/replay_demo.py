#!/usr/bin/env python3
"""Paired sampled replay on selected GPU/cache RTL with full physical CRAM."""
from pathlib import Path
import argparse, concurrent.futures, difflib, hashlib, json, re, shutil, struct, subprocess
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/os30-fps25-20261005/phase10'
O=R/'docs/measurements/quake2-optimize-20261008/rtl-demo-v1'
B=P/'gpu-endpoint-replay-v1/model'
S=R/'docs/measurements/quake2-optimize-20261008/demo-screen-v2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def prepare():
 O.mkdir(exist_ok=False);shutil.copytree(B/'frozen',O/'frozen')
 for name,pin in {
  'gpu_core.v':'5e9dd586e64034ba7b9f2bb97d9f4b8cba20c772da21d263f38ad74049269e5a',
  'gpu_tex_cache.v':'c3fdc0adfe5ed583c37bf09ad3dbbbecb1e3c6ae1f5714cab5064eacc328e51c',
  'gpu_color_depth_cache.sv':'ab1f1aeeb135cd2f80e44adb39616c35f28f1e164ab639b2f5645fbb447000ae'}.items():
  p=R/'src/fpga/common/os30'/name;assert sha(p)==pin;shutil.copy2(p,O/'frozen/common'/name)
 shutil.copy2(P/'cram1-share-full-v1/frozen/cram_chip_model.v',O/'frozen/cram_chip_model.v')
 tb=O/'frozen/tb_gpu.v';s=tb.read_text()
 a='.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(6), .WORD_BITS(4)'
 assert s.count(a)==1
 s=s.replace(a,'.ADDR_W(26), .CACHE_ALL(1), .WAYS(2), .SET_BITS(8), .WORD_BITS(4),\n    .REGISTER_READ_LOOKUP(1), .REGISTER_FILL_HAZARD(1), .SPECULATE_WRITE_HOT0(1)')
 # Exact production GPU bytes have no passive proof registers. Remove only
 # the three inherited checks that referenced those proof-only registers.
 start=s.index('reg [63:0] origin_product_cycles=')
 end=s.index('// Literal original four assignment sites',start)
 s=s[:start]+s[end:]
 a='always @(negedge clk)if(owner_checked&&gpu.dsp_a!==gpu.proof_dsp_a_control_old)\n $fatal(1,"DSP-A next-state differs from literal original full writer chain");'
 assert s.count(a)==1;s=s.replace(a,'')
 a='always @(negedge clk)if(step_checked)begin\n if(gpu.sp_sstep!==gpu.proof_old_sp_sstep || gpu.sp_tstep!==gpu.proof_old_sp_tstep)\n  $fatal(1,"running-step full original writer shadow differs");\nend'
 assert s.count(a)==1;s=s.replace(a,'');assert 'gpu.proof_' not in s;tb.write_text(s)
 p=O/'frozen/texture_memory.sv';s=p.read_text();assert s.count('.bd_word_addr(21\'b0)')==1;p.write_text(s.replace('.bd_word_addr(21\'b0)', '.bd_word_addr(22\'b0)'))
 p=O/'frozen/texture_observer.hpp';s=p.read_text().replace('0x200000','0x1000000')
 a='auto &mem=tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;'
 b='auto &mem=(a&0x800000)?tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem1:tb->rootp->tb_gpu__DOT__texture_memory__DOT__chip__DOT__mem0;\n    uint32_t h=(a&0x7fffff)>>1;'
 assert s.count(a)==2;s=s.replace(a,b)
 s=s.replace('mem[a>>1]','mem[h]').replace('mem[(a>>1)+1]','mem[h+1]')
 # Non-tagged historical page-remap mode is deliberately unsupported here.
 s=s.replace('if(!fast_tex||tagged_tex)return;','if(!fast_tex||tagged_tex)return;\n    throw std::runtime_error("Q2 replay requires tagged texture snapshots");')
 p.write_text(s)
 p=O/'frozen/replay.cpp';s=p.read_text()
 assert s.count('>0x200000')==2;s=s.replace('>0x200000','>0x1000000')
 p.write_text(s)
 cmd=[x.replace(str(B),str(O)) for x in json.loads((B/'command.json').read_text())]
 put(O/'command.json',cmd);shutil.copy2(__file__,O/'recipe.py')
 delta=[]
 for p in sorted((O/'frozen').rglob('*')):
  if p.is_file():
   old=B/'frozen'/p.relative_to(O/'frozen')
   if sha(p)!=sha(old):delta.append(dict(path=str(p.relative_to(O)),before=sha(old),after=sha(p)))
 put(O/'prepared.json',dict(passed=True,changes=delta,recipe_sha256=sha(__file__),
  files={str(p.relative_to(O)):sha(p)for p in (O/'frozen').rglob('*')if p.is_file()},
  source_evidence={str(p.relative_to(R)):sha(p)for p in [B/'command.json',P/'gpu-endpoint-replay-v1/manifest.json',P/'cram1-share-full-v1/frozen/cram_chip_model.v']},
  scope='Exact selected GPU, texture cache,32KiB render cache and params; inherited full SDRAM/CRAM controllers and monitored chip fixtures. CPU/ring producer synthetic; no native CPU or FPS claim.',
  filter='Preserve raw vector; omit MEM1 snapshots wholly below byte0x300000 only (three CPU framebuffer pages) to avoid replay overwriting GPU render with non-rasterized qsim snapshots. Keep every other memory/texture/MMIO/command record. Append explicit depth dump. CPU-only HUD snapshots are separately compared, not part of final RTL framebuffer.'))
 with (O/'build.log').open('w')as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 put(O/'built.json',dict(passed=True,prepared_sha256=sha(O/'prepared.json'),executable_sha256=sha(O/'obj/Vtb_gpu')))

def vector(source,dest):
 data=source.read_bytes();pos=0;parts=[];removed=[];commands=[];zaddr=zstride=zstep=0
 while pos<len(data):
  start=pos;tag=data[pos:pos+4];pos+=4;keep=True
  if tag in (b'MEM1',b'TEX1'):
   a,n=struct.unpack_from('<II',data,pos);pos+=8+((n+3)&~3)
   if tag==b'MEM1' and a<0x300000:
    assert a+n<=0x300000;keep=False;removed.append(dict(address=a,length=n,sha256=hashlib.sha256(data[start:pos]).hexdigest()))
  elif tag==b'CMDS':
   n=struct.unpack_from('<I',data,pos)[0];pos+=4;w=struct.unpack_from('<'+'I'*n,data,pos);pos+=4*n;i=0
   while i<n:
    op=w[i]>>24;count=w[i]&8191;assert i+count+1<=n
    commands.append(list(w[i:i+count+1]))
    if op==0x4a and count==17:zaddr,zstride,zstep=w[i+12:i+15]
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
 dest.write_bytes(b''.join(parts)+b'DUMP'+struct.pack('<II',zaddr,zstride*240)+b'END.')
 put(dest.with_suffix('.filter.json'),dict(raw_sha256=sha(source),filtered_sha256=sha(dest),removed_cpu_framebuffer_snapshots=removed,zaddr=zaddr,zstride=zstride,zstep=zstep,commands=commands))
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
  b=stem.with_suffix('.bin').read_bytes();assert len(b)==153612+stride*240,(frame,len(b),stride)
  depth=b[153612:];canonical=b''.join(depth[y*stride+x*step:y*stride+x*step+2]for y in range(240)for x in range(320))
  result=dict(passed=True,frame=frame,command=cmd,color_sha256=hashlib.sha256(b[:153600]).hexdigest(),depth_sha256=hashlib.sha256(canonical).hexdigest(),stats={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',stem.with_suffix('.log').read_text())},output_sha256=sha(stem.with_suffix('.bin')))
  put(stem.with_suffix('.json'),result);print(label,frame,result['stats'].get('cycles'),flush=True);return result
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:rows=list(pool.map(one,[20,80,180,300]))
 put(dest/'result.json',dict(passed=True,frames=rows,prepared_sha256=sha(O/'prepared.json')))

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','run']);a.add_argument('--label');n=a.parse_args()
 if n.action=='prepare':prepare()
 else:assert n.label;run(n.label)
