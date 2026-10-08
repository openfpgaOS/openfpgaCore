#!/usr/bin/env python3
"""Bounded actual-GPU zero count, partial chunk, compact slot and reset replay."""
import concurrent.futures,json,re,shutil,struct,subprocess,sys
from pathlib import Path
import phase10_gpu_span_control_acceptance as c
a=c.a;OUT=a.OUT/'zero-records'
def prepare():
 OUT.mkdir(exist_ok=False)
 for kind,base in [('baseline',c.review.OLD/'model'),('candidate',a.OUT/'model')]:
  d=OUT/kind;shutil.copytree(base/'frozen',d/'frozen')
  cmd=[x.replace(str(base),str(d))for x in json.loads((base/'command.json').read_text())]
  cmd=[x for x in cmd if x!='+define+EXCLUDE_GPU_PARAM_SPAN_LIST']
  for name in ['INCLUDE_PARAM_SPAN_LIST','INCLUDE_COMPACT_SPAN','INCLUDE_COLUMN_LIST']:
   old='-G'+name+'=0';assert cmd.count(old)==1;cmd[cmd.index(old)]='-G'+name+'=1'
  # The selected configuration omits these command inputs. This independent
  # protocol configuration enables their unchanged original decoder paths.
  p=d/'frozen/replay.cpp';s=p.read_text();marker='        } else if (tag == GV_TAG_FLSH) {'
  assert s.count(marker)==1
  s=s.replace(marker,'''        } else if (tag == GV_TAG('R','S','E','T')) {
            if(!wait_idle(50000000ull))throw std::runtime_error("pre-reset drain timeout");
            mmio_write(0,6);tick(8);mmio_write(0,16);
'''+marker)
  p.write_text(s);a.save(d/'command.json',cmd)
  if kind=='candidate':
   p=d/'frozen/common/gpu_core.v';s=p.read_text()
   s=a.rep(s,'reg[3:0] proof_cnt_valid_old;',"reg[3:0] proof_cnt_valid_old;\nreg[63:0]proof_stale_zero_overwrite=0;\nreg[3:0]proof_zero_lanes=0,proof_nonzero_lanes=0;\nfinal $display(\"ZERO_RECORD_AUDIT stale_zero=%0d zero_lanes=%0d nonzero_lanes=%0d\",proof_stale_zero_overwrite,proof_zero_lanes,proof_nonzero_lanes);")
   s=a.rep(s,'if(spanprod_select)$fatal(1,"count RAM read/write ownership collision");', '''if(spanprod_select)$fatal(1,"count RAM read/write ownership collision");
        if(spanprod_count_wd==0)begin
            proof_zero_lanes<=proof_zero_lanes|(4'b1<<spanprod_count_wa);
            if(spanprod_count[spanprod_count_wa]!=0)proof_stale_zero_overwrite<=proof_stale_zero_overwrite+1;
        end else proof_nonzero_lanes<=proof_nonzero_lanes|(4'b1<<spanprod_count_wa);''')
   p.write_text(s)
 (OUT/'recipe.py').write_text(Path(__file__).read_text())

def build():
 def one(kind):
  d=OUT/kind;cmd=json.loads((d/'command.json').read_text())
  with(d/'build.log').open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
  a.save(d/'build.json',dict(simulator_sha256=a.sha(d/'obj/Vtb_gpu')))
 with concurrent.futures.ThreadPoolExecutor(max_workers=2)as pool:list(pool.map(one,['baseline','candidate']))

