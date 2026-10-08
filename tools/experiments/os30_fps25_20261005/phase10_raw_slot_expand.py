#!/usr/bin/env python3
"""Expand raw slots with the executable native-FMA reference, then replay RTL."""
import ctypes,json,struct,subprocess,concurrent.futures,os
from pathlib import Path
from phase10_experiment import CAMP,module,sha
HERE=Path(__file__).resolve().parent
OUT=CAMP/'phase10'/os.environ.get('RAW_SLOT_LABEL','raw-slot-ring-v1')
Q=CAMP/'phase10/raw-slot-ring-v1/functional-qsim'
CAPTURE=os.environ.get('RAW_SLOT_CAPTURE','capture-checkpoint')
WRAPPER=r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
typedef struct gpu {
uint32_t raw_slot[32][6],raw_context[5],raw_valid,raw_projected;
unsigned raw_context_valid,raw_internal;
uint64_t raw_uploads,raw_contexts,raw_triangles,raw_rejected,raw_formatted,raw_draws;
} gpu_t;
#include "raw_slot_model.h"
static gpu_t state;
static uint32_t result[8192];static unsigned result_count;
static void exec_command(gpu_t*g,uint32_t hdr,const uint32_t*p) {
unsigned n=hdr&8191;assert(result_count+n+1<=8192);
result[result_count++]=hdr;memcpy(result+result_count,p,n*4);result_count+=n;
}
void reset(void){memset(&state,0,sizeof state);}
unsigned expand(uint32_t hdr,const uint32_t*p){
result_count=0;unsigned op=hdr>>24;
if(op>=0x60&&op<=0x62)rs_execute(&state,hdr,p);
else {if(op==0x50||op==0x53||op==0x56)state.raw_projected=0;exec_command(&state,hdr,p);}
return result_count;
}
uint32_t *output(void){return result;}
'''
def library():
 p=OUT/'expander';p.mkdir(exist_ok=False);(p/'model.c').write_text(WRAPPER)
 subprocess.run(['cc','-shared','-fPIC','-O2','-DQSIM_RETAINED_FMA26','-fno-strict-aliasing','-ffp-contract=off','-fno-fast-math','-I'+str(Q),str(p/'model.c'),str(Q/'fpu.c'),'-lm','-o',str(p/'model.so')],check=True)
 lib=ctypes.CDLL(str(p/'model.so'));lib.expand.argtypes=[ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32)];lib.expand.restype=ctypes.c_uint;lib.output.restype=ctypes.POINTER(ctypes.c_uint32)
 return lib
def convert(source,dest,lib):
 data=source.read_bytes();pos=0;out=bytearray();lib.reset();before={};after={}
 seed=source.with_suffix('.vec.rawseed')
 if seed.exists():
  valid,*context=struct.unpack('<6I',seed.read_bytes())
  if valid:lib.expand(0x61000005,(ctypes.c_uint32*5)(*context))
 while pos<len(data):
  begin=pos;tag=data[pos:pos+4];pos+=4
  if tag==b'CMDS':
   n,=struct.unpack_from('<I',data,pos);pos+=4;end=pos+n*4;words=[]
   while pos<end:
    h,=struct.unpack_from('<I',data,pos);size=h&8191;body=struct.unpack_from(f'<{size}I',data,pos+4);pos+=(size+1)*4
    before[hex(h>>24)]=before.get(hex(h>>24),0)+1
    arr=(ctypes.c_uint32*size)(*body);count=lib.expand(h,arr);p=lib.output();added=list(p[:count]);words+=added
    j=0
    while j<len(added):
     op=hex(added[j]>>24);after[op]=after.get(op,0)+1;j+=(added[j]&8191)+1
   assert pos==end;out+=tag+struct.pack('<I',len(words))+struct.pack(f'<{len(words)}I',*words);continue
  if tag in (b'MEM1',b'TEX1'):
   n=struct.unpack_from('<I',data,pos+4)[0];pos+=8+((n+3)&~3)
  elif tag==b'MMIO':pos+=8
  elif tag==b'TLUT':pos+=32768
  elif tag==b'PALB':pos+=4
  elif tag==b'DUMP':pos+=8
  elif tag==b'RAND':pos+=12
  else:assert tag in (b'FLSH',b'FENC',b'HASH',b'END.'),tag
  out+=data[begin:pos]
 assert pos==len(data);dest.write_bytes(out)
 return dict(source_sha256=sha(source),expanded_sha256=sha(dest),raw_op_counts=before,expanded_op_counts=after)
def main():
 lib=library();out=OUT/'replay-reference';out.mkdir(exist_ok=False)
 base=CAMP/'phase10/gpu-direct-native-functional-v1/capture-baseline/gpuvec'
 exe=CAMP/'phase10/vcache-narrow-v1/model/obj/Vtb_gpu'
 adapter=module('raw_depth_adapter',HERE/'phase4_combined_validate.py');jobs=[];converted=[];depth={}
 for f in [40,120,200,360,400,500,600,720]:
  expanded=out/f'expanded-{f}.vec';converted.append(dict(frame=f,**convert(OUT/f'{CAPTURE}/gpuvec/frame_{f:05d}.vec',expanded,lib)))
  for which,source in [('baseline',base/f'frame_{f:05d}.vec'),('candidate',expanded)]:
   name=f'{which}-{f}';vec=out/f'{name}.vec';_,depth[name],_=adapter.with_depth(source,vec)
   jobs.append((name,[str(exe),'+memory_traffic=1','+fast_tex=1','+tagged_tex=1',str(vec),str(out/f'{name}.bin')]))
 (out/'expanded.json').write_text(json.dumps(converted,indent=2)+'\n')
 def run(j):
  name,args=j
  with (out/f'{name}.log').open('w') as o:r=subprocess.run(args,stdout=o,stderr=subprocess.STDOUT)
  return dict(name=name,command=args,returncode=r.returncode)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:runs=list(pool.map(run,jobs))
 comparisons=[]
 for f in [40,120,200,360,400,500,600,720]:
  a=(out/f'baseline-{f}.bin').read_bytes();b=(out/f'candidate-{f}.bin').read_bytes();has=depth[f'baseline-{f}'];assert has==depth[f'candidate-{f}'];assert len(a)==len(b)==153612+(153600 if has else 0)
  comparisons.append(dict(frame=f,depth_present=has,color_exact=a[:153600]==b[:153600],fence_exact=a[153600:153604]==b[153600:153604],depth_exact=a[153612:]==b[153612:]))
 result=dict(passed=all(x['returncode']==0 for x in runs) and all(x['color_exact'] and x['fence_exact'] and x['depth_exact'] for x in comparisons),scope='Eight actual retained GPU+arbiter+SDRAM/PSRAM replays under audio/scanout traffic. Raw opcode cull/format expansion uses executable native-FMA software reference; new worker RTL/timing not included. Full-frame raster correctness is bounded to these eight views.',comparisons=comparisons,runs=runs,simulator_sha256=sha(exe),expander_sha256=sha(OUT/'expander/model.so'),input_sha256={str(x.relative_to(out)):sha(x) for x in out.glob('*.vec')})
 (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ('passed','comparisons')},indent=2))
if __name__=='__main__':main()
