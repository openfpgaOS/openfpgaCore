#!/usr/bin/env python3
"""Private actual-CPU + selected LOD5/physical CRAM/alias integration proof."""
import hashlib,json,os,re,shutil,struct,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'lod5-live-native-v1';OLD=P/'lod5-queue-native-v1';BASE=P/'compact-lod5-core-v2/frozen';PHY=P/'cram1-share-full-v1/frozen'
CPU='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('x')as f:json.dump(d,f,indent=2);f.write('\n')
GEOMETRY=r'''
wire geometry_reg_wr;wire[9:0]geometry_reg_addr;wire[31:0]geometry_reg_wdata,geometry_reg_rdata;wire[3:0]geometry_reg_wstrb;
wire geometry_arvalid,geometry_arready,geometry_rvalid,geometry_rready,geometry_rlast;
wire[31:0]geometry_araddr,geometry_rdata;wire[7:0]geometry_arlen;
wire geometry_view_active,geometry_view_block,geometry_view_valid;wire[5:0]geometry_view_count;
reg[2:0] geometry_startup=0;
always@(posedge clk_cpu)geometry_startup<={geometry_startup[1:0],1'b1};
wire geometry_reset=!geometry_startup[2]||!reset_n;
geometry_load_alias geometry_load(
 .clk(clk_cpu),.reset(geometry_reset),
 .reg_wr(geometry_reg_wr),.reg_addr(geometry_reg_addr),.reg_wdata(geometry_reg_wdata),.reg_wstrb(geometry_reg_wstrb),.reg_rdata(geometry_reg_rdata),
 .m_arvalid(geometry_arvalid),.m_arready(geometry_arready),.m_araddr(geometry_araddr),.m_arlen(geometry_arlen),
 .m_rvalid(geometry_rvalid),.m_rready(geometry_rready),.m_rdata(geometry_rdata),.m_rresp(2'b00),.m_rlast(geometry_rlast),
 .view_read_addr(alias_view_addr),.view_read_data(alias_view_data),.view_active(geometry_view_active),.view_block(geometry_view_block),.view_valid(geometry_view_valid),.view_count(geometry_view_count));
'''
def physical():
 t=(PHY/'phase10_cram1_share_tb.v').read_text()
 a=t.index('    // ---- adapter <-> controller burst ----');b=t.index('    reg [1:0] arvalid=0;',a)
 s=t[a:b].replace('reg         word_rd = 0, word_wr = 0;','wire word_rd=0,word_wr;').replace('reg  [21:0] word_addr = 0; reg [31:0] word_data = 0; reg [3:0] word_wstrb = 4\'hF;','wire[21:0]word_addr;wire[31:0]word_data;wire[3:0]word_wstrb=15;')
 a=t.index('    gpu_cram1_tex_adapter adapter (');b=t.index('\n\n    integer cycles=',a)
 s+=t[a:b]
 s=s.replace('.clk(clk)', '.clk(clk_cpu)').replace('posedge clk ', 'posedge clk_cpu ').replace('EXTRA_REFRESH_CYCLES','3')
 # Expose ordinary selected GPU upload; physical chip starts uninitialized.
 s+='''
wire tex_arvalid,tex_arready,tex_rvalid,tex_rlast;
wire[31:0]tex_araddr,tex_rdata;wire[7:0]tex_arlen;
wire arvalid_m,arready_m,rvalid_m,rlast_m;wire[31:0]araddr_m,rdata_m;wire[7:0]arlen_m;
geometry_cram1_read_arbiter cram_arbiter(
 .clk(clk_cpu),.reset_n(reset_n),.s0_reset(!reset_n),
 .s0_arvalid(tex_arvalid),.s0_arready(tex_arready),.s0_araddr(tex_araddr),.s0_arlen(tex_arlen),.s0_rvalid(tex_rvalid),.s0_rdata(tex_rdata),.s0_rlast(tex_rlast),
 .s1_arvalid(geometry_arvalid&&bcr_st==B_DONE),.s1_arready(geometry_arready),.s1_araddr(geometry_araddr),.s1_arlen(geometry_arlen),.s1_rvalid(geometry_rvalid),.s1_rdata(geometry_rdata),.s1_rlast(geometry_rlast),
 .m_arvalid(arvalid_m),.m_arready(arready_m),.m_araddr(araddr_m),.m_arlen(arlen_m),.m_rvalid(rvalid_m),.m_rdata(rdata_m),.m_rlast(rlast_m));
integer native_upload_words=0,native_load_bursts=0,native_alias_beats=0,native_alias_stalls=0;
always@(posedge clk_cpu)if(reset_n)begin
 if(word_wr)native_upload_words<=native_upload_words+1;
 if(geometry_arvalid&&geometry_arready)native_load_bursts<=native_load_bursts+1;
 if(alias_shim.s_rvalid&&alias_shim.s_rready&&alias_shim.view_active)native_alias_beats<=native_alias_beats+1;
 if(alias_shim.s_rvalid&&!alias_shim.s_rready&&alias_shim.view_active)native_alias_stalls<=native_alias_stalls+1;
 if(cram_errors)$fatal(1,"physical CRAM timing/error");
 if(geometry_load.raw_start&&geometry_view_active)$fatal(1,"LOAD overwrote active view");
 if(geometry_reg_wr&&geometry_reg_addr==2&&geometry_reg_wdata[0]&&(word_busy||gpu.gpu_tex_mem_up_inflight))$fatal(1,"LOAD before source upload drained");
 if(geometry_rvalid&&!geometry_rready)$fatal(1,"CRAM response cannot be backpressured");
end
'''
 return s

