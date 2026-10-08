#!/usr/bin/env python3
"""Bounded full-strobe W_WAIT shortcut; private component qualification only."""
import difflib,inspect,json,shutil,sys
from pathlib import Path
import phase10_cache_write_speculate as v1
P=v1.P;OUT=P/'cache-write-speculate-v2';BASE=P/'cache-write-speculate-v1/gpu_color_depth_cache.sv'
rep=v1.rep;sha=v1.sha;save=v1.save
def prepare():
 assert sha(BASE)=='0777f44dd1be0cca3443ca2ed57d436f3f81f75bf882c9956cf04505f0d297ef'
 old=BASE.read_text();s=old
 s=rep(s,'assign s_wready=(state==W_GET) || (state==BY_W && m_wready);',
 '''// A full word replaces all 40 data/valid/dirty bits. W_WAIT already
// owns its slot and metadata, so its stale data_q is irrelevant for this beat.
wire write_get=(state==W_GET) || (SPECULATE_WRITE_HOT0 && state==W_WAIT && s_wstrb==4'hf);
assign s_wready=write_get || (state==BY_W && m_wready);''')
 s=rep(s,'        W_GET: begin\n            if(s_wvalid) begin',
        '        W_WAIT, W_GET: begin\n            if(write_get && s_wvalid) begin')
 s=rep(s,'            W_WAIT: state<=W_GET;\n            W_GET: if(s_wvalid) begin',
 '''            W_WAIT, W_GET: begin
                // W_WAIT without an accepted full beat fetches current data;
                // an accepted same-line beat fetches the next word instead.
                state<=W_GET;
                if(write_get && s_wvalid) begin''')
 s=rep(s,'            R_WAIT: state<=R_WORD;',
        '            end\n            R_WAIT: state<=R_WORD;')
 OUT.mkdir(exist_ok=False)
 (OUT/'gpu_color_depth_cache.sv').write_text(s);shutil.copy2(BASE,OUT/'baseline.sv')
 shutil.copy2(__file__,OUT/'recipe.py')
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='write-speculate-v1/cache',tofile='write-speculate-v2/cache')))
 save(OUT/'prepared.json',dict(source_sha256=sha(OUT/'gpu_color_depth_cache.sv'),parent_sha256=sha(BASE),
  default_parameter=0,selected_parameter=1,new_register_bits=0,new_states=0,new_ram_bits=0,
  scope='Only SPECULATE_WRITE_HOT0 enables full-strobe W acceptance in existing W_WAIT; W_GET storage and advance path shared.',quartus_invoked=False))
 print(sha(OUT/'gpu_color_depth_cache.sv'))

DIRECTED=r'''
    void full_wait_ownership() {
        // Both W_WAIT entrants, line-end/next-line beats, all first-word masks,
        // and W absent during the wait. Full overwrite must ignore poisoned
        // old data (especially WCLEAR's final-word collision).
        for(unsigned clear=0;clear<2;++clear) for(unsigned last=0;last<2;++last)
        for(unsigned n:{1u,2u,17u}) for(unsigned mask:{0u,5u,15u})
        for(unsigned absent=0;absent<2;++absent) {
            flush(); if(!clear)read(0x1000,1); read(0x1100,1);
            unsigned a=0x1000+(last?LINE_MODEL-4:0);
            input_write_addr=a;input_write_words=n;
            t.s_awaddr=a;t.s_awlen=n-1;t.s_awvalid=1;
            t.s_wdata=0xa5c37e19;t.s_wstrb=mask;t.s_wlast=n==1;t.s_wvalid=!absent;
            while(!tick().aw){}t.s_awvalid=0;
            t.s_awaddr=0x5abc;t.s_awlen=255;t.s_araddr=0x4000;t.s_arlen=7;
            t.s_arvalid=1;t.flush_req=1;
            bool first_done=false;
            if(clear || HOT_WRITE_MODEL) {
                while(t.debug_state!=6){auto q=tick();check(!q.w&&!q.ar&&!q.aw,"W/AW/AR escaped before owned W_WAIT");}
                auto q=tick();check(!q.ar&&!q.aw&&!t.flush_done,"later ownership overtook W_WAIT");
                check(q.w==bool(HOT_WRITE_MODEL&&mask==15&&!absent),"full W_WAIT shortcut qualification");
                first_done=q.w;
                if(!first_done)check(t.debug_state==7,"nonaccepted W_WAIT did not fetch into W_GET");
                else if(n==1)check(t.debug_state==1,"full last W did not finish");
                else check(t.debug_state==(last?2:7),"full W_WAIT next-word state");
            }
            t.s_arvalid=0;t.flush_req=0;t.s_wvalid=1;
            if(!first_done)while(!tick().w){}
            for(unsigned i=0;i<n;++i){
                if(i){t.s_wdata=0x12789abcu+i;t.s_wstrb=(i&1)?5:15;t.s_wlast=i+1==n;t.s_wvalid=1;while(!tick().w){}}
                auto &e=expected[a/4+i];for(unsigned b=0;b<4;++b)if(t.s_wstrb&(1u<<b))e=(e&~(255u<<(8*b)))|(t.s_wdata&(255u<<(8*b)));
                t.s_wvalid=0;
            }
            while(!tick().b){}input_write_words=0;
            read(a&~(LINE_MODEL-1),WORDS_PER_LINE);read(a,n);flush();
        }
    }
'''

