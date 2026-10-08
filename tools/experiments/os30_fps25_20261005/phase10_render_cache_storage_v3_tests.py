#!/usr/bin/env python3
"""Actual old/new cache cycle miter plus hostile memory scoreboard; no CAD."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,re,shutil,subprocess,sys
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10';O=P/'render-cache-storage-v3';D=O/'protocol'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def put(p,d):
 with p.open('x') as f:json.dump(d,f,indent=2,sort_keys=True);f.write('\n')
def debug(s):
 return s.replace('output reg protocol_error','output reg protocol_error,\n    output wire [4:0] debug_state').replace('reg [4:0] state;','reg [4:0] state;\nassign debug_state=state;')
def prepare():
 D.mkdir(exist_ok=False);shutil.copy2(__file__,D/'recipe.py')
 old=debug((O/'baseline.sv').read_text());new=debug((O/'gpu_color_depth_cache.sv').read_text())
 ref=old.replace('module gpu_color_depth_cache #(','module gpu_color_depth_cache_reference #(',1)
 (D/'reference.sv').write_text(ref)
 block=new.split(') (',1)[1].split('\n);',1)[0]
 ports=[];decl=[]
 for line in block.splitlines():
  m=re.match(r'\s*(input|output) (?:wire|reg)\s*(\[[^]]+\])?\s*(.*?)[,;]?$',line)
  if not m:continue
  direction,width,names=m.groups()
  for n in names.rstrip(',').split(','):
   n=n.strip();assert re.fullmatch(r'\w+',n),(n,line)
   ports.append((n,direction))
   if direction=='output':decl.append(f'wire {width or ""} ref_{n};')
 params=re.findall(r'parameter (\w+)\s*=',new.split(') (',1)[0])
 inst='\n'.join(decl)+'\ngpu_color_depth_cache_reference #(\n'+',\n'.join(f' .{p}({p})' for p in params)+'\n) reference (\n'+',\n'.join(f' .{n}({"ref_" if di=="output" else ""}{n})' for n,di in ports)+'\n);\n'
 assertions='''
integer check_cycles=0,look_cycles=0,replacements=0,victim_collisions=0;
integer init_writes=0,alloc_writes=0,bank_switches=0,reset_cycles=0;
reg [63:0] read_banks=0,write_banks=0;
reg [DATA_BITS-1:0] prev_read=0;
reg mon_started=0;
always @(negedge clk) begin
 mon_started<=1;
 if(mon_started) begin
  check_cycles=check_cycles+1;
  assert({state,walk,slot,addr,left,read_op,maintenance,bounds_fresh,fill_new,beat}==
    {reference.state,reference.walk,reference.slot,reference.addr,reference.left,reference.read_op,reference.maintenance,reference.bounds_fresh,reference.fill_new,reference.beat});
  assert(data_ra==reference.data_ra && data_wa==reference.data_wa && data_we==reference.data_we);
  assert(data_q==reference.data_q);
  if(data_we) assert(data_w==reference.data_w);
  assert(meta_we==reference.meta_we && meta_wa==reference.meta_wa);
  if(meta_we) assert(meta_w==reference.meta_w);
  assert({flush_done,busy,has_lines,s_arready,s_awready,s_wready,s_rvalid,s_bvalid,m_arvalid,m_awvalid,m_wvalid,hits,misses,writebacks,protocol_error}==
    {ref_flush_done,ref_busy,ref_has_lines,ref_s_arready,ref_s_awready,ref_s_wready,ref_s_rvalid,ref_s_bvalid,ref_m_arvalid,ref_m_awvalid,ref_m_wvalid,ref_hits,ref_misses,ref_writebacks,ref_protocol_error});
  if(s_rvalid) assert({s_rdata,s_rlast}=={ref_s_rdata,ref_s_rlast});
  if(m_arvalid) assert({m_araddr,m_arlen}=={ref_m_araddr,ref_m_arlen});
  if(m_awvalid) assert({m_awaddr,m_awlen}=={ref_m_awaddr,ref_m_awlen});
  if(m_wvalid) assert({m_wdata,m_wstrb,m_wlast}=={ref_m_wdata,ref_m_wstrb,ref_m_wlast});
  if(reset_n && state==LOOK) begin
   assert(victim_q==reference.victim[set_index]);
   assert({selected,selected_meta,hit,empty}=={reference.selected,reference.selected_meta,reference.hit,reference.empty});
   look_cycles=look_cycles+1;
   if(!hit && !empty) replacements=replacements+1;
  end
 end
end
always @(posedge clk) begin
 if(!reset_n)reset_cycles=reset_cycles+1;
 if(victim_we && victim_wa==meta_ra)victim_collisions=victim_collisions+1;
 if(victim_we && state==INIT)init_writes=init_writes+1;
 if(victim_we && state==ALLOC)alloc_writes=alloc_writes+1;
 if((data_ra>>11)!=(prev_read>>11))bank_switches=bank_switches+1;
 prev_read<=data_ra;
 read_banks[data_ra>>11]<=1;
 if(data_we)write_banks[data_wa>>11]<=1;
end
final $display("STORAGE_COVER checks=%0d look=%0d replace=%0d collision=%0d init=%0d alloc=%0d reset=%0d bank_switch=%0d read_banks=%0h write_banks=%0h",check_cycles,look_cycles,replacements,victim_collisions,init_writes,alloc_writes,reset_cycles,bank_switches,read_banks,write_banks);
'''
 (D/'gpu_color_depth_cache.sv').write_text(new.replace('endmodule',inst+assertions+'\nendmodule',1))
 s=(P/'cache-write-speculate-v2/protocol/main.cpp').read_text()
 s=s.replace('std::array<uint32_t,16384>','std::array<uint32_t,262144>')
 directed='''
    void storage_directed() {
        flush();
        // Visit every set and fill every way, then wrap its replacement
        // pointer twice. Dirty all ways before eviction. This reaches every
        // physical bank and crosses every data-bank boundary.
        for(unsigned round=0;round<WAYS_MODEL+2;++round)
            for(unsigned set=0;set<SETS_MODEL;++set) {
                unsigned a=0x1000+(round*SETS_MODEL+set)*LINE_MODEL;
                read(a,1);write(a,1,(set&1)?5:15);
            }
        for(unsigned set=0;set<SETS_MODEL;++set)
            read(0x1000+set*LINE_MODEL,1);
        flush();
        // Reset while INIT is partway through, then make the first request
        // use the final set. All replacement entries must be initialized.
        t.reset_n=0;SET_BUS_RESET(0);tick();t.reset_n=1;SET_BUS_RESET(1);
        for(unsigned i=0;i<WAYS_MODEL;++i)tick();
        t.reset_n=0;tick();t.reset_n=1;
        while(t.busy)tick();expected=mem;
        read(0x1000+(SETS_MODEL-1)*LINE_MODEL,1);flush();
        // Cancel a miss immediately before ALLOC. No victim update may leak
        // through the synchronous-reset write gate.
        unsigned a=0x2000;t.s_araddr=a;t.s_arlen=0;t.s_arvalid=1;
        while(!tick().ar){}t.s_arvalid=0;
        while(t.debug_state!=8)tick();
        reset();read(a,1);flush();
    }
'''
 s=s.replace('    void run() {',directed+'\n    void run() {')
 s=s.replace('reset();staged_read_ownership();','reset();storage_directed();staged_read_ownership();')
 (D/'main.cpp').write_text(s)
 print('PREPARED',flush=True)
CONFIGS=[('gpu8',6,2,4,1,1,1,1),('gpu32',8,2,4,1,1,1,1),('ranged',8,2,4,0,1,1,1),('fourway',7,4,4,1,1,1,1),('small',1,2,1,1,1,1,1),('legacy',6,2,4,0,0,0,0)]
def run():
 def one(c):
  name,sets,ways,words,allcache,readstage,fillstage,spec=c;job=D/name;job.mkdir(exist_ok=True)
  cmd=['verilator','--cc','--exe','--build','--assert','-j','2','-Wno-fatal','--top-module','gpu_color_depth_cache','--Mdir',str(job/'obj'),f'-GSET_BITS={sets}',f'-GWORD_BITS={words}',f'-GWAYS={ways}','-GADDR_W=26',f'-GCACHE_ALL={allcache}',f'-GREGISTER_READ_LOOKUP={readstage}',f'-GREGISTER_FILL_HAZARD={fillstage}',f'-GSPECULATE_WRITE_HOT0={spec}','+define+CACHE_POISON_COLLISIONS','-CFLAGS',f'-std=c++17 -O2 -DSET_BITS_MODEL={sets} -DWORD_BITS_MODEL={words} -DWAYS_MODEL={ways} -DCACHE_ALL_MODEL={allcache} -DHAS_BUS_RESET -DFILL_STAGE_MODEL={fillstage} -DHOT_WRITE_MODEL={spec}',str(D/'gpu_color_depth_cache.sv'),str(D/'reference.sv'),str(D/'main.cpp')]
  if (job/'command.json').exists():assert json.loads((job/'command.json').read_text())==cmd
  else:
   put(job/'command.json',cmd)
   with (job/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
  results=[]
  for seed in [1,2]:
   if not (job/f'seed{seed}.log').exists():
    with (job/f'seed{seed}.log').open('x') as f:subprocess.run([str(job/'obj/Vgpu_color_depth_cache'),str(seed)],stdout=f,stderr=subprocess.STDOUT,check=True)
   t=(job/f'seed{seed}.log').read_text();assert 'PASS cycles=' in t and 'STORAGE_COVER' in t and 'FAIL' not in t
   nums={k:int(v) for k,v in re.findall(r'(cycles|memory_reads|memory_writes|checks|look|replace|collision|init|alloc|reset|bank_switch)=(\d+)',t)}
   masks={k:int(v,16) for k,v in re.findall(r'(read_banks|write_banks)=([0-9a-f]+)',t)}
   assert all(nums[k]>0 for k in ['checks','look','collision','init','alloc','reset']),nums
   if allcache or sets<8:assert nums['replace']>0,nums
   # Ranged GPU32 has exactly two disjoint16KiB regions: at most two
   # tags/set fit both ways, so zero true victim replacements is expected.
   bank_count=1<<max(0,sets+(ways.bit_length()-1)+words-11)
   assert masks['read_banks']==masks['write_banks']==(1<<bank_count)-1,masks
   if bank_count>1:assert nums['bank_switch']>0
   results.append(dict(seed=seed,**nums,**masks))
  print(name,results,flush=True);return name,results
 with ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(one,CONFIGS))
 put(D/'result.json',dict(passed=True,actual_original_rtl_cycle_oracle=True,poison_collisions=True,all_owned_bus_signals_compared_every_cycle=True,all_data_RAM_outputs_compared_every_cycle=True,configurations=CONFIGS,runs=results))
if __name__=='__main__':assert sys.argv[1] in ['prepare','run'];globals()[sys.argv[1]]()
