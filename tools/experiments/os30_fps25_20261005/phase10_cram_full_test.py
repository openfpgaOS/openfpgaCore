#!/usr/bin/env python3
"""Independent full-16MiB physical CRAM addressing and reader-reset proof."""
import hashlib,json,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cram1-share-full-v1'
BASE=P/'cram1-share-v1/frozen'
ARB=P/'lod5-components-v1/frozen/phase10_cram1_read_arbiter.v'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:60],s.count(a));return s.replace(a,b)
def save(p,v):
 with p.open('x') as f:json.dump(v,f,indent=2);f.write('\n')

RESET_TEST=r'''
    integer media_resets=0,discarded_beats=0,physical_uploads=0;
    reg abandoned=0;
    always @(posedge clk)if(reset_n)begin
      if(abandoned&&rvalid[0])$fatal(1,"abandoned texture response leaked");
      if(abandoned&&rvalid_m)discarded_beats<=discarded_beats+1;
    end
    task automatic reset_texture(input integer phase);
      integer delay_cycles;
      // Reset before selection, around capture/ACK, and after selected R beats.
      @(negedge clk);araddr[0]=32'h003c0000;arlen[0]=31;arvalid[0]=1;
      if(phase==0)begin
        s0_reset=1;arvalid[0]=0;abandoned=1;
      end else if(phase==1)begin
        wait(adapter.burst_rd);
        @(negedge clk);s0_reset=1;arvalid[0]=0;abandoned=1;
      end else begin
        do @(posedge clk);while(!arready[0]);
        inflight[0]=1;
        @(negedge clk);arvalid[0]=0;
        if(phase>2)begin
          delay_cycles=0;
          while(delay_cycles<phase-2)begin
            @(posedge clk);if(rvalid[0])delay_cycles=delay_cycles+1;
          end
        end
        @(negedge clk);s0_reset=1;inflight[0]=0;abandoned=1;
      end
      // A competing geometry request arrives during reset and must complete.
      fork
        read_burst(1,32'h00fc0000,16);
        begin
          repeat(2)@(negedge clk);s0_reset=0;
          wait(arbiter.state==0&&!burst_busy&&!raw_busy);
        end
      join
      @(negedge clk);abandoned=0;
      read_burst(0,32'h003c0000,4);
      media_resets=media_resets+1;
    endtask
'''
INITIAL=r'''
    initial begin
      araddr[0]=0;araddr[1]=0;arlen[0]=0;arlen[1]=0;
      for(k=0;k<2;k=k+1)begin bursts[k]=0;words[k]=0;wait_cycles[k]=0;response_cycles[k]=0;end
      #20 reset_n=1;
      while(bcr_st!=B_DONE)@(negedge clk);
      // Real byte offsets, all distinct under the full address bits. Seed the
      // complete burst windows, then prove chip-side writes do not alias them.
      preload(22'h000000,64);
      preload(22'h0f0000,128); // 0x003c0000 low alias sentinel
      preload(22'h1fff00,256); // immediately before die boundary
      preload(22'h200000,128); // immediately after die boundary
      preload(22'h3f0000,2048);// real reserved pool start0x00fc0000
      preload(22'h3ffff0,16); // final legal64bytes
      upload(22'h0f0000);upload(22'h3f0000);
      upload(22'h1fffff);upload(22'h200000);upload(22'h3fffff);
      physical_uploads=5;
      read_burst(0,32'h003c0000,1);
      read_burst(1,32'h00fc0000,1);
      read_burst(1,32'h007ffffc,1);
      read_burst(1,32'h00800000,1);
      read_burst(1,32'h00fffffc,1);
      for(k=1;k<=32;k=k+1)read_burst(1,32'h00fc0000,k);
      // Valid DMA bursts split at4KiB, hence at the8MiB die boundary too.
      read_burst(1,32'h007fffc0,16);read_burst(1,32'h00800000,16);
      read_burst(1,32'h00fc0fc0,16);read_burst(1,32'h00fc1000,16);
      read_burst(1,32'h00ffffc0,16);
      fork
        read_burst(1,32'h00fc0080,16);
        begin wait(adapter.burst_rd);read_burst(0,32'h003c0100,4);end
      join
      fork
        begin for(integer t=0;t<100;t=t+1)begin
          repeat((t*13)%5)@(negedge clk);
          read_burst(0,32'h003c0000+((t*17)%96)*4,4);
        end end
        begin for(integer t=0;t<100;t=t+1)begin
          repeat((t*7)%3)@(negedge clk);
          read_burst(1,(t%2?32'h00800000:32'h00fc0000)+((t*3)%64)*4,1+(t%16));
        end end
        begin wait(burst_busy);upload(22'h3f0300);physical_uploads=physical_uploads+1;end
      join
      read_burst(0,32'h00fc0c00,1);
      // GPU media reset cannot cancel or corrupt an accepted geometry read.
      fork
        read_burst(1,32'h00fc0000,32);
        begin
          wait(arbiter.state==2&&arbiter.owner);
          @(negedge clk);s0_reset=1;repeat(5)@(negedge clk);s0_reset=0;
        end
      join
      media_resets=media_resets+1;
      for(k=0;k<=5;k=k+1)reset_texture(k);
      reset_texture(17);reset_texture(32);
      // An unaccepted texture request queued behind geometry is cancellable.
      fork
        read_burst(1,32'h00fc0000,32);
        begin
          wait(arbiter.state==2&&arbiter.owner);
          @(negedge clk);arvalid[0]=1;araddr[0]=32'h003c0000;arlen[0]=3;
          @(negedge clk);s0_reset=1;arvalid[0]=0;
          repeat(3)@(negedge clk);s0_reset=0;
        end
      join
      media_resets=media_resets+1;
      fork read_burst(0,32'h003c0000,4);read_burst(1,32'h00ffffc0,16);join
      // Fabric reset resets all source owners coherently and reprograms BCR.
      @(negedge clk);reset_n=0;repeat(3)@(negedge clk);reset_n=1;
      while(bcr_st!=B_DONE)@(negedge clk);
      read_burst(1,32'h00fc0040,16);read_burst(0,32'h003c0000,4);
      if(cram_errors!=0)$fatal(1,"chip errors %0d",cram_errors);
      $display("RESULT PASS refresh=%0d texture_bursts=%0d geometry_bursts=%0d texture_words=%0d geometry_words=%0d media_resets=%0d discarded_beats=%0d uploads=%0d cycles=%0d",EXTRA_REFRESH_CYCLES,bursts[0],bursts[1],words[0],words[1],media_resets,discarded_beats,physical_uploads,cycles);
      $finish;
    end
    initial begin #4000000;$fatal(1,"timeout");end
'''
def prepare():
 f=OUT/'frozen';f.mkdir(parents=True,exist_ok=False)
 for p in BASE.iterdir():
  if p.is_file():shutil.copyfile(p,f/p.name)
 shutil.copyfile(ARB,f/ARB.name)
 p=f/'cram_chip_model.v';s=p.read_text()
 s=rep(s,'input  wire [20:0] bd_word_addr,','input  wire [21:0] bd_word_addr,')
 s=s.replace('bd_word_addr[20]','bd_word_addr[21]').replace('bd_word_addr[18:0]','bd_word_addr[20:0]')
 s=rep(s,'localparam BANK_BITS = 20;','localparam BANK_BITS = 22;')
 s=s.replace('2MB per bank','8MiB per bank').replace('20-bit addressing per bank (1M × 16','22-bit addressing per bank (4M × 16')
 s=s.replace('bit 20 selects bank, bits 19:0','bit 21 selects bank, bits 20:0').replace('word_idx[18:0]','word_idx[20:0]')
 p.write_text(s)
 p=f/'phase10_cram1_share_tb.v';s=p.read_text()
 s=rep(s,'reg reset_n = 0;','reg reset_n = 0; reg s0_reset=0;')
 s=rep(s,'reg [20:0] bd_word_addr','reg [21:0] bd_word_addr')
 s=rep(s,'{base[21],1\'b0,base[18:0]}+i','base+i')
 s=rep(s,'.clk(clk),.reset_n(reset_n),\n      .s0_arvalid','.clk(clk),.reset_n(reset_n),.s0_reset(s0_reset),\n      .s0_arvalid')
 s=rep(s,'if(arready[p]&&!arvalid[p])','if(arready[p]&&!arvalid[p]&&!(p==0&&s0_reset))')
 i=s.index('    initial begin\n      araddr[0]=0;');j=s.index('endmodule',i)
 s=s[:i]+RESET_TEST+INITIAL+s[j:];p.write_text(s)
 shutil.copyfile(__file__,OUT/'recipe.py')
 save(OUT/'inputs.json',dict(sources={str(p.relative_to(OUT)):sha(p) for p in f.iterdir() if p.is_file()},arbiter_sha256=sha(ARB),scope=__doc__))
def run(refresh):
 f=OUT/'frozen';d=OUT/f'refresh{refresh}';d.mkdir(exist_ok=False)
 cmd=['verilator','--binary','--timing','-j','3','-Wno-fatal','-Wno-WIDTH','-Wno-UNOPTFLAT','-Wno-CASEINCOMPLETE','--top-module','tb_geometry_cram1_share','--Mdir',str(d/'obj'),f'-GEXTRA_REFRESH_CYCLES={refresh}']+[str(p) for p in f.iterdir() if p.suffix in ('.v','.sv')]
 save(d/'build-command.json',cmd)
 with (d/'build.log').open('x') as log:subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
 with (d/'run.log').open('x') as log:r=subprocess.run([str(d/'obj/Vtb_geometry_cram1_share')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
 save(d/'result.json',dict(returncode=r.returncode,stats=dict(re.findall(r'(\w+)=(\d+)',(d/'run.log').read_text())),simulator_sha256=sha(d/'obj/Vtb_geometry_cram1_share')))
 assert r.returncode==0,(d,(d/'run.log').read_text()[-2000:])
 print((d/'run.log').read_text()[-1000:])
if __name__=='__main__':
 for action in sys.argv[1:]:
  if action=='prepare':prepare()
  else:run(int(action))
