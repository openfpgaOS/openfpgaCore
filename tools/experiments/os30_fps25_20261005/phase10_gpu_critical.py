#!/usr/bin/env python3
"""Private GPU critical-path instrumentation and explicitly ideal memory bound."""
import argparse,hashlib,importlib.util,json,os,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OUT=P/'gpu-critical-bound-v2'
OLD=P/'render-line32-v1/baseline'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')
def rep(s,a,b):
    assert s.count(a)==1,(a[:120],s.count(a));return s.replace(a,b)
PROBE='''
// Exclusive counters gated after initialization. No GPU behavior is changed.
reg [63:0] crit_main[0:63],crit_drv[0:63],crit_pss[0:63];
reg [63:0] crit_frag[0:15],crit_span_len[0:15];
reg [63:0] crit_drain[0:15];
integer ci;
initial begin
 for(ci=0;ci<64;ci=ci+1)begin crit_main[ci]=0;crit_drv[ci]=0;crit_pss[ci]=0;end
 for(ci=0;ci<16;ci=ci+1)begin crit_frag[ci]=0;crit_span_len[ci]=0;end
 for(ci=0;ci<16;ci=ci+1)crit_drain[ci]=0;
end
wire crit_zero_uv=gpu.spanprod_pl_origin[1]==0&&gpu.spanprod_pl_du[1]==0&&gpu.spanprod_pl_dv[1]==0
 &&gpu.spanprod_pl_origin[2]==0&&gpu.spanprod_pl_du[2]==0&&gpu.spanprod_pl_dv[2]==0;
reg [3:0] crit_block;
always @* begin
 crit_block=0;
 if(gpu.fp_pipe_stall)begin
  if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)crit_block=1;
  else if(gpu.fbss==gpu.FBSS_ZTEST_ACC_EVAL)crit_block=2;
  else if(gpu.fbss!=gpu.FBSS_IDLE)crit_block=3;
  else if(gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test)begin
   if(!gpu.fb_read_barrier_clear)crit_block=4;
   else if(gpu.zw_fill_busy)crit_block=5;
   else crit_block=6;
  end else if(gpu.fb_write_buffer_stall)crit_block=7;
  else if(gpu.p1_valid&&!gpu.p1_response_ready)crit_block=8;
  else if(gpu.cmap_pipe_wait)crit_block=9;
  else crit_block=10;
 end else if(gpu.p2b_valid)crit_block=11;
 else if(gpu.src_done)crit_block=12;
 else if(gpu.persp_active)crit_block=13;
 else crit_block=14;
end
always @(posedge clk)if(reset_n&&critical_enable)begin
 crit_main[gpu.state]<=crit_main[gpu.state]+1;
 if(gpu.state==gpu.S_TRI_DERIVE)crit_drv[gpu.dstate]<=crit_drv[gpu.dstate]+1;
 if(gpu.state==gpu.S_FRAG_PIPE)begin
  crit_frag[crit_block]<=crit_frag[crit_block]+1;
  crit_pss[gpu.persp_pss]<=crit_pss[gpu.persp_pss]+1;
  if(crit_zero_uv&&gpu.persp_active)crit_drain[9]<=crit_drain[9]+1;
  if(crit_zero_uv&&gpu.persp_issue_stall&&!gpu.fp_pipe_stall&&!gpu.src_done)crit_drain[10]<=crit_drain[10]+1;
  if(gpu.persp_issue_stall&&!gpu.fp_pipe_stall&&!gpu.src_done)crit_drain[11]<=crit_drain[11]+1;
  if(gpu.src_done)begin
   crit_drain[0]<=crit_drain[0]+1;
   if(gpu.fp_pipe_stall)crit_drain[1]<=crit_drain[1]+1;
   if(gpu.fbss==gpu.FBSS_ZTEST_R_WAIT)crit_drain[2]<=crit_drain[2]+1;
   if(gpu.p3_valid&&!gpu.p3_discard&&gpu.p3_z_test&&gpu.fbss==gpu.FBSS_IDLE&&!gpu.fb_read_barrier_clear)crit_drain[3]<=crit_drain[3]+1;
   if(gpu.persp_active)crit_drain[4]<=crit_drain[4]+1;
   if(gpu.p1_valid||gpu.p2_valid||gpu.p2b_valid||gpu.p3_valid)crit_drain[5]<=crit_drain[5]+1;
  end
 end
 if(gpu.state==gpu.S_SPANPROD_EMIT)begin
  if(crit_zero_uv&&gpu.spanprod_attr_persp)crit_drain[8]<=crit_drain[8]+1;
  if(gpu.spanprod_cur_count<16)crit_span_len[gpu.spanprod_cur_count]<=crit_span_len[gpu.spanprod_cur_count]+1;
  else crit_span_len[15]<=crit_span_len[15]+1;
 end
end
final begin
 for(ci=0;ci<64;ci=ci+1)$display("CRITICAL_MAIN %0d %0d",ci,crit_main[ci]);
 for(ci=0;ci<64;ci=ci+1)$display("CRITICAL_DRV %0d %0d",ci,crit_drv[ci]);
 for(ci=0;ci<64;ci=ci+1)$display("CRITICAL_PSS %0d %0d",ci,crit_pss[ci]);
 for(ci=0;ci<16;ci=ci+1)$display("CRITICAL_FRAG %0d %0d",ci,crit_frag[ci]);
 for(ci=0;ci<16;ci=ci+1)$display("CRITICAL_SPAN %0d %0d",ci,crit_span_len[ci]);
 for(ci=0;ci<16;ci=ci+1)$display("CRITICAL_DRAIN %0d %0d",ci,crit_drain[ci]);
end
'''
def prepare():
    OUT.mkdir(exist_ok=False);d=OUT/'coupled';shutil.copytree(OLD/'model/frozen',d/'model/frozen')
    shutil.copytree(OLD/'qsim',d/'qsim',ignore=shutil.ignore_patterns('build','qsim'))
    p=d/'model/frozen/tb_gpu.v';s=p.read_text()
    s=rep(s,'    input  wire        clk,','''    input wire critical_enable, oracle_memory,
    output wire om_arvalid,om_awvalid,om_wvalid,om_wlast,
    output wire [31:0] om_araddr,om_awaddr,om_wdata,
    output wire [7:0] om_arlen,om_awlen,
    output wire [3:0] om_wstrb,
    input wire om_arready,om_awready,om_wready,om_rvalid,om_rlast,om_bvalid,
    input wire [31:0] om_rdata,
    input  wire        clk,''')
    wires='''wire real_arready,real_awready,real_wready,real_rvalid,real_rlast,real_bvalid;
wire [31:0] real_rdata;
assign om_arvalid=gpu_rd_arvalid;assign om_araddr=gpu_rd_araddr;assign om_arlen=gpu_rd_arlen;
assign om_awvalid=gpu_wr_awvalid;assign om_awaddr=gpu_wr_awaddr;assign om_awlen=gpu_wr_awlen;
assign om_wvalid=gpu_wr_wvalid;assign om_wdata=gpu_wr_wdata;assign om_wstrb=gpu_wr_wstrb;assign om_wlast=gpu_wr_wlast;
assign gpu_rd_arready=oracle_memory?om_arready:real_arready;
assign gpu_rd_rvalid=oracle_memory?om_rvalid:real_rvalid;
assign gpu_rd_rdata=oracle_memory?om_rdata:real_rdata;
assign gpu_rd_rlast=oracle_memory?om_rlast:real_rlast;
assign gpu_wr_awready=oracle_memory?om_awready:real_awready;
assign gpu_wr_wready=oracle_memory?om_wready:real_wready;
assign gpu_wr_bvalid=oracle_memory?om_bvalid:real_bvalid;
'''
    s=rep(s,'tb_sdram_cont #(',wires+'\ntb_sdram_cont #(')
    for port,wire in [('arready','gpu_rd_arready'),('rvalid','gpu_rd_rvalid'),('rdata','gpu_rd_rdata'),('rlast','gpu_rd_rlast'),('awready','gpu_wr_awready'),('wready','gpu_wr_wready'),('bvalid','gpu_wr_bvalid')]:
        s=rep(s,'.m0_'+port+'('+wire+')','.m0_'+port+'(real_'+port+')')
    for port,wire in [('arvalid','gpu_rd_arvalid'),('awvalid','gpu_wr_awvalid'),('wvalid','gpu_wr_wvalid')]:
        s=rep(s,'.m0_'+port+'('+wire+')','.m0_'+port+'('+wire+'&&!oracle_memory)')
    s=rep(s,'endmodule',PROBE+'\nendmodule');p.write_text(s)
    p=d/'model/frozen/coupled.cpp';s=p.read_text()
    s=rep(s,'static void tick() {','''static bool oracle_mode,oracle_write,oracle_read,oracle_b;
static uint32_t oracle_wa,oracle_ra;
static unsigned oracle_wi,oracle_wn,oracle_ri,oracle_rn;
static uint64_t oracle_reads,oracle_writes,oracle_read_words,oracle_write_words;
static void oracle_drive() {
    tb->om_awready=!oracle_write&&!oracle_read&&!oracle_b;
    tb->om_wready=oracle_write;
    tb->om_arready=!oracle_write&&!oracle_read&&!oracle_b&&!tb->om_awvalid;
    tb->om_bvalid=oracle_b;tb->om_rvalid=oracle_read;
    tb->om_rlast=oracle_read&&oracle_ri+1==oracle_rn;
    if(oracle_read)memcpy(&tb->om_rdata,memory()+oracle_ra+4*oracle_ri,4);
}
static void oracle_sample() {
    if(!oracle_mode)return;
    if(oracle_b)oracle_b=false;
    if(tb->om_rvalid) {
        ++oracle_ri;++oracle_read_words;
        if(oracle_ri==oracle_rn)oracle_read=false;
    }
    if(tb->om_awvalid&&tb->om_awready) {
        assert(!oracle_write&&!oracle_read);oracle_write=true;
        oracle_wa=tb->om_awaddr&0x03ffffff;oracle_wn=tb->om_awlen+1;oracle_wi=0;++oracle_writes;
    }
    if(tb->om_wvalid&&tb->om_wready) {
        assert(oracle_write&&oracle_wi<oracle_wn&&bool(tb->om_wlast)==(oracle_wi+1==oracle_wn));
        uint32_t data=tb->om_wdata;
        for(unsigned b=0;b<4;++b)if(tb->om_wstrb&(1u<<b))memory()[oracle_wa+4*oracle_wi+b]=data>>(8*b);
        ++oracle_wi;++oracle_write_words;
        if(oracle_wi==oracle_wn){oracle_write=false;oracle_b=true;}
    }
    if(tb->om_arvalid&&tb->om_arready) {
        assert(!oracle_write&&!oracle_read&&!oracle_b);oracle_read=true;
        oracle_ra=tb->om_araddr&0x03ffffff;oracle_rn=tb->om_arlen+1;oracle_ri=0;++oracle_reads;
    }
}
static void tick() {''')
    s=rep(s,'    tb->clk=0; tb->eval();\n    if(!initializing)',
        '    tb->clk=0; tb->eval();oracle_drive();tb->eval();\n    if(!initializing)')
    s=rep(s,'    tb->clk=1; tb->eval(); cycles++;','    oracle_sample();\n    tb->clk=1; tb->eval(); cycles++;')
    s=rep(s,'    initializing=true;tb=new Vtb_gpu;','''    initializing=true;tb=new Vtb_gpu;
    oracle_mode=getenv("IDEAL_GPU_MEMORY")&&atoi(getenv("IDEAL_GPU_MEMORY"));
    tb->oracle_memory=oracle_mode;tb->critical_enable=0;''')
    s=rep(s,'    initializing=false;active=true;tb->observe_enable=1;snapshot("start",0);','    initializing=false;active=true;tb->observe_enable=1;tb->critical_enable=1;snapshot("start",0);')
    s=rep(s,'    assert(!gd_operation);\n    FILE *g=','''    assert(!gd_operation);
    FILE *oracle=fopen((folder+"/oracle-memory.json").c_str(),"w");assert(oracle);
    fprintf(oracle,"{\\"enabled\\":%s,\\"read_bursts\\":%llu,\\"write_bursts\\":%llu,\\"read_words\\":%llu,\\"write_words\\":%llu}\\n",
      oracle_mode?"true":"false",(unsigned long long)oracle_reads,(unsigned long long)oracle_writes,
      (unsigned long long)oracle_read_words,(unsigned long long)oracle_write_words);fclose(oracle);
    FILE *g=''')
    p.write_text(s)
    cmd=[x.replace(str(OLD),str(d)) for x in json.loads((OLD/'build-command.json').read_text())]
    save(d/'build-command.json',cmd)
    save(d/'inputs.json',dict(scope=__doc__,parent=str(OLD),sources={str(x.relative_to(d)):sha(x) for x in d.rglob('*') if x.is_file()},recipe_sha256=sha(Path(__file__))))
