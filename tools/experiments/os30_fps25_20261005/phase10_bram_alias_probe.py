#!/usr/bin/env python3
"""Private retained-CPU M1 cacheable BRAM result handoff microbenchmark."""
import json,hashlib,struct,subprocess,shutil,sys,os,time,re
from pathlib import Path
from phase10_experiment import CAMP,ROOT,write,sha
OUT=CAMP/'phase10/bram-alias-native-v1'
NATIVE=CAMP/'phase10/cpu-audit';BASE=ROOT/'docs/measurements/os30-dual-20261004/validation/baseline128'
ALIAS=r'''
// Private read-only 2KiB result alias, ahead of M1 SDRAM arbitration.
// One cycle synchronous BRAM response; one accepted read burst at a time.
wire normal_arready,normal_rvalid,normal_rlast;
wire [31:0] normal_rdata;wire [1:0] normal_rresp;
reg read_active,read_alias;
reg alias_rvalid;reg [31:0] alias_rdata;reg [8:0] alias_index;
reg [7:0] alias_remaining;
reg [31:0] alias_mem[0:511];
wire alias_select=cpu_sdram_araddr[31:11]==21'h27f00;
initial $readmemh("alias.hex",alias_mem);
assign cpu_sdram_arready=!read_active&&(alias_select?1'b1:normal_arready);
assign cpu_sdram_rvalid=read_active&&(read_alias?alias_rvalid:normal_rvalid);
assign cpu_sdram_rdata=read_alias?alias_rdata:normal_rdata;
assign cpu_sdram_rresp=read_alias?2'b00:normal_rresp;
assign cpu_sdram_rlast=read_alias?(alias_remaining==0):normal_rlast;
always @(posedge clk_cpu or negedge reset_n) begin
 if(!reset_n)begin read_active<=0;read_alias<=0;alias_rvalid<=0;alias_index<=0;alias_remaining<=0;alias_rdata<=0;end
 else begin
  if(cpu_sdram_arvalid&&cpu_sdram_arready)begin
   read_active<=1;read_alias<=alias_select;
   if(alias_select)begin
    if(cpu_sdram_araddr[1:0]!=0 || ({1'b0,cpu_sdram_araddr[10:2]}+cpu_sdram_arlen)>511)$fatal(1,"Alias burst out of window");
    alias_index<=cpu_sdram_araddr[10:2];alias_remaining<=cpu_sdram_arlen;
    alias_rdata<=alias_mem[cpu_sdram_araddr[10:2]];alias_rvalid<=1;
   end
  end
  if(cpu_sdram_rvalid&&cpu_sdram_rready)begin
   if(cpu_sdram_rlast)begin read_active<=0;alias_rvalid<=0;end
   else if(read_alias)begin alias_index<=alias_index+1'b1;alias_remaining<=alias_remaining-1'b1;alias_rdata<=alias_mem[alias_index+1'b1];end
  end
  if(cpu_sdram_awvalid&&cpu_sdram_awready&&cpu_sdram_awaddr[31:11]==21'h27f00)$fatal(1,"Read-only alias write");
 end
end
'''
KERNEL=r'''
#include <stdint.h>
#include <string.h>
volatile unsigned traps,irq_count;
static void put(char c){while(!(*(volatile unsigned*)0x4f000000u&2)){}*(volatile unsigned*)0x4f000004u=(unsigned char)c;}
static void text(const char*s){while(*s)put(*s++);}
static void hex(unsigned v){for(unsigned i=0;i<8;i++)put("0123456789abcdef"[(v>>(28-4*i))&15]);put(' ');}
static unsigned cycle(void){return *(volatile unsigned*)0x40000004u;}
static void fence(void){__asm__ volatile("fence":::"memory");}
static void inval(void*p,unsigned bytes){uintptr_t end=(uintptr_t)p+bytes,a=(uintptr_t)p&~63u;fence();for(;a<end;a+=64)__asm__ volatile(".insn i 0x0F,2,x0,%0,0"::"r"(a):"memory");fence();}
static void fail(unsigned i,unsigned got,unsigned expected){text("KERNEL FAIL ");hex(i);hex(got);hex(expected);put('\n');for(;;)__asm__ volatile("wfi");}
static uint32_t bits(float f){uint32_t u;memcpy(&u,&f,4);return u;}
static int16_t uv(unsigned v,unsigned axis){const int16_t edge[4]={-32768,-1,0,32767};return v<4?edge[(v+axis)&3]:(int16_t)(v*2137u+axis*13007u);}
static uint32_t expected(unsigned v,unsigned w){
 if(w<5)return 0x3f000000u+v*0x10000u+w*0x1000u;
 if(w==5)return bits((float)uv(v,0));if(w==6)return bits((float)uv(v,1));
 if(w==7)return 0x80112233u+v*0x0103070bu;return v&63u;
}
__attribute__((noinline)) static void copy32(uint32_t*restrict d,const uint32_t*restrict s,unsigned n){
 for(unsigned v=0;v<n;v++,s+=8,d+=9){
  d[0]=s[0];d[1]=s[1];d[2]=s[2];d[3]=s[3];d[4]=s[4];
  uint32_t uv=s[5];float u=(float)(int16_t)uv,t=(float)(int16_t)(uv>>16);
  memcpy(d+5,&u,4);memcpy(d+6,&t,4);d[7]=s[6];d[8]=s[7];
 }
}
__attribute__((noinline)) static void copy64(uint32_t*restrict d,const uint32_t*restrict s,unsigned n){
 for(unsigned v=0;v<n;v++,s+=16,d+=9){d[0]=s[0];d[1]=s[1];d[2]=s[2];d[3]=s[3];d[4]=s[4];d[5]=s[5];d[6]=s[6];d[7]=s[7];d[8]=s[8];}
}
#if ALIAS_LAYOUT==32
#define COPY copy32
#else
#define COPY copy64
#endif
static void check(const uint32_t*d,unsigned n){for(unsigned v=0;v<n;v++)for(unsigned w=0;w<9;w++){unsigned want=expected(v,w);if(d[v*9+w]!=want)fail(v*9+w,d[v*9+w],want);}}
int main(void){
 const uint32_t *alias=(const uint32_t*)0x13f80000u,*sdram=(const uint32_t*)0x13040000u;
 volatile uint32_t*uncached=(volatile uint32_t*)0x53040000u;uint32_t*target=(uint32_t*)0x13048080u;
 text("BRAM_ALIAS_BEGIN ");hex(ALIAS_LAYOUT);put('\n');
 inval((void*)sdram,2048);
 for(unsigned v=0;v<32;v++)for(unsigned w=0;w<ALIAS_LAYOUT/4;w++){
  uint32_t value;
  if(ALIAS_LAYOUT==64)value=w<9?expected(v,w):0;
  else value=w<5?expected(v,w):w==5?(uint16_t)uv(v,0)|((uint32_t)(uint16_t)uv(v,1)<<16):expected(v,w+1);
  uncached[v*(ALIAS_LAYOUT/4)+w]=value;
 }
 fence();inval((void*)sdram,2048);inval((void*)alias,2048);
 for(unsigned kind=0;kind<2;kind++)for(unsigned n=1;n<=32;n++)for(unsigned layout=0;layout<3;layout++){
  unsigned dest=layout==0?0:layout==1?32-n:(32-n)/2;const uint32_t*source=kind?alias:sdram;uint32_t*d=target+dest*9;
  unsigned inv=0,cold=0,warm=0;
  COPY(d,source,n);check(d,n);
  for(unsigned repeat=0;repeat<8;repeat++){
   unsigned a=cycle();COPY(d,source,n);fence();unsigned b=cycle();warm+=b-a;
   a=cycle();inval((void*)source,n*ALIAS_LAYOUT);b=cycle();COPY(d,source,n);fence();unsigned c=cycle();inv+=b-a;cold+=c-b;
   check(d,n);
  }
  text("ALIAS_METHOD ");hex(ALIAS_LAYOUT);hex(kind);hex(n);hex(dest);hex(8);hex(inv);hex(cold);hex(warm);put('\n');
 }
 text("BRAM_ALIAS_COMPLETE\nKERNEL PASS HAL init\n");return 0;
}
'''
def prepare():
 OUT.mkdir(exist_ok=False)
 tb=(BASE/'tb_irq.v').read_text();anchor='axi_sdram_arbiter sdram_arb (';assert tb.count(anchor)==1;tb=tb.replace(anchor,ALIAS+'\n'+anchor)
 start=tb.index('    // M1 — CPU (live)');end=tb.index('    // M2 —',start)
 block=tb[start:end]
 for name in ('arready','rvalid','rdata','rresp','rlast'):block=block.replace('cpu_sdram_'+name,'normal_'+name)
 block=block.replace('.m1_arvalid(cpu_sdram_arvalid)','.m1_arvalid(cpu_sdram_arvalid&&!read_active&&!alias_select)')
 block=block.replace('.m1_rready (cpu_sdram_rready)','.m1_rready (cpu_sdram_rready&&read_active&&!read_alias)')
 tb=tb[:start]+block+tb[end:];(OUT/'tb_alias.v').write_text(tb)
 host=(NATIVE/'main.cpp').read_text();host=host.replace('static Vtb_system *tb;','static Vtb_system *tb;\nstatic std::vector<uint32_t> alias_image;')
 anchor='    tb = new Vtb_system;';host=host.replace(anchor,'''    {std::ifstream f("alias.hex");std::string line;while(std::getline(f,line))alias_image.push_back(std::stoul(line,nullptr,16));if(alias_image.size()!=512)return 9;}
'''+anchor)
 anchor='                uint32_t expect = model_peek(widx);';assert host.count(anchor)==1
 host=host.replace(anchor,'''                uint32_t expect = (beat_addr>=0x13f80000u&&beat_addr<0x13f80800u)?alias_image[(beat_addr-0x13f80000u)/4]:model_peek(widx);''')
 anchor='            uint32_t expect = model_peek((beat_addr >> 2) & 0xFFFFFF);';assert host.count(anchor)==1
 host=host.replace(anchor,'''            uint32_t expect = (beat_addr>=0x13f80000u&&beat_addr<0x13f80800u)?alias_image[(beat_addr-0x13f80000u)/4]:model_peek((beat_addr >> 2) & 0xFFFFFF);''')
 (OUT/'main.cpp').write_text(host);(OUT/'kernel.c').write_text(KERNEL)
 mk=(BASE/'test.mk').read_text().replace(str(BASE/'tb_irq.v'),str(OUT/'tb_alias.v'))
 (OUT/'test.mk').write_text(mk)
 write(OUT/'prepared.json',dict(scope=__doc__,private_only=True,parent_cpu_sha256=sha(ROOT/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'),cpu_expected='33895e9a2fc141d8da49bb75e082e06964a49e328b6d87363516ac6f9ab26119',sources={n:sha(OUT/n) for n in ['tb_alias.v','main.cpp','kernel.c','test.mk']},recipe_sha256=sha(Path(__file__))))
def build_model():
 cmd=['make','-f',str(OUT/'test.mk'),str(OUT/'obj/Vtb_system'),'SYSTEM_SDRAM_DIR='+str(OUT/'obj'),'SYSTEM_SDRAM_CPP='+str(OUT/'main.cpp'),'VEXII_MISTER='+str(ROOT/'src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'),'VERILATOR=verilator -j 3']
 with (OUT/'build-model.log').open('x') as f:subprocess.run(cmd,cwd=ROOT/'src/fpga/test',stdout=f,stderr=subprocess.STDOUT,check=True)
 write(OUT/'build-model.json',dict(command=cmd,simulator_sha256=sha(OUT/'obj/Vtb_system')))
def firmware():
 old=NATIVE/'dma-native-v4';manifest=json.loads((old/'manifest.json').read_text())
 for layout in (32,64):
  f=OUT/f'firmware{layout}';f.mkdir(exist_ok=False)
  for n in ['start.S','link.ld','firmware.mif']:shutil.copyfile(old/'firmware'/n,f/n)
  (f/'kernel.c').write_text(f'#define ALIAS_LAYOUT {layout}\n'+KERNEL)
  cmd=[t.replace(str(old/'firmware'),str(f)) for t in manifest['compiler_command']]
  with (OUT/f'build-firmware{layout}.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
  with (f/'kernel.asm').open('x') as log:subprocess.run(['llvm-objdump','-d','--mattr=+m,+f,+c',str(f/'kernel.elf')],stdout=log,check=True)
  blob=(f/'kernel.elf').read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',blob);segs=[]
  for i in range(h[10]):
   ph=struct.unpack_from('<8I',blob,h[5]+i*h[9])
   if ph[0]==1 and ph[5]:segs.append((ph[2],ph[5],blob[ph[1]:ph[1]+ph[4]]))
  start=0x10320000;data=bytearray(max(a+n for a,n,d in segs)-start)
  for a,n,d in segs:assert a>=start;data[a-start:a-start+len(d)]=d
  (f/'kernel.bin').write_bytes(data)
  def uv(v,a):
   return [-32768,-1,0,32767][(v+a)&3] if v<4 else ((v*2137+a*13007+32768)%65536)-32768
  def expected(v,w):
   if w<5:return 0x3f000000+v*0x10000+w*0x1000
   if w in (5,6):return struct.unpack('<I',struct.pack('<f',float(uv(v,w-5))))[0]
   return ((0x80112233+v*0x0103070b)&0xffffffff) if w==7 else v&63
  values=[]
  for v in range(32):
   for w in range(layout//4):values.append(expected(v,w) if layout==64 and w<9 else 0 if layout==64 else expected(v,w) if w<5 else ((uv(v,0)&65535)|((uv(v,1)&65535)<<16)) if w==5 else expected(v,w+1))
  values += [0]*(512-len(values));(f/'alias.hex').write_text(''.join(f'{x:08x}\n' for x in values))
  write(OUT/f'firmware{layout}.json',dict(command=cmd,files={p.name:sha(p) for p in f.iterdir()},scope='Native copy functions: layout32 includes exactly two signed-int16 to float conversions per vertex; layout64 copies9existingfloat/word fields.'))
def run():
 for layout in (32,64):
  for mode in ('quiet','nominal'):
   cmd=[str(OUT/'obj/Vtb_system'),'kernel.bin','30000000'];env=dict(SCAN_ENABLE='0' if mode=='quiet' else '1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
   log=OUT/f'{layout}-{mode}.log';begin=time.monotonic()
   with log.open('x') as f:r=subprocess.run(cmd,cwd=OUT/f'firmware{layout}',env=dict(os.environ,**env),stdout=f,stderr=subprocess.STDOUT,timeout=300)
   text=log.read_text();ok=r.returncode==0 and 'BRAM_ALIAS_COMPLETE' in text and 'KERNEL PASS HAL init' in text and 'KERNEL FAIL' not in text and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in text
   write(OUT/f'{layout}-{mode}.json',dict(passed=ok,command=cmd,environment=env,returncode=r.returncode,elapsed_seconds=time.monotonic()-begin,log_sha256=sha(log)));assert ok
   print(layout,mode,'passed',flush=True)
if __name__=='__main__':
 for action in sys.argv[1:]:globals()[action]()