MONITOR=v1.MONITOR
MONITOR=rep(MONITOR,'assert(state==W_WAIT && !s_wready && !s_awready && !s_arready);',
 '''assert(state==W_WAIT && !s_awready && !s_arready);
            assert(s_wready==(SPECULATE_WRITE_HOT0 && s_wstrb==4'hf));''')
MONITOR=rep(MONITOR,'assert(data_ra=={mon_write_slot,mon_write_addr[2+:WORD_BITS]});',
 '''if(s_wvalid && s_wready && left!=1 && same_line)
                assert(data_ra=={mon_write_slot,word_index+WORD_BITS'(1)});
            else assert(data_ra=={mon_write_slot,mon_write_addr[2+:WORD_BITS]});''')
MONITOR+=r'''
integer full_wait_hot=0,full_wait_clear=0,partial_wait=0,zero_wait=0,absent_wait=0;
reg [4:0] previous_state;
always @(posedge clk)begin
 previous_state<=state;
 if(reset_n && state==W_WAIT)begin
  if(!s_wvalid)absent_wait<=absent_wait+1;
  else if(s_wstrb==0)begin assert(!s_wready);zero_wait<=zero_wait+1;end
  else if(s_wstrb!=15)begin assert(!s_wready);partial_wait<=partial_wait+1;end
  else if(SPECULATE_WRITE_HOT0)begin
   assert(s_wready && data_we && data_w=={8'hff,s_wdata});
   assert(bounds_fresh && active_meta[0] && active_meta[META_TAG+:TAG_BITS]==tag);
   assert(meta_we && meta_w[1:0]==2'b11);
   if(previous_state==IDLE)full_wait_hot<=full_wait_hot+1;
   else begin assert(previous_state==WCLEAR);full_wait_clear<=full_wait_clear+1;end
  end
 end
end
final $display("FULL_WAIT hot=%0d clear=%0d partial=%0d zero=%0d absent=%0d",full_wait_hot,full_wait_clear,partial_wait,zero_wait,absent_wait);
'''

def tests():
 # Reuse the retained independent memory scoreboard and all original tests.
 # Only adapt monitor expectations where v2 intentionally accepts a full W.
 fn=inspect.getsource(v1.tests)
 fn=rep(fn,"source=rep(source,'    void run() {',DIRECTED+'\\n    void run() {')",
        "source=rep(source,'    void run() {',DIRECTED+'\\n    void run() {')")
 fn=rep(fn,"'reset();staged_read_ownership();hazard_directed();speculative_write_ownership();')",
        "'reset();staged_read_ownership();hazard_directed();speculative_write_ownership();full_wait_ownership();')")
 fn=rep(fn,"        if name=='baseline.sv':s=rep(s,'reg [4:0] state;','localparam SPECULATE_WRITE_HOT0=0;\\nreg [4:0] state;')\n",'')
 ns=dict(v1.__dict__,OUT=OUT,BASE=BASE,MONITOR=MONITOR,DIRECTED=v1.DIRECTED+DIRECTED,__file__=__file__)
 exec(fn,ns);ns['tests']()
 d=OUT/'protocol';coverage=[]
 import re
 for p in sorted(d.glob('*/seed*.log')):
  x={k:int(v) for k,v in re.findall(r'(hot|clear|partial|zero|absent)=(\d+)',re.search(r'FULL_WAIT (.*)',p.read_text()).group(1))}
  if p.parent.name in ('enabled','ranged'):assert all(x.values()),(p,x)
  coverage.append(dict(log=str(p.relative_to(OUT)),counts=x))
 save(d/'full-wait-result.json',dict(passed=True,runs=coverage,source_sha256=sha(OUT/'gpu_color_depth_cache.sv'),
  scope='All40 overwrite, owned metadata, both W_WAIT predecessors, partial/zero/absent W, first W before/concurrent AW, late poisoned AW, line crossings and existing hostile RAM/reset/posted traffic.'))
if __name__=='__main__':
 for action in sys.argv[1:]:globals()[action]()
