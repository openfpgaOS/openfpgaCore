#!/usr/bin/env python3
"""Current readonly alias + real CPU-ring submission A/B on existing CPU RTL."""
import hashlib,json,os,re,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'lod5-queue-native-v1';Q=P/'cpu-queues-v1';OLD=P/'bram-alias-native-v1'
BASE=P/'lod5-cram-span-core-v2';CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n')

SUBMIT=r'''
#define GPU(off) (*(volatile uint32_t*)(0x4a000000u+(off)))
static __attribute__((noinline)) void submit(const uint32_t*src,unsigned remaining){
 volatile uint32_t*port=(volatile uint32_t*)0x4a000004u;
 while(remaining>=16){
  {uint32_t a=src[0],b=src[1],c=src[2],d=src[3];*port=a;*port=b;*port=c;*port=d;}
  {uint32_t a=src[4],b=src[5],c=src[6],d=src[7];*port=a;*port=b;*port=c;*port=d;}
  {uint32_t a=src[8],b=src[9],c=src[10],d=src[11];*port=a;*port=b;*port=c;*port=d;}
  {uint32_t a=src[12],b=src[13],c=src[14],d=src[15];*port=a;*port=b;*port=c;*port=d;}
  src+=16;remaining-=16;
 }
 while(remaining--)*port=*src++;
 uint32_t status=GPU(0x14);if((status&0x300)!=0x100)fail(900,status,0x100);
 GPU(0)=8;
}
static void submission(void){
 uint32_t sizes[6]={16,64,128,512,1024,4094};
 const uint32_t*source=(const uint32_t*)0x13100000u;
 volatile uint32_t*u=(volatile uint32_t*)0x53100000u;
 GPU(0)=6;GPU(0)=16;
 for(unsigned i=0;i<4094;i++)u[i]=(i&1)?i:0x02000001u;
 fence();inval((void*)source,16384);
 for(unsigned cold=0;cold<2;cold++)for(unsigned size=0;size<6;size++){
  unsigned n=sizes[size],total=0;
  for(unsigned repeat=0;repeat<8;repeat++){
   if(cold)inval((void*)source,n*4);
   unsigned published=(GPU(4)+n*4)&16383u;
   unsigned a=cycle();submit(source,n);fence();unsigned b=cycle();total+=b-a;
   unsigned limit=200000;
   while(GPU(4)!=published && --limit){}
   if(!limit)fail(901,GPU(4),published);
   while((GPU(0x10)!=published||(GPU(0x14)&1)) && --limit){}
   if(!limit)fail(902,GPU(0x10),published);
   if(GPU(0x18)!=n-1)fail(903,GPU(0x18),n-1);
  }
  text("SUBMIT_METHOD ");hex(cold);hex(n);hex(8);hex(total);put('\n');
 }
 text("SUBMISSION_COMPLETE\n");
}
'''

def prepare():
 OUT.mkdir(exist_ok=False)
 cpu_recipe=json.loads((Q/'recipe.json').read_text())
 assert len(cpu_recipe['changes'])==5
 shim=P/'lod5-components-v1/frozen/phase10_bram_alias.v'
 assert sha(shim)=='b265bab9aeb93c5ae2444f652579b0fdc13cdab90e06b63d2c3fbcaf49b802d9'
 for mode in ('control','queues'):
  d=OUT/mode;d.mkdir();shutil.copytree(Q/'cpu-model/frozen',d/'frozen')
  cpu=BASE/'frozen'/CPU if mode=='control' else Q/'VexiiRiscv.v'
  shutil.copyfile(cpu,d/'frozen'/CPU)
  if mode=='control':shutil.copyfile(BASE/'frozen/src/fpga/common/cpu_system.v',d/'frozen/src/fpga/common/cpu_system.v')
  top=d/'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/tb_irq.v'
  t=top.read_text();marker='axi_sdram_arbiter sdram_arb (';assert t.count(marker)==1
  wires='\n// Exact LOD5 alias shim; fixed synthetic completed result rows.\n'
  fields={'arvalid':1,'arready':1,'araddr':32,'arlen':8,'rvalid':1,'rready':1,'rdata':32,'rresp':2,'rlast':1,'awvalid':1,'awready':1,'awaddr':32,'awlen':8,'wvalid':1,'wready':1,'wdata':32,'wstrb':4,'wlast':1,'bvalid':1,'bresp':2}
  for n,width in fields.items():wires+=f'wire '+(f'[{width-1}:0] ' if width>1 else '')+f'alias_normal_{n};\n'
  wires+='wire [7:0] alias_view_addr;reg [31:0] alias_view_data;reg [31:0] alias_mem[0:511];\n'
  wires+='initial $readmemh("alias.hex",alias_mem);\nalways @(posedge clk_cpu) alias_view_data<=alias_mem[{alias_view_addr[4:0],alias_view_addr[7:5]}];\n'
  wires+='geometry_bram_alias alias_shim(.clk(clk_cpu),.reset(!reset_n),.cpu_reset(!reset_n),\n'
  wires+=',\n'.join(f'.s_{n}(cpu_sdram_{n}),.m_{n}(alias_normal_{n})' for n in fields)
  wires+=',\n.view_read_addr(alias_view_addr),.view_read_data(alias_view_data),.view_active(),.view_block(1\'b0),.view_valid(1\'b1),.view_count(6\'d32));\n'
  t=t.replace(marker,wires+'\n'+marker)
  start=t.index('    // M1 — CPU (live)');end=t.index('    // M2 —',start)
  t=t[:start]+t[start:end].replace('cpu_sdram_','alias_normal_')+t[end:]
  assert t.count('gpu_core gpu (')==1
  t=t.replace('gpu_core gpu (','gpu_core #(.INCLUDE_CPU_RING(1),.INCLUDE_COMMAND_DMA(0)) gpu (')
  top.write_text(t)
  shutil.copyfile(shim,d/'frozen/src/fpga/common/phase10_bram_alias.v')
  host=d/'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/main.cpp'
  host.write_text((OLD/'main.cpp').read_text().replace('if (uart_buf.size() > 8192) uart_buf.erase(0, 4096);','if (uart_buf.size() > 1048576) uart_buf.erase(0, 524288);'))
  oldcmd=json.loads((Q/'cpu-model/build.json').read_text())['command']
  cmd=[s.replace(str(Q/'cpu-model'),str(d)) for s in oldcmd]
  cmd.insert(-1,'../common/phase10_bram_alias.v')
  save(d/'prepared.json',dict(command=cmd,cpu_sha256=sha(cpu),shim_sha256=sha(shim),source_sha256={str(p.relative_to(d)):sha(p) for p in (d/'frozen').rglob('*') if p.is_file()}))
 # Identical precompiled alias firmware exists, but append a true MMIO-ring test.
 fw=OUT/'firmware';fw.mkdir()
 for n in ['start.S','link.ld','firmware.mif','alias.hex']:shutil.copyfile(OLD/'firmware32'/n,fw/n)
 code=(OLD/'firmware32/kernel.c').read_text()
 assert code.count('int main(void){')==1
 code=code.replace('int main(void){',SUBMIT+'\nint main(void){')
 code=code.replace('text("BRAM_ALIAS_COMPLETE\\nKERNEL PASS HAL init\\n");','submission();text("BRAM_ALIAS_COMPLETE\\nKERNEL PASS HAL init\\n");')
 assert 'submission();text' in code
 (fw/'kernel.c').write_text(code)
 command=[s.replace(str(OLD/'firmware32'),str(fw)) for s in json.loads((OLD/'firmware32.json').read_text())['command']]
 save(OUT/'firmware-command.json',command)
 save(OUT/'prepared.json',dict(scope=__doc__,original_cpu_sha256=sha(BASE/'frozen'/CPU),candidate_cpu_sha256=sha(Q/'VexiiRiscv.v'),shim_sha256=sha(shim),recipe_sha256=sha(Path(__file__)),scope_limits=['Actual selected alias shim and1cycle row store, without accelerator compute or busy handoff.','GPU commands are complete fence streams through actual MMIO/ring/parser, without raster traffic.','Synthetic scanout and cache-state sensitivity are not full SM64 or Quake2 FPS.']))

