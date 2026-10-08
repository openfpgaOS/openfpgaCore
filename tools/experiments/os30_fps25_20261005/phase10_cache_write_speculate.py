#!/usr/bin/env python3
"""Private hot0 write speculation; no new state/register or Quartus invocation."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v3/frozen/src/fpga/common/gpu_color_depth_cache.sv'
OUT=P/'cache-write-speculate-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()

def rep(s,a,b):
    assert s.count(a)==1,(a,s.count(a))
    return s.replace(a,b,1)

def save(p,d):
    with p.open('x') as f: json.dump(d,f,indent=2);f.write('\n')

def prepare():
    old=BASE.read_text()
    s=rep(old,'parameter REGISTER_FILL_HAZARD = 0','''parameter REGISTER_FILL_HAZARD = 0,
    // Speculate on hot0's data slot before AW is registered. A hot1-only
    // hit re-reads through existing W_WAIT; no live AW tag selects data RAM.
    // With unstaged reads, AR retains its original immediate hot-slot path.
    parameter SPECULATE_WRITE_HOT0 = 0''')
    s=rep(s,'IDLE: data_ra={hot_slot,lookup_addr[2+:WORD_BITS]};',
        '''IDLE: data_ra={(SPECULATE_WRITE_HOT0 && (REGISTER_READ_LOOKUP || s_awvalid))
                       ? hot0_slot : hot_slot,lookup_addr[2+:WORD_BITS]};''')
    s=rep(s,'slot<=hot_slot; hits<=hits+1; state<=W_GET;',
        '''slot<=hot_slot; hits<=hits+1;
                            state<=(SPECULATE_WRITE_HOT0 && !hot0_hit) ? W_WAIT : W_GET;''')
    OUT.mkdir(exist_ok=False)
    (OUT/'gpu_color_depth_cache.sv').write_text(s)
    shutil.copyfile(BASE,OUT/'baseline.sv')
    shutil.copyfile(__file__,OUT/'recipe.py')
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),
        fromfile='compact-v3/cache',tofile='write-speculate-v1/cache')))
    save(OUT/'prepared.json',dict(source_sha256=sha(OUT/'gpu_color_depth_cache.sv'),
         baseline_sha256=sha(BASE),default_parameter=0,selected_parameter=1,
         new_register_bits=0,new_states=0,extra_cycles='One per cacheable hot1-only AW; all other schedules unchanged.',
         quartus_invoked=False,deployment_ready=False))
    print(sha(OUT/'gpu_color_depth_cache.sv'))

MONITOR=r'''
integer cov_hot0=0,cov_hot1=0,cov_wait=0,cov_wait_w=0,cov_reset_wait=0;
reg mon_hot1=0;
reg [ADDR_W-1:0] mon_write_addr;
reg [INDEX_BITS-1:0] mon_write_slot;
always @(posedge clk) begin
    if(!reset_n && state==W_WAIT) cov_reset_wait<=cov_reset_wait+1;
    if(reset_n) begin
        if(SPECULATE_WRITE_HOT0 && state==IDLE && (REGISTER_READ_LOOKUP || s_awvalid))
            assert(data_ra=={hot0_slot,lookup_addr[2+:WORD_BITS]});
        if(mon_hot1) begin
            assert(state==W_WAIT && !s_wready && !s_awready && !s_arready);
            assert(addr==mon_write_addr && slot==mon_write_slot);
            assert(data_ra=={mon_write_slot,mon_write_addr[2+:WORD_BITS]});
            cov_wait<=cov_wait+1;
            if(s_wvalid)cov_wait_w<=cov_wait_w+1;
        end
        if(state==IDLE && s_awvalid && !flush_req && cacheable(s_awaddr)
            && (hot0_hit || hot1_hit)) begin
            if(hot0_hit)cov_hot0<=cov_hot0+1;
            else cov_hot1<=cov_hot1+1;
        end
        if(state==FILL_AR && m_arvalid)assert(!fill_hazard);
    end
    mon_hot1<=reset_n && SPECULATE_WRITE_HOT0 && state==IDLE && s_awvalid && !flush_req
          && overlaps(s_awaddr,s_awlen) && !(!has_lines && (write_no_allocate || s_awlen>=7))
          && cacheable(s_awaddr) && !hot0_hit && hot1_hit;
    mon_write_addr<=s_awaddr;mon_write_slot<=hot_slot;
end
final begin
    $display("WRITE_COVER hot0=%0d hot1=%0d wait=%0d wait_w=%0d reset_wait=%0d",cov_hot0,cov_hot1,cov_wait,cov_wait_w,cov_reset_wait);
end
'''

DIRECTED=r'''
    void speculative_write_ownership() {
        flush(); read(0x1000,1); read(0x1100,1); // hot0=B, hot1=A
        input_write_addr=0x1000; input_write_words=1;
        t.s_awaddr=0x1000;t.s_awlen=0;t.s_awvalid=1;
        t.s_wdata=0xaabbccdd;t.s_wstrb=5;t.s_wlast=1;t.s_wvalid=1;
        while(!tick().aw){}
        t.s_awvalid=0;
        // Live inputs cease to belong to the accepted write. The first W
        // remains valid from the AW edge and must wait for the correct data.
        t.s_awaddr=0x5abc;t.s_awlen=255;t.s_araddr=0x4000;t.s_arlen=7;
        t.s_arvalid=1;t.flush_req=1;
#if HOT_WRITE_MODEL
        check(t.debug_state==6,"hot1 AW did not enter W_WAIT");
        auto stalled=tick();check(!stalled.w&&!stalled.ar&&!stalled.aw&&!t.flush_done,"hot1 wait ownership");
#endif
        t.s_arvalid=0;t.flush_req=0;
        while(!tick().w){}
        expected[0x1000/4]=(expected[0x1000/4]&0xff00ff00u)|0x00bb00ddu;
        t.s_wvalid=0;while(!tick().b){}input_write_words=0;
        read(0x1000,1);flush();
        // Cancel after accepting a hot1 AW but before any W/downstream AW.
        read(0x1000,1);read(0x1100,1);
        t.s_awaddr=0x1000;t.s_awlen=0;t.s_awvalid=1;
        while(!tick().aw){}t.s_awvalid=0;
        check(!wn&&!t.m_awvalid,"speculation launched downstream write");
        reset();read(0x1000,1);flush();
    }
'''

def tests():
    D=OUT/'protocol';D.mkdir(exist_ok=False)
    shutil.copyfile(__file__,D/'recipe.py')
    source=(P/'cache-read-stage-v2/protocol/main.cpp').read_text()
    source=rep(source,'    void run() {',DIRECTED+'\n    void run() {')
    source=rep(source,'reset();staged_read_ownership();hazard_directed();',
               'reset();staged_read_ownership();hazard_directed();speculative_write_ownership();')
    # Every ordinary write also poisons no-longer-owned AW inputs.
    source=rep(source,'        t.s_awvalid=0;\n        for (unsigned i=0;i<n;i++) {',
        '        t.s_awvalid=0;t.s_awaddr=0x5abc;t.s_awlen=255;\n        for (unsigned i=0;i<n;i++) {')
    (D/'main.cpp').write_text(source)
    for name in ('baseline.sv','gpu_color_depth_cache.sv'):
        s=(OUT/name).read_text()
        s=rep(s,'output reg protocol_error','output reg protocol_error,\n    output wire [4:0] debug_state')
        s=rep(s,'reg [4:0] state;','reg [4:0] state;\nassign debug_state=state;')
        if name=='baseline.sv':s=rep(s,'reg [4:0] state;','localparam SPECULATE_WRITE_HOT0=0;\nreg [4:0] state;')
        s=rep(s,'endmodule',MONITOR+'\nendmodule')
        (D/name).write_text(s)
    configurations=[('baseline',0,1),('default',0,1),('enabled',1,1),('ranged',1,0)]
    def one(c):
        name,enabled,allcache=c;job=D/name;job.mkdir()
        rtl=D/('baseline.sv' if name=='baseline' else 'gpu_color_depth_cache.sv')
        cmd=['verilator','--cc','--exe','--build','--assert','-j','2','-Wno-fatal',
             '--top-module','gpu_color_depth_cache','--Mdir',str(job/'obj'),
             '-GSET_BITS=6','-GWORD_BITS=4','-GWAYS=2','-GADDR_W=26',f'-GCACHE_ALL={allcache}',
             '-GREGISTER_READ_LOOKUP=1','-GREGISTER_FILL_HAZARD=1','+define+CACHE_POISON_COLLISIONS',
             '-CFLAGS',f'-std=c++17 -O2 -DSET_BITS_MODEL=6 -DWORD_BITS_MODEL=4 -DWAYS_MODEL=2 -DCACHE_ALL_MODEL={allcache} -DHAS_BUS_RESET -DFILL_STAGE_MODEL=1 -DHOT_WRITE_MODEL={enabled}']
        if name!='baseline':cmd.append(f'-GSPECULATE_WRITE_HOT0={enabled}')
        cmd += [str(rtl),str(D/'main.cpp')]
        save(job/'command.json',cmd)
        with (job/'build.log').open('x') as f:subprocess.run(cmd,stdout=f,stderr=subprocess.STDOUT,check=True)
        runs=[]
        for seed in (1,2,3):
            with (job/f'seed{seed}.log').open('x') as f:
                subprocess.run([str(job/'obj/Vgpu_color_depth_cache'),str(seed)],stdout=f,stderr=subprocess.STDOUT,check=True)
            text=(job/f'seed{seed}.log').read_text();assert 'PASS cycles=' in text and 'FAIL' not in text
            result=dict(seed=seed,**{k:int(v) for k,v in re.findall(r'(cycles|memory_reads|memory_writes)=(\d+)',text)})
            result['coverage']={k:int(v) for k,v in re.findall(r'(hot0|hot1|wait|wait_w|reset_wait)=(\d+)',text)}
            if enabled:assert all(result['coverage'].values()),result
            runs.append(result)
        print(name,runs,flush=True)
        return name,runs
    with ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(one,configurations))
    assert results['baseline']==results['default']
    save(D/'result.json',dict(passed=True,default_cycle_identical=True,poison_collisions=True,
         live_aw_poison=True,same_cycle_first_w=True,wait_reset=True,runs=results,
         limits='Component memory scoreboard only; no whole-frame performance or fitted timing claim.'))

if __name__=='__main__':
    assert sys.argv[1:] in (['prepare'],['tests'])
    {'prepare':prepare,'tests':tests}[sys.argv[1]]()
