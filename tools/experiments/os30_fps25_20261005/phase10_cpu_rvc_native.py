#!/usr/bin/env python3
"""Native decoder/control regressions for exact RVC bit-plane rewrite."""
import concurrent.futures,hashlib,json,os,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';O=P/'cpu-rvc-bits-v2';N=O/'native';OLD=P/'cpu-fp-rs3-mux-v1/native';B=ROOT/'docs/measurements/os30-dual-20261004/validation'
CPU=Path('src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v');sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def prepare():
 assert json.loads((O/'proof/result.json').read_text())['passed'];N.mkdir(exist_ok=False);shutil.copytree(OLD/'frozen',N/'frozen')
 source=(O/'VexiiRiscv.v').read_text();oldcpu=(OLD/'frozen'/CPU).read_text()
 a=oldcpu.index('wire [31:0] fp_rs3_reference =');b=oldcpu.index('\nendmodule',a);monitor=oldcpu[a:b]
 monitor+='''
 reg [63:0] rvc_checks=0,rvc_compressed=0,rvc_raw32=0,rvc_illegal=0;
 wire [31:0] rvc_reference_instruction=AlignerPlugin_logic_feeder_lanes_0_isRvc ? AlignerPlugin_logic_feeder_lanes_0_withRvc_dec_inst : AlignerPlugin_logic_extractors_0_ctx_instruction;
 always @(posedge clk) if(!reset) begin
  assert(rvc_reference_instruction==decode_ctrls_0_up_Decode_INSTRUCTION_0);
  rvc_checks<=rvc_checks+1;
  if(decode_ctrls_0_up_isFiring)begin
   if(AlignerPlugin_logic_feeder_lanes_0_isRvc)rvc_compressed<=rvc_compressed+1;
   else rvc_raw32<=rvc_raw32+1;
   if(decode_ctrls_0_up_Decode_DECOMPRESSION_FAULT_0)rvc_illegal<=rvc_illegal+1;
  end
 end
 final $display("RVC_COVER checks=%0d compressed=%0d raw32=%0d illegal=%0d",rvc_checks,rvc_compressed,rvc_raw32,rvc_illegal);
'''
 e=source.index('\nendmodule',source.index('module VexiiRiscv ('));source=source[:e]+'\n'+monitor+source[e:];(N/'frozen'/CPU).write_text(source)
 cmd=[x.replace(str(OLD),str(N)) for x in json.loads((OLD/'prepared.json').read_text())['command']]
 save(N/'prepared.json',dict(command=cmd,candidate_sha256=sha(O/'VexiiRiscv.v'),sources_sha256={str(p.relative_to(N)):sha(p) for p in (N/'frozen').rglob('*') if p.is_file()}))
 shutil.copy2(__file__,N/'recipe.py')
def model():
 p=json.loads((N/'prepared.json').read_text())
 for n,h in p['sources_sha256'].items():assert sha(N/n)==h,n
 with (N/'build.log').open('x') as f:subprocess.run(p['command'],cwd=N/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT,check=True)
 save(N/'build.json',dict(simulator_sha256=sha(N/'obj/Vtb_system'),passed=True))
def run():
 d=N/'runs';d.mkdir(exist_ok=False)
 programs={'frontend':B/'frontend-hazards/frontend.bin','stress':B/'firmware/stress.bin','lighting':P/'cpu-audit/lighting-native/firmware/kernel.bin'}
 for name,p in programs.items():
  w=d/name;w.mkdir();shutil.copy2(p,w/'kernel.bin');shutil.copy2(B/'firmware/firmware.mif',w/'firmware.mif')
 def one(job):
  name,mode=job;w=d/name
  env=dict(SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PERIOD='6361' if name=='lighting' else '700',SCAN_LEN='160' if name=='lighting' else '128',SCAN_PHASE='317')
  if name=='lighting':env.update(STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_ACTIVE='240',SCAN_TOTAL='262')
  logs={};sources={}
  for variant,exe in [('reference',OLD/'obj/Vtb_system'),('candidate',N/'obj/Vtb_system')]:
   log=w/(mode+'-'+variant+'.log');cmd=[str(exe),'kernel.bin','30000000']
   with log.open('x') as f:subprocess.run(cmd,cwd=w,env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,timeout=300,check=True)
   t=log.read_text();assert 'HAL init' in t and 'FAIL' not in t
   assert 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in t
   assert 'R mismatches=0  scanout mismatches=0  rlast slips=0  orphan beats=0  DRAM strikes=0' in t
   logs[variant]=t;sources[variant]=dict(command=cmd,simulator_sha256=sha(exe),log_sha256=sha(log))
  uart=lambda t:t.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
  assert uart(logs['reference'])==uart(logs['candidate']),(name,mode,'UART/cycles differ')
  cycles=lambda t:int(re.search(r'cycles=(\d+) PC=',t)[1])
  assert cycles(logs['reference'])==cycles(logs['candidate']),(name,mode,'end cycle mismatch')
  t=logs['candidate'];m=re.search(r'RVC_COVER checks=(\d+) compressed=(\d+) raw32=(\d+) illegal=(\d+)',t);assert m
  cv=dict(zip(('checks','compressed','raw32','illegal'),map(int,m.groups())));assert all(cv[x]>0 for x in ('checks','compressed','raw32'))
  if name=='frontend':assert 'FRONTEND RESULT' in t
  if name=='stress':assert 'CPU stress PASS: integer, FPU, atomics, traps, cache.' in t and int(re.search(r'IRQ count: ([0-9a-f]+)',t)[1],16)>0
  if name=='lighting':assert 'NATIVE_LIGHTING_COMPLETE' in t and len(re.findall(r'^LIGHT ',uart(t),re.M))==1227
  return dict(program=name,mode=mode,passed=True,UART_byte_equal=True,cycles_equal=True,cycles=cycles(t),coverage=cv,environment=env,sources=sources)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(one,[(n,m) for n in programs for m in ('quiet','nominal')]))
 save(N/'result.json',dict(passed=True,runs=results,total_live_decode_checks=sum(x['coverage']['checks'] for x in results)));print(json.dumps(results,indent=2))
if __name__=='__main__':globals()[sys.argv[1]]()