def prepare():
 OUT.mkdir(exist_ok=True)
 for mode in ('control','queues'):
  d=OUT/mode;d.mkdir(exist_ok=False);shutil.copytree(OLD/mode/'frozen',d/'frozen')
  for rel in ['src/fpga/common','src/fpga/targets/pocket']:
   for src in (BASE/rel).rglob('*'):
    if src.is_file():dst=d/'frozen'/src.relative_to(BASE);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst)
  # Control uses original CPU and its original ID adapter, candidate selected compact CPU.
  if mode=='queues':shutil.copyfile(BASE/CPU,d/'frozen'/CPU)
  else:shutil.copyfile(OLD/mode/'frozen/src/fpga/common/cpu_system.v',d/'frozen/src/fpga/common/cpu_system.v')
  top=d/'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/tb_irq.v';t=top.read_text()
  a=t.index('wire [7:0] alias_view_addr;');b=t.index('geometry_bram_alias alias_shim',a)
  t=t[:a]+'wire[7:0]alias_view_addr;wire[31:0]alias_view_data;\n'+GEOMETRY+physical()+t[b:]
  t=t.replace(".view_active(),.view_block(1'b0),.view_valid(1'b1),.view_count(6'd32)",'.view_active(geometry_view_active),.view_block(geometry_view_block),.view_valid(geometry_view_valid),.view_count(geometry_view_count)')
  t=t.replace('axi_periph_slave periph (','axi_periph_slave periph (\n.geometry_reg_wr(geometry_reg_wr),.geometry_reg_addr(geometry_reg_addr),.geometry_reg_wdata(geometry_reg_wdata),.geometry_reg_wstrb(geometry_reg_wstrb),.geometry_reg_rdata(geometry_reg_rdata),')
  t=t.replace('gpu_core #(.INCLUDE_CPU_RING(1),.INCLUDE_COMMAND_DMA(0)) gpu (','gpu_core #(.INCLUDE_CPU_RING(1),.INCLUDE_COMMAND_DMA(0)) gpu (\n.gpu_tex_mem_arvalid(tex_arvalid),.gpu_tex_mem_arready(tex_arready),.gpu_tex_mem_araddr(tex_araddr),.gpu_tex_mem_arlen(tex_arlen),.gpu_tex_mem_rvalid(tex_rvalid),.gpu_tex_mem_rdata(tex_rdata),.gpu_tex_mem_rlast(tex_rlast),\n.gpu_tex_mem_up_wr(word_wr),.gpu_tex_mem_up_addr(word_addr),.gpu_tex_mem_up_wdata(word_data),.gpu_tex_mem_up_busy(word_busy),')
  top.write_text(t)
  chip=next(PHY.rglob('cram_chip_model.v'));shutil.copyfile(chip,d/'frozen/src/fpga/test/cram_chip_model.v')
  host=d/'frozen/docs/measurements/os30-dual-20261004/validation/baseline128/main.cpp';s=host.read_text()
  s=re.sub(r'    \{std::ifstream f\("alias.hex"\).*?\}\n','',s)
  # Dynamic alias data is checked by unchanged native client + independent firmware golden fields.
  s=s.replace('alias_image[(beat_addr-0x13f80000u)/4]:model_peek((beat_addr', 'data:model_peek((beat_addr').replace('alias_image[(beat_addr-0x13f80000u)/4]:model_peek(widx)', 'tb->dbg_m1_rdata:model_peek(widx)')
  host.write_text(s)
  cmd=[s.replace(str(OLD/mode),str(d)) for s in json.loads((OLD/mode/'prepared.json').read_text())['command']]
  cmd.insert(cmd.index('--cc'),'-DINCLUDE_SPAN_LOOKAHEAD');cmd.insert(cmd.index('--cc'),'-DINCLUDE_SPAN_DEDICATED_MULT')
  names=['phase10_load_alias.v','phase10_float_recip2.v','phase10_raw_vtx_dma.v','GeometryFmaProbe.v','geometry_lighting_batch.v','geometry_color_rtz.v','geometry_float_batch_recip.v','geometry_transform_light.v','geometry_raw_load.v','phase10_cram1_read_arbiter.v']
  for n in names:cmd.insert(-1,'../common/'+n)
  for n in ['cram1_controller.v','cram1_phy.sv','gpu_cram1_tex_adapter.v']:cmd.insert(-1,'../targets/pocket/'+n)
  cmd.insert(-1,'cram_chip_model.v')
  shutil.copyfile(PHY/'altddio_out_stub.v',d/'frozen/src/fpga/test/altddio_out_stub.v');cmd.insert(-1,'altddio_out_stub.v')
  save(d/'prepared.json',dict(command=cmd,source_sha256={str(p.relative_to(d)):sha(p)for p in (d/'frozen').rglob('*')if p.is_file()}))
 save(OUT/'prepared.json',dict(recipe_sha256=sha(Path(__file__)),base=str(BASE.relative_to(ROOT)),scope=__doc__,limits=['Dynamic alias response correctness checked against native expected vertex fields in firmware; CPU bus checker independently checks all non-alias SDRAM data and response IDs.','Isolated unchanged client helper, not whole game or physical timing closure.']))

