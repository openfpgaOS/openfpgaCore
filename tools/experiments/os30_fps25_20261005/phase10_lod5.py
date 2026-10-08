#!/usr/bin/env python3
"""Private LOD5 CRAM source and span-v3 integration; no production edits."""
import argparse,difflib,hashlib,json,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'load-bram-core-area-v1'
COMP=P/'lod5-components-v1'
CORE=P/'lod5-cram-span-core-v1'
TOP='src/fpga/targets/pocket/core_top.v'
QSF='src/fpga/targets/pocket/ap_core.qsf'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):
 assert s.count(a)==1,(a[:90],s.count(a));return s.replace(a,b)
def prepare():
 old=P/'load-bram-alias-v3';f=COMP/'frozen';f.mkdir(parents=True,exist_ok=False)
 sources=json.loads((old/'sources.json').read_text())
 for n,v in sources.items():
  assert sha(old/n)==v['sha256'],n
  shutil.copy2(old/n,f/n)
 p=f/'phase10_load_alias.v';s=p.read_text().replace('Private LOD4:','Private LOD5:')
 s=rep(s,"32'h4c4f4404","32'h4c4f4405")
 s=rep(s,'geometry_raw_load #(.CAPTURE_CONTEXT(0),.ENABLE_INVALIDATE(0)) load(',
  "geometry_raw_load #(.CAPTURE_CONTEXT(0),.ENABLE_INVALIDATE(0),\n  .SOURCE_MIN_ADDRESS(32'h00000000),.SOURCE_END_ADDRESS(33'h001000000)) load(")
 p.write_text(s)
 p=f/'geometry_raw_load.v';s=p.read_text()
 s=rep(s,'parameter EXTERNAL_DMA=0) (',"parameter EXTERNAL_DMA=0,\n parameter [31:0] SOURCE_MIN_ADDRESS=32'h10000000,\n parameter [32:0] SOURCE_END_ADDRESS=33'h014000000) (")
 s=rep(s,' raw_vtx_dma dma(', ' raw_vtx_dma #(.MIN_ADDRESS(SOURCE_MIN_ADDRESS),.END_ADDRESS(SOURCE_END_ADDRESS)) dma(')
 p.write_text(s)
 s=(P/'cram1-share-v1/frozen/phase10_cram1_read_arbiter.v').read_text()
 s=rep(s,' input wire clk,reset_n,',' input wire clk,reset_n,\n // Texture-client reset may cancel only its unaccepted request.\n input wire s0_reset,')
 s=rep(s,' reg owner,prefer_one;', ' reg owner,prefer_one,discard_s0;\n wire s0_valid=s0_arvalid && !s0_reset;')
 s=s.replace('(!s0_arvalid || prefer_one)','(!s0_valid || prefer_one)').replace('selected ? s1_arvalid : s0_arvalid','selected ? s1_arvalid : s0_valid')
 s=rep(s,'!selected && m_arready;','!selected && m_arready && !s0_reset;')
 s=rep(s,'!owner && m_rvalid;','!owner && m_rvalid && !discard_s0 && !s0_reset;')
 s=rep(s,'state<=IDLE;owner<=0;prefer_one<=0;', 'state<=IDLE;owner<=0;prefer_one<=0;discard_s0<=0;')
 s=rep(s,'  else case(state)','''  else begin
   if(state!=IDLE && !owner && s0_reset)discard_s0<=1;
   case(state)''')
 s=rep(s,'    owner<=selected;','    owner<=selected;discard_s0<=0;')
 s=rep(s,'   ADDRESS:if(m_arvalid && m_arready)begin state<=RESPONSE;prefer_one<=!owner;end', '''   ADDRESS:begin
    // Adapter ARREADY acknowledges its previous-cycle capture. Even if the
    // texture client reset meanwhile, the accepted physical burst must drain.
    if(m_arready)begin state<=RESPONSE;prefer_one<=!owner;end
    else if(!owner && s0_reset)state<=IDLE;
   end''')
 s=rep(s,'  endcase\n end','  endcase\n  end\n end')
 (f/'phase10_cram1_read_arbiter.v').write_text(s)
 p=f/'phase10_load_alias_test.cpp';s=p.read_text().replace('0x4c4f4404','0x4c4f4405')
 s=rep(s,'jobs.push_back(j);','j.h[1]=0x00fc0000+i*4096;jobs.push_back(j);')
 s=rep(s,'j.h[1]=0x10000fc0;','j.h[1]=0x00fc0fc0;')
 s=rep(s,' bool ar=t.dma_arvalid&&t.dma_arready,', ' if(t.dma_rvalid)need(t.dma_rready,"CRAM response backpressured");\n bool ar=t.dma_arvalid&&t.dma_arready,')
 s=rep(s,' // Keep old output burst stalled while the next complete job is requested.', ''' // Physical CRAM offsets: both ends, both dies,4KiB and top-pool boundaries.
 for(auto address:{0u,0x003c0000u,0x007ffffcu,0x00800000u,0x00fc0000u,0x00ffffe8u}){
  Job j=jobs[0];j.h[0]=1;j.h[1]=address;j.d.resize(15);configure(j);put(8,1);done();check(j);
 }
 unsigned rejected=0;
 for(auto address:{1u,0x00ffffe9u,0x00ffffecu,0x01000000u,0x10000000u,0xfffffffcu}){
  Job j=jobs[0];j.h[0]=1;configure(j);unsigned oldbursts=dma_bursts;
  put(0xc,address);put(8,1);done();need((reg(4)&0x60)==0x20&&!(reg(4)&4),"CRAM range status");
  need(dma_bursts==oldbursts,"invalid CRAM source issued AR");rejected++;
 }
 // Keep old output burst stalled while the next complete job is requested.''')
 s=rep(s,' std::printf("{\\"passed\\":true,', ''' // Fabric reset is permitted to cancel only with the external responder reset.
 configure(jobs[0]);put(8,1);for(unsigned i=0;i<1000&&!dma_active;i++)tick();
 need(dma_active,"fabric reset test lacks accepted burst");reset();
 need(reg(4)==0,"fabric reset preserved source owner");configure(jobs[0]);put(8,1);done();check(jobs[0]);
 need(rejected==6,"range coverage");
 std::printf("{\\"passed\\":true,''')
 p.write_text(s)
 shutil.copy2(__file__,COMP/'recipe.py')
 save(COMP/'sources.json',{n:sha(f/n) for n in sorted(x.name for x in f.iterdir())})
 print(COMP)