def model(mode):
 d=OUT/mode;prep=json.loads((d/'prepared.json').read_text())
 for n,h in prep['source_sha256'].items():assert sha(d/n)==h,n
 with (d/'build.log').open('x') as f:subprocess.run(prep['command'],cwd=d/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT,check=True)
 save(d/'build.json',dict(passed=True,simulator_sha256=sha(d/'obj/Vtb_system'),prepared_sha256=sha(d/'prepared.json')))

def firmware():
 f=OUT/'firmware';cmd=json.loads((OUT/'firmware-command.json').read_text())
 with (OUT/'firmware-build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (f/'kernel.asm').open('x') as log:subprocess.run(['llvm-objdump','-d','--mattr=+m,+f,+c',str(f/'kernel.elf')],stdout=log,check=True)
 b=(f/'kernel.elf').read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);segs=[]
 for i in range(h[10]):
  p=struct.unpack_from('<8I',b,h[5]+i*h[9])
  if p[0]==1 and p[5]:segs.append((p[2],p[5],b[p[1]:p[1]+p[4]]))
 start=0x10320000;data=bytearray(max(a+n for a,n,b in segs)-start)
 for a,n,b in segs:assert a>=start;data[a-start:a-start+len(b)]=b
 (f/'kernel.bin').write_bytes(data)
 save(OUT/'firmware.json',dict(command=cmd,files={p.name:sha(p) for p in f.iterdir() if p.is_file()}))

def run(mode,traffic):
 d=OUT/mode;f=OUT/'firmware';exe=d/'obj/Vtb_system'
 assert sha(exe)==json.loads((d/'build.json').read_text())['simulator_sha256']
 for n,h in json.loads((OUT/'firmware.json').read_text())['files'].items():assert sha(f/n)==h,n
 env=dict(SCAN_ENABLE='0' if traffic=='quiet' else '1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
 cmd=[str(exe),'kernel.bin','60000000'];log=d/(traffic+'.log');begin=time.monotonic()
 with log.open('x') as stream:r=subprocess.run(cmd,cwd=f,env=dict(os.environ,**env),stdout=stream,stderr=subprocess.STDOUT,timeout=300)
 s=log.read_text();ok=r.returncode==0 and 'SUBMISSION_COMPLETE' in s and 'BRAM_ALIAS_COMPLETE' in s and 'KERNEL PASS HAL init' in s and 'KERNEL FAIL' not in s and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in s
 uart=s.split('=== UART BEGIN ===')[1].split('=== UART END ===')[0]
 rows=lambda prefix,n:[[int(x,16) for x in m] for m in re.findall(prefix+' '+' '.join(['([0-9a-f]+)']*n),uart)]
 result=dict(passed=ok,mode=mode,traffic=traffic,command=cmd,environment=env,returncode=r.returncode,elapsed_seconds=time.monotonic()-begin,alias=rows('ALIAS_METHOD',8),submission=rows('SUBMIT_METHOD',4),log_sha256=sha(log),firmware_sha256=sha(f/'kernel.bin'))
 save(d/(traffic+'-result.json'),result);print(mode,traffic,ok,len(result['alias']),len(result['submission']));assert ok and len(result['alias'])==192 and len(result['submission'])==12

if __name__=='__main__':globals()[sys.argv[1]](*sys.argv[2:])
