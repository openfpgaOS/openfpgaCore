#!/usr/bin/env python3
"""Hazard-register proof: actual hazard assertion plus hostile bus scoreboard."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,re,shutil,subprocess
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
OUT=P/'cache-read-stage-v2';D=OUT/'protocol';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    D.mkdir(exist_ok=False);shutil.copy2(__file__,D/'recipe.py')
    s=(P/'cache-read-stage-v1/protocol-v2/main.cpp').read_text()
    s=s.replace('    void run() {',r'''
    void hazard_directed() {
        flush();
        // Four posted single-word bursts fill and wrap the tracker while B
        // is withheld. The subsequent same-line read must await retirement.
        for(unsigned round=0;round<3;round++) {
            commit_hold=300;t.write_no_allocate=1;
            for(unsigned i=0;i<4;i++)write(0x1800+round*64+i*4,1,15);
            t.write_no_allocate=0;read(0x1800+round*64,4);flush();
        }
        // A posted burst crossing a line must block either line's refill.
        for(unsigned second=0;second<2;second++) {
            commit_hold=120;t.write_no_allocate=1;write(0x203c,2,15);
            t.write_no_allocate=0;read(second?0x2040:0x203c,1);flush();
        }
#if FILL_STAGE_MODEL
        // Cancel precisely FILL_CHECK, before any external AR is emitted.
        t.s_araddr=0x3000;t.s_arlen=0;t.s_arvalid=1;
        while(!tick().ar){}t.s_arvalid=0;
        while(t.debug_state!=27)tick();
        check(!rn&&!t.m_arvalid,"FILL_CHECK released AR early");
        reset();read(0x3000,1);flush();
#endif
    }
    void run() {''')
    s=s.replace('reset();staged_read_ownership();','reset();staged_read_ownership();hazard_directed();')
    (D/'main.cpp').write_text(s)
    shutil.copy2(OUT/'baseline.sv',D/'baseline.sv')
    s=(OUT/'gpu_color_depth_cache.sv').read_text()
    s=s.replace('output reg protocol_error','output reg protocol_error,\n    output wire [4:0] debug_state')
    s=s.replace('reg [4:0] state;','reg [4:0] state;\nassign debug_state=state;')
    monitor=r'''
integer cov_fill_check=0,cov_full=0,cov_wrap=0,cov_cross_wait=0;
integer cov_b_wait=0,cov_push_pop=0,cov_ar=0,cov_reset_check=0;
reg mon_fill_wait=0;
reg [ADDR_W-1:0] mon_addr;
reg mon_hazard;
always @(posedge clk) begin
    if(!reset_n && state==FILL_CHECK) cov_reset_check<=cov_reset_check+1;
    if(reset_n) begin
        if(state==FILL_CHECK || state==FILL_AR) begin
            assert(!m_awvalid);
            if(mon_fill_wait) begin
                assert(addr==mon_addr);
                assert(mon_hazard || !fill_hazard);
            end
        end
        if(state==FILL_AR && m_arvalid) begin
            assert(!fill_hazard);
            cov_ar<=cov_ar+1;
        end
        if(state==FILL_CHECK) begin
            assert(REGISTER_FILL_HAZARD && !m_arvalid);
            cov_fill_check<=cov_fill_check+1;
        end
        if(wb_full)cov_full<=cov_full+1;
        if(m_awvalid && m_awready && wb_tail==WB_PTR'(WB_POST-1))cov_wrap<=cov_wrap+1;
        if(m_bvalid && wb_valid[wb_head] && state==FILL_AR)cov_b_wait<=cov_b_wait+1;
        if(m_awvalid && m_awready && m_bvalid && wb_valid[wb_head])cov_push_pop<=cov_push_pop+1;
        for(integer i=0;i<WB_POST;i=i+1)
            if((state==FILL_CHECK || state==FILL_AR) && wb_valid[i] && wb_cross[i] && wb_line[i]==prev_line)
                cov_cross_wait<=cov_cross_wait+1;
    end
    mon_fill_wait<=reset_n && (state==FILL_CHECK || state==FILL_AR);
    mon_addr<=addr;mon_hazard<=fill_hazard;
end
final begin
    $display("HAZARD_COVER fill_check=%0d full=%0d wrap=%0d cross_wait=%0d b_wait=%0d push_pop=%0d ar=%0d reset_check=%0d",cov_fill_check,cov_full,cov_wrap,cov_cross_wait,cov_b_wait,cov_push_pop,cov_ar,cov_reset_check);
end
'''
    s=s.replace('endmodule',monitor+'\nendmodule');(D/'gpu_color_depth_cache.sv').write_text(s)
    # Explicit final() runs the RTL coverage block, beyond destructor semantics.
    p=D/'main.cpp';s=p.read_text().replace('test.run(); }','test.run(); test.t.final(); }');p.write_text(s)
    configs=[('baseline',0,1),('default',0,1),('staged',1,1),('ranged',1,0)]
    def one(c):
        name,stage,allcache=c;job=D/name;job.mkdir();rtl=D/('baseline.sv' if name=='baseline' else 'gpu_color_depth_cache.sv')
        cmd=['verilator','--cc','--exe','--build','--assert','-j','2','-Wno-fatal','--top-module','gpu_color_depth_cache','--Mdir',str(job/'obj'),'-GSET_BITS=6','-GWORD_BITS=4','-GWAYS=2','-GADDR_W=26',f'-GCACHE_ALL={allcache}','-GREGISTER_READ_LOOKUP=1','+define+CACHE_POISON_COLLISIONS','-CFLAGS',f'-std=c++17 -O2 -DSET_BITS_MODEL=6 -DWORD_BITS_MODEL=4 -DWAYS_MODEL=2 -DCACHE_ALL_MODEL={allcache} -DHAS_BUS_RESET -DFILL_STAGE_MODEL={stage}']
        if name!='baseline':cmd.append(f'-GREGISTER_FILL_HAZARD={stage}')
        cmd += [str(rtl),str(D/'main.cpp')]
        (job/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
        with (job/'build.log').open('x') as o:subprocess.run(cmd,stdout=o,stderr=subprocess.STDOUT,check=True)
        runs=[]
        for seed in [1,2,3]:
            with (job/f'seed{seed}.log').open('x') as o:subprocess.run([str(job/'obj/Vgpu_color_depth_cache'),str(seed)],stdout=o,stderr=subprocess.STDOUT,check=True)
            text=(job/f'seed{seed}.log').read_text();assert 'PASS cycles=' in text and 'FAIL' not in text
            r=dict(seed=seed,**{k:int(v) for k,v in re.findall(r'(cycles|memory_reads|memory_writes)=(\d+)',text)})
            if name!='baseline':
                r['coverage']={k:int(v) for k,v in re.findall(r'(fill_check|full|wrap|cross_wait|b_wait|push_pop|ar|reset_check)=(\d+)',text)}
                if stage:assert all(r['coverage'].values()),r
            runs.append(r)
        print(name,runs,flush=True);return name,runs
    with ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(one,configs))
    assert results['baseline']==[{k:v for k,v in x.items() if k!='coverage'} for x in results['default']]
    (D/'result.json').write_text(json.dumps(dict(passed=True,default_cycle_identical=True,actual_hazard_assertion=True,poison_collisions=True,fill_check_reset=True,runs=results),indent=2)+'\n')
    (D/'sources.json').write_text(json.dumps({x.name:sha(x) for x in D.iterdir() if x.is_file()},indent=2)+'\n')
if __name__=='__main__':main()