def model(mode):
 d=OUT/mode;p=json.loads((d/'prepared.json').read_text())
 with(d/'build.log').open('x')as f:r=subprocess.run(p['command'],cwd=d/'frozen/src/fpga/test',stdout=f,stderr=subprocess.STDOUT)
 save(d/'build.json',dict(passed=r.returncode==0,returncode=r.returncode,prepared_sha256=sha(d/'prepared.json'),simulator_sha256=sha(d/'obj/Vtb_system')if(d/'obj/Vtb_system').exists()else None));assert r.returncode==0
def firmware_image():
 f=OUT/'firmware';app=P/'load-cram-driver-v1/app.elf'
 def segments(p):
  b=p.read_bytes();h=struct.unpack_from('<16sHHIIIIIHHHHHH',b);ss=[]
  for i in range(h[10]):
   q=struct.unpack_from('<8I',b,h[5]+i*h[9])
   if q[0]==1 and q[5]:ss.append((q[2],q[5],b[q[1]:q[1]+q[4]]))
  return ss
 appsegs=segments(app);im=bytearray(max(a+n for a,n,b in appsegs)-0x10320000)
 for a,n,b in appsegs:im[a-0x10320000:a-0x10320000+len(b)]=b
 for a,n,b in segments(f/'kernel.elf'):
  assert a<0x10400000 or 0x10c00000<=a and a+n<=0x10c10000
  im[a-0x10320000:a-0x10320000+len(b)]=b
 (f/'kernel.bin').write_bytes(im)
 bound=[]
 for a,n in [(0x104cf140,0x37a),(0x104cf0c0,0x5c),(0x104d9840,0x3b8)]:
  orig=next(b[a-x:a-x+n]for x,size,b in appsegs if x<=a and a+n<=x+len(b));assert im[a-0x10320000:a-0x10320000+n]==orig
  bound.append(dict(address=a,size=n,instruction_sha256=hashlib.sha256(orig).hexdigest()))
 out=dict(app_sha256=sha(app),firmware_sha256=sha(f/'kernel.bin'),unchanged_functions=bound,source_asset_window=[0x10c00000,0x10c10000],limits=['Private source fixture replaces unused asset bytes inside immutable ELF bounds; called machine code and helper literals remain unchanged.'])
 (OUT/'firmware.json').write_text(json.dumps(out,indent=2)+'\n')

def run(mode,traffic):
 d=OUT/mode;f=OUT/'firmware';exe=d/'obj/Vtb_system'
 env=dict(SCAN_ENABLE='0' if traffic=='quiet' else '1',SCAN_PHASE='317',STRIKE_SCAN_PERIOD='1000000000',SCAN_BASE_HW='0x180000',SCAN_PERIOD='6361',SCAN_LEN='160',SCAN_ACTIVE='240',SCAN_TOTAL='262')
 cmd=[str(exe),'kernel.bin','15000000'];begin=time.monotonic()
 with(d/(traffic+'.log')).open('x')as stream:r=subprocess.run(cmd,cwd=f,env=dict(os.environ,**env),stdout=stream,stderr=subprocess.STDOUT,timeout=300)
 log=d/(traffic+'.log');s=log.read_text();ok=r.returncode==0 and 'LIVE_LOD5_COMPLETE' in s and 'KERNEL FAIL' not in s and 'WCONF: mism=0 spurious=0 exp_fifo=0 aw_fifo=0 run_left=0' in s
 result=dict(passed=ok,returncode=r.returncode,command=cmd,environment=env,elapsed_seconds=time.monotonic()-begin,log_sha256=sha(log),firmware_sha256=sha(f/'kernel.bin'))
 save(d/(traffic+'-result.json'),result);print(json.dumps(result));assert ok
if __name__=='__main__':globals()[sys.argv[1]](*sys.argv[2:])
