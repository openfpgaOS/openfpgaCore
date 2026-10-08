#!/usr/bin/env python3
"""Bind paired normal-ELF software work and sampled actual GPU output."""
from pathlib import Path
import argparse,csv,hashlib,json,struct
R=Path(__file__).resolve().parents[3]
P=R/'docs/measurements/quake2-optimize-20261008'
S=P/'demo-screen-v2';G=P/'rtl-demo-depth-v2'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def put(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def tags(p, wanted):
 b=p.read_bytes();i=0;out=[]
 while i<len(b):
  start=i;t=b[i:i+4];i+=4
  if t in (b'MEM1',b'TEX1'):
   a,n=struct.unpack_from('<II',b,i);i+=8+((n+3)&~3)
  elif t==b'CMDS':n=struct.unpack_from('<I',b,i)[0];i+=4+4*n
  elif t in (b'MMIO',b'DUMP'):i+=8
  elif t==b'TLUT':i+=32768
  elif t==b'PALB':i+=4
  elif t==b'RAND':i+=12
  elif t in (b'FLSH',b'FENC',b'HASH',b'END.'):pass
  else:raise AssertionError((t,i))
  assert i<=len(b)
  if t in wanted:out.append(b[start:i])
 return out
def compare(label):
 O=P/('comparison-'+label+'-v1');O.mkdir(exist_ok=False)
 metrics={};raw=[];proofs=[]
 for lab in ('baseline',label):
  rows=list(csv.DictReader((S/lab/'frames.csv').open()));assert len(rows)==362
  raw.append(rows)
  result=json.loads((S/(lab+'-result.json')).read_text());assert result['passed']
  assert result['elf_sha256']==sha(S/(lab+'.elf'))
  summary=json.loads((S/lab/'summary.json').read_text())
  assert summary['env']['unmapped_mmio']==0 and summary['gpu_sync']['ring_overflows']==0
  assert summary['gpu_sync']['queued_words_at_end']==0
  assert summary['fpu_subnormal']==dict(operands=0,results=0)
  metrics[lab]={k:sum(int(x[k]) for x in rows[20:360])/340 for k in ('insns','est_cycles','gpu_cmd_words')}
 for key in ('frame','host_framecount','buffer','gpu_cmd_words','hash_cmd','hash_px','hash_z','hash_pal'):
  assert [x[key]for x in raw[0]]==[x[key]for x in raw[1]],key
 for frame in (20,80,180,300):
  name=f'frame_{frame:05}'
  a=json.loads((G/'baseline'/(name+'.json')).read_text());b=json.loads((G/label/(name+'.json')).read_text())
  assert a['passed'] and b['passed']
  assert a['color_sha256']==b['color_sha256'] and a['depth_sha256']==b['depth_sha256']
  assert a['rendered_depth_nonzero_pixels']>0 and a['final_depth_clear'] and b['final_depth_clear']
  assert a['stats']['cycles']==b['stats']['cycles']
  assert a['stats']['cram_errors']==b['stats']['cram_errors']==0
  aa=(S/'baseline/gpuvec'/(name+'.vec'));bb=(S/label/'gpuvec'/(name+'.vec'))
  assert tags(aa,{b'CMDS',b'MMIO',b'TEX1',b'TLUT',b'PALB'})==tags(bb,{b'CMDS',b'MMIO',b'TEX1',b'TLUT',b'PALB'})
  fa=json.loads((G/'baseline'/(name+'.filter.json')).read_text());fb=json.loads((G/label/(name+'.filter.json')).read_text())
  assert fa['commands']==fb['commands']
  # The readback snapshot must not delete/change/reorder any original command.
  for lab,filt in (('baseline',fa),(label,fb)):
   src=tags(S/lab/'gpuvec'/(name+'.vec'),{b'CMDS'})
   dst=tags(G/lab/(name+'.vec'),{b'CMDS'})
   assert b''.join(x[8:]for x in src)==b''.join(x[8:]for x in dst)
  proofs.append(dict(frame=frame,color_sha256=a['color_sha256'],rendered_depth_sha256=a['depth_sha256'],
    rendered_depth_nonzero_pixels=a['rendered_depth_nonzero_pixels'],final_depth_is_cleared=True,
    rtl_cycles=a['stats']['cycles'],commands_textures_mmio_exact=True))
 assert len({p['rendered_depth_sha256']for p in proofs})==4
 gain=metrics['baseline']['est_cycles']-metrics[label]['est_cycles']
 result=dict(passed=True,baseline_elf_sha256=sha(S/'baseline.elf'),candidate_elf_sha256=sha(S/(label+'.elf')),
  software_frame_comparisons=362,steady_frame_indices=[20,359],steady_denominator=340,
  metrics=metrics,modeled_cycles_saved_per_frame=gain,modeled_cpu_percent_saved=100*gain/metrics['baseline']['est_cycles'],
  modeled_ms_saved_at_configured_100MHz=gain/100000,sampled_rtl_pairs=proofs,
  exact_current_hw_features='0x2b136753',geometry_enabled=False,texture_bytes=16777216,
  limits=['CPU timing is inherited scalar qsim with I64/D64 capacities, not actual selected pipelined Vexii or Pocket FPS.',
   'No real kernel/audio/CPU-GPU contention; fixed30Hz sound-disabled demo only.',
   'Instantaneous functional CRAM uploads in qsim; actual sampled GPU reads use physical16MiB controller/chip model.',
   'GPU framebuffer replay omits only original CPU framebuffer MEM1 snapshots below0x300000; CPU-only overlays remain outside RTL framebuffer proof and have exact qsim image hashes.',
   'Rendered depth is captured before final clear; inserted readback drains prior commands and can change synthetic scheduling, equally for both sides.',
   'Four sampled frames are not exhaustive gameplay coverage; raw SDRAM full-memory hashes can differ due ELF code bytes.'])
 put(O/'result.json',result)
 files=[Path(__file__),S/'prepared.json',S/'harness-adaptation.json',S/'qsim/qsim',G/'prepared.json',G/'built.json',G/'obj/Vtb_gpu',O/'result.json']
 for lab in ('baseline',label):
  files += [S/(lab+'.elf'),S/(lab+'-result.json'),S/(lab+'-steady-profile.json'),S/(lab+'.log'),S/(lab+'-startup.log')]
  files += [p for p in (S/lab).rglob('*')if p.is_file()]
  files += [p for p in (S/(lab+'-startup')).rglob('*')if p.is_file()]
  files += [p for p in (G/lab).rglob('*')if p.is_file()]
 put(O/'manifest.json',dict(passed=True,evidence_sha256={str(p.relative_to(R)):sha(p)for p in files}))
 print(json.dumps(dict(output=str(O),manifest_sha256=sha(O/'manifest.json'),gain_percent=result['modeled_cpu_percent_saved'],modeled_ms=result['modeled_ms_saved_at_configured_100MHz']),indent=2))
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('label');compare(a.parse_args().label)