def build():
    d=OUT/'coupled';q=d/'qsim'
    with (d/'build-qsim.log').open('x') as log:subprocess.run(['make','-j3',*[f'build/qsim/{n}.o' for n in 'cpu fpu cache prof qelf lint gpu app env main'.split()]],cwd=q,stdout=log,stderr=subprocess.STDOUT,check=True)
    cmd=json.loads((d/'build-command.json').read_text())
    with (d/'model/build.log').open('x') as log:subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True)
    save(d/'model/build.json',dict(command=cmd,simulator_sha256=sha(d/'model/obj/Vtb_gpu')))
def run(label):
    p=ROOT/'tools/experiments/os30_overlap_20261006/run.py';spec=importlib.util.spec_from_file_location('runner',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.OUT=OUT/'coupled';sys.argv=[str(p),label,'--elf',str(P.parent/'phase9/checks-state-f11/app.elf')];m.main()
    dest=m.OUT/('run-'+label);assert 'FATAL' not in (dest/'run.log').read_text()
    f=dest/'inputs.json';v=json.loads(f.read_text());v['environment']['IDEAL_GPU_MEMORY']=os.environ.get('IDEAL_GPU_MEMORY','0');f.write_text(json.dumps(v,indent=2)+'\n')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action');p.add_argument('label',nargs='?');a=p.parse_args()
    globals()[a.action](*([a.label] if a.label else []))