def run():
 pack=lambda *w:struct.pack('<'+'I'*len(w),*[x&0xffffffff for x in w])
 fb,z,tex=0x100000,0x180000,0x10000
 texture=pack(0xffffffff)+bytes(60)
 data=bytearray(b'TEX1'+pack(tex,len(texture))+texture+b'MEM1'+pack(tex,len(texture))+texture)
 data+=b'RAND'+pack(fb,128*128*2,0xabc123)+b'MEM1'+pack(z,128*128*2)+bytes(128*128*2)
 words=[];cases=[]
 def emit(payload):words.extend([0x48000000|len(payload)]+payload)
 def flush():
  nonlocal data,words
  if words:data+=b'CMDS'+pack(len(words))+pack(*words);words=[]
 for epoch in range(3):
  # Every lane is first written nonzero and then zero; lengths cross chunk
  # refill and odd final-record packing. Both axes and depth modes are used.
  for n in (4,1,2,3,5,7,8,9,12):
   for zeros in (0,15,5,10):
    axis=(n+epoch)&1;major,minor=(2,256)if axis else(256,2)
    payload=[0]*(31+((n+1)//2)*3)
    payload[:8]=[fb,major,minor,tex,1,0,0,0xa0|(1<<12)|(axis<<16)|((epoch%4)<<24)]
    payload[14]=65536;payload[17]=63<<16;payload[26:31]=[z,major,minor,n,0]
    records=[]
    for i in range(n):records.append((4+i*2,4+n+epoch*25,0 if (zeros>>(i%4))&1 else 1+(i%4)))
    for i in range(0,n,2):
     u,v,count=records[i];u1,v1,c1=records[i+1]if i+1<n else(0,0,0);k=31+(i//2)*3
     payload[k:k+3]=[(v<<16)|u,(u1<<16)|count,(c1<<16)|v1]
    emit(payload);cases.append(dict(kind='long',epoch=epoch,count=n,zero_mask=zeros,axis=axis))
    if len(words)>1500:flush()
  for n in range(1,5):
   for zeros in (0,15,5,10):
    payload=[n<<28|0x80<<20,1,0,2]
    for i in range(n):payload +=[fb+(80+epoch*8+i)*256+8,tex,(63<<16)|(0 if(zeros>>i)&1 else 3),0,0,0,0]
    emit(payload);cases.append(dict(kind='compact',epoch=epoch,count=n,zero_mask=zeros))
  flush();data+=b'RSET'
 words+=[0x02000001,0x17c0ffee];flush()
 data+=b'DUMP'+pack(fb,128*128*2)+b'DUMP'+pack(z,128*128*2)+b'FENCEND.'
 vec=OUT/'zeros.vec';vec.write_bytes(data);a.save(OUT/'cases.json',cases)
 rows=[]
 for traffic in (0,2):
  stats={};outputs={}
  for kind in ('baseline','candidate'):
   exe=OUT/kind/'obj/Vtb_gpu';name=f'{kind}-t{traffic}';log=OUT/(name+'.log');output=OUT/(name+'.bin')
   cmd=[str(exe),'+fast_tex=1',f'+memory_traffic={traffic}','+tagged_tex=1',str(vec),str(output)]
   with log.open('x')as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
   s=log.read_text();assert'FATAL'not in s;stats[kind]=re.search(r'RESULT cycles=(\d+) busy_cycles=(\d+) batches=(\d+)',s).groups();outputs[kind]=output.read_bytes()
  assert outputs['baseline']==outputs['candidate'];assert stats['baseline']==stats['candidate'],stats
  counts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'SPAN_CONTROL_AUDIT (.*)',(OUT/f'candidate-t{traffic}.log').read_text()).group(1))}
  assert all(counts[k]>0 for k in ['selected_zero','selected_nonzero','writes_zero','writes_nonzero','clears','resets']),counts
  zcounts={k:int(v)for k,v in re.findall(r'(\w+)=(\d+)',re.search(r'ZERO_RECORD_AUDIT (.*)',(OUT/f'candidate-t{traffic}.log').read_text()).group(1))}
  assert zcounts['stale_zero']>0 and zcounts['zero_lanes']==zcounts['nonzero_lanes']==15,zcounts
  rows.append(dict(zero_coverage=zcounts,traffic=traffic,stats=stats['candidate'],output_sha256=a.sha(OUT/f'candidate-t{traffic}.bin'),counts=counts))
 a.save(OUT/'result.json',dict(passed=True,pairs=2,command_cases=len(cases),exact_cycles_and_outputs=True,runs=rows,scope='Additional unchanged decoder paths enabled in both GPUs; selected-native configuration is separately compared in 16 pairs. Soft reset only after idle drain.'))
if __name__=='__main__':
 for name in sys.argv[1:]:globals()[name]()
