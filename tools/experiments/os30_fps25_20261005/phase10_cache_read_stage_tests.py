#!/usr/bin/env python3
"""Frozen cache read-stage scoreboard with collisions/ownership/reset coverage."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,re,shutil,subprocess
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10';OUT=P/'cache-read-stage-v1';D=OUT/'protocol-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 D.mkdir(exist_ok=False);shutil.copy2(__file__,D/'recipe.py')
 s=(ROOT/'tools/tests/gpu_color_depth_cache.cpp').read_text()
 s=s.replace('    void run() {',r'''    void staged_read_ownership() {
        read(0x1000,1); // establish a hot line
        t.s_araddr=0x1000;t.s_arlen=0;t.s_arvalid=1;
        while(!tick().ar){} t.s_arvalid=0;
        // A later write/flush cannot overtake an accepted read, including
        // the new lookup stage. No W follows this deliberately withdrawn AW.
        t.s_awaddr=0x1100;t.s_awlen=0;t.s_awvalid=1;t.flush_req=1;
        t.s_araddr=0x5abc;t.s_arlen=255; // inactive inputs must no longer select lookup
        auto q=tick();check(!q.aw&&!t.flush_done,"laterAW/flush overtookAR");
        t.s_awvalid=0;
        while(!q.r)q=tick();
        check(q.last&&q.data==expected[0x1000/4],"staged hotread mismatch");
        flush();
        // Reset during the just-accepted hot/cold read. No external AR has
        // yet escaped from this capture/lookup interval in either mode.
        for(unsigned hot=0;hot<2;hot++) {
            if(hot)read(0x1000,1);
            t.s_araddr=0x1000;t.s_arlen=0;t.s_arvalid=1;
            while(!tick().ar){} t.s_arvalid=0;
            check(!rn,"lookup unexpectedly launched downstreamread");
            reset();read(0x1000,1);flush();
        }
    }
    void run() {''')
 s=s.replace('for(auto &w:mem) w=random(); reset();','for(auto &w:mem) w=random(); reset();staged_read_ownership();')
 (D/'main.cpp').write_text(s)
 for name in ['baseline.sv','gpu_color_depth_cache.sv']:shutil.copy2(OUT/name,D/name)
 monitor='''
`ifdef READ_STAGE_CHECKS
always @(posedge clk) if(reset_n && state==R_ACCEPT) begin
 assert(REGISTER_READ_LOOKUP && read_op && busy);
 assert(!s_awready && !s_arready && !flush_done);
 assert(lookup_addr==addr && data_ra=={hot_slot,word_index});
end
`endif
'''
 p=D/'gpu_color_depth_cache.sv';s=p.read_text();s=s.replace('endmodule',monitor+'\nendmodule');p.write_text(s)
 configs=[('baseline',0,1),('default',0,1),('staged',1,1),('ranged',1,0)]
 def one(c):
  name,stage,allcache=c;job=D/name;job.mkdir();rtl=D/('baseline.sv' if name=='baseline' else 'gpu_color_depth_cache.sv')
  cmd=['verilator','--cc','--exe','--build','--assert','-j','2','-Wno-fatal','--top-module','gpu_color_depth_cache','--Mdir',str(job/'obj'),'-GSET_BITS=6','-GWORD_BITS=4','-GWAYS=2','-GADDR_W=26',f'-GCACHE_ALL={allcache}','+define+CACHE_POISON_COLLISIONS','+define+READ_STAGE_CHECKS','-CFLAGS',f'-std=c++17 -O2 -DSET_BITS_MODEL=6 -DWORD_BITS_MODEL=4 -DWAYS_MODEL=2 -DCACHE_ALL_MODEL={allcache} -DHAS_BUS_RESET']
  if name!='baseline':cmd.append(f'-GREGISTER_READ_LOOKUP={stage}')
  cmd += [str(rtl),str(D/'main.cpp')]
  (job/'command.json').write_text(json.dumps(cmd,indent=2)+'\n')
  with (job/'build.log').open('x') as o:subprocess.run(cmd,stdout=o,stderr=subprocess.STDOUT,check=True)
  runs=[]
  for seed in [1,2,3]:
   with (job/f'seed{seed}.log').open('x') as o:subprocess.run([str(job/'obj/Vgpu_color_depth_cache'),str(seed)],stdout=o,stderr=subprocess.STDOUT,check=True)
   text=(job/f'seed{seed}.log').read_text();assert 'PASS cycles=' in text and 'FAIL' not in text
   runs.append(dict(seed=seed,**{k:int(v) for k,v in re.findall(r'(cycles|memory_reads|memory_writes)=(\d+)',text)}))
  print(name,runs,flush=True);return name,runs
 with ThreadPoolExecutor(max_workers=2) as pool:results=dict(pool.map(one,configs))
 assert results['baseline']==results['default']
 (D/'result.json').write_text(json.dumps(dict(passed=True,default_cycle_identical=True,poison_collisions=True,stage_ownership_reset_cases=True,runs=results),indent=2)+'\n')
 (D/'sources.json').write_text(json.dumps({x.name:sha(x) for x in D.iterdir() if x.is_file()},indent=2)+'\n')
if __name__=='__main__':main()