def test():
 f=COMP/'frozen';d=COMP/'wrapper';d.mkdir(exist_ok=False)
 cmd=['verilator','--cc','--exe','--build','-j','4','-Wno-fatal','-Wno-WIDTH','--top-module','tb_load_alias','--Mdir',str(d/'obj'),
  '-CFLAGS','-O2 -std=c++17']+[str(x) for x in sorted(f.glob('*.v'))]+[str(f/'phase10_load_alias_test.cpp')]
 save(d/'build-command.json',cmd)
 with (d/'build.log').open('x') as log:subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (d/'run.log').open('x') as log:subprocess.run([str(d/'obj/Vtb_load_alias'),str(f/'native.bin')],stdout=log,stderr=subprocess.STDOUT,check=True)
 result=json.loads((d/'run.log').read_text().splitlines()[-1]);assert result['passed']
 result.update(source_id='0x4c4f4405',physical_source_range=[0,0x1000000],invalid_source_cases=6,
  tested_valid_offsets=[0,0x3c0000,0x7ffffc,0x800000,0xfc0000,0xffffe8],no_response_backpressure=True,
  source_sha256={n:sha(f/n) for n in json.loads((COMP/'sources.json').read_text())})
 save(d/'result.json',result);print(result)
def top_patch(s):
 s=rep(s,'wire geometry_reset = !geometry_startup[2] || !sdram_cpu_reset_n;',
  'wire geometry_reset = !geometry_startup[2] || !cram1_reset_n;')
 s=rep(s,'// Private LOD4 results:','// Private LOD5 results:')
 marker='gpu_cram1_tex_adapter cram1_tex_adapter (\n'
 new='''// LOD5 immutable vertex reads share the existing physical CRAM controller.
// Accepted texture reads drain through media reset; LOAD resets only with
// the CRAM fabric. Both clients consume every returned word without stalls.
wire c1_shared_arvalid,c1_shared_arready,c1_shared_rvalid,c1_shared_rlast;
wire [31:0] c1_shared_araddr,c1_shared_rdata;
wire [7:0] c1_shared_arlen;
geometry_cram1_read_arbiter geometry_cram_reader (
 .clk(clk_cpu),.reset_n(cram1_reset_n),.s0_reset(!reset_n_cpu_media),
 .s0_arvalid(gpu_tex_arvalid),.s0_arready(gpu_tex_arready),
 .s0_araddr(gpu_tex_araddr),.s0_arlen(gpu_tex_arlen),
 .s0_rvalid(gpu_tex_rvalid),.s0_rdata(gpu_tex_rdata),.s0_rlast(gpu_tex_rlast),
 .s1_arvalid(geometry_arvalid && cram1_bcr_state==C1_BCR_DONE),.s1_arready(geometry_arready),
 .s1_araddr(geometry_araddr),.s1_arlen(geometry_arlen),
 .s1_rvalid(geometry_rvalid),.s1_rdata(geometry_rdata),.s1_rlast(geometry_rlast),
 .m_arvalid(c1_shared_arvalid),.m_arready(c1_shared_arready),
 .m_araddr(c1_shared_araddr),.m_arlen(c1_shared_arlen),
 .m_rvalid(c1_shared_rvalid),.m_rdata(c1_shared_rdata),.m_rlast(c1_shared_rlast));
assign geometry_rresp=2'b00;

gpu_cram1_tex_adapter cram1_tex_adapter (
'''
 s=rep(s,marker,new)
 a=s.index(marker);b=s.index('\n);',a)
 t=s[a:b].replace('gpu_tex_','c1_shared_');s=s[:a]+t+s[b:]
 s=rep(s,'''        // M2: private raw vertex reads. All write channels remain tied idle.
        .m2_arvalid(geometry_arvalid), .m2_arready(geometry_arready),
        .m2_araddr(geometry_araddr), .m2_arlen(geometry_arlen),
        .m2_rvalid(geometry_rvalid), .m2_rdata(geometry_rdata), .m2_rresp(geometry_rresp), .m2_rlast(geometry_rlast),
        .m2_rready(geometry_rready),''','''        // LOD5 source reads use CRAM1; M2 is again constant idle.
        .m2_arvalid(1'b0), .m2_arready(),
        .m2_araddr(32'd0), .m2_arlen(8'd0),
        .m2_rvalid(), .m2_rdata(), .m2_rresp(), .m2_rlast(),
        .m2_rready(1'b1),''')
 return s
def stage():
 assert json.loads((COMP/'wrapper/result.json').read_text())['passed']
 expected=json.loads((BASE/'prepared.json').read_text())['source_sha256']
 for n,h in expected.items():assert sha(BASE/'frozen'/n)==h,n
 CORE.mkdir(exist_ok=False);f=CORE/'frozen';shutil.copytree(BASE/'frozen',f)
 for n in ('phase10_load_alias.v','geometry_raw_load.v','phase10_cram1_read_arbiter.v'):
  shutil.copy2(COMP/'frozen'/n,f/'src/fpga/common'/n)
 g=P/'span-lookahead-v3/gpu_core.v'
 assert sha(g)=='ac840c18f263714257eebbb219486d9d345657c4db52bbbc69dd2ec6b4032a5d'
 shutil.copy2(g,f/'src/fpga/common/gpu_core.v')
 (f/TOP).write_text(top_patch((f/TOP).read_text()))
 with (f/QSF).open('a') as q:q.write('\n# Private LOD5 + exact span lookahead; retained CPU, clocks and seed.\nset_global_assignment -name VERILOG_FILE ../../common/phase10_cram1_read_arbiter.v\nset_global_assignment -name VERILOG_MACRO INCLUDE_SPAN_LOOKAHEAD\n')
 actual={str(x.relative_to(f)):sha(x) for x in sorted(f.rglob('*')) if x.is_file()}
 cpu='src/fpga/vendor/vexriscv/VexiiRiscv/VexiiRiscv_os30.v';assert actual[cpu]==expected[cpu]
 save(CORE/'prepared.json',dict(scope=__doc__,private_only=True,production_modified=False,
  source_sha256=actual,parent_prepared_sha256=sha(BASE/'prepared.json'),recipe_sha256=sha(Path(__file__)),
  component_manifest_sha256=sha(COMP/'sources.json'),component_test_sha256=sha(COMP/'wrapper/result.json'),
  cpu_preserved=True,cpu_cache_kib=128,configured_mhz=100,seed=2,seed_sweep=False,
  changed={n:{'before':expected.get(n),'after':h} for n,h in actual.items() if expected.get(n)!=h},
  full_core_functionally_qualified=False,deployment_ready=False))
 patch=[]
 for n in (TOP,QSF,'src/fpga/common/phase10_load_alias.v','src/fpga/common/geometry_raw_load.v'):
  patch+=difflib.unified_diff((BASE/'frozen'/n).read_text().splitlines(True),(f/n).read_text().splitlines(True),fromfile='LOD4/'+n,tofile='LOD5/'+n)
 (CORE/'integration.patch').write_text(''.join(patch));shutil.copy2(__file__,CORE/'recipe.py');print(CORE)
if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','test','stage']);args=a.parse_args();globals()[args.action]()
