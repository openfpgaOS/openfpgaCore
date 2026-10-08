#!/usr/bin/env python3
"""Private synchronous victim metadata and 2048-word render-data banks; no CAD."""
from pathlib import Path
import difflib,hashlib,json,shutil,sys
R=Path(__file__).resolve().parents[3];P=R/'docs/measurements/os30-fps25-20261005/phase10'
B=P/'cache64-rvcpipe-map-v1/frozen/src/fpga/common/gpu_color_depth_cache.sv';O=P/'render-cache-storage-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a,s.count(a));return s.replace(a,b,1)
def prepare():
 old=B.read_text();assert sha(B)=='2f68b6571a5fbaa88ec016d571d84f0403acaf0b000efab70daa9b4ed2f12aea'
 s=rep(old,'// Data + byte-valid/dirty masks use one 40-bit simple-dual-port M10K RAM.','// Data + byte-valid/dirty masks use 40-bit simple-dual-port M10K banks.\n// Banks are at most 2048 words; reads retain the original one-cycle latency.')
 s=rep(s,'reg [WAY_BITS-1:0] victim [0:SETS-1];','(* ramstyle = "M10K, no_rw_check" *) reg [WAY_BITS-1:0] victim [0:SETS-1];\nreg [WAY_BITS-1:0] victim_q;')
 a=s.index('// A single explicit read address and write port allow Quartus to infer RAM.')
 b=s.index('wire [3:0] word_valid=',a)
 s=s[:a]+'''// Keep each data RAM at most 2048 words deep so its 40 bits pack into
// eight M10Ks. The registered bank selector follows the same read edge as
// the RAM address; selecting with live data_ra would misalign prefetches.
wire [39:0] data_q;
reg data_we;
reg [DATA_BITS-1:0] data_ra, data_wa;
reg [39:0] data_w;
generate if(DATA_BITS<=11) begin: data_single
    (* ramstyle = "M10K, no_rw_check" *) reg [39:0] data_mem [0:LINES*WORDS-1];
    reg [39:0] q;
    always @(posedge clk) begin
        if(data_we) data_mem[data_wa]<=data_w;
        q<=data_mem[data_ra];
`ifdef CACHE_POISON_COLLISIONS
        if(data_we && data_wa==data_ra) q<=~data_w;
`endif
    end
    assign data_q=q;
end else begin: data_banked
    localparam BANK_BITS=DATA_BITS-11, BANKS=1<<BANK_BITS;
    wire [BANKS*40-1:0] bank_q;
    reg [BANK_BITS-1:0] read_bank;
    always @(posedge clk) read_bank<=data_ra[DATA_BITS-1:11];
    for(genvar b=0;b<BANKS;b=b+1) begin: bank
        (* ramstyle = "M10K, no_rw_check" *) reg [39:0] data_mem [0:2047];
        reg [39:0] q;
        wire write_here=data_we && data_wa[DATA_BITS-1:11]==BANK_BITS'(b);
        always @(posedge clk) begin
            if(write_here) data_mem[data_wa[10:0]]<=data_w;
            q<=data_mem[data_ra[10:0]];
`ifdef CACHE_POISON_COLLISIONS
            if(write_here && data_wa[10:0]==data_ra[10:0]) q<=~data_w;
`endif
        end
        assign bank_q[b*40+:40]=q;
    end
    assign data_q=bank_q[read_bank*40+:40];
end endgenerate
''' +s[b:]
 anchor='wire [WAYS*META_BITS-1:0] meta_q;'
 s=rep(s,anchor,'''// Replacement metadata shares the tag lookup's registered read address.
// INIT/ALLOC writes are never consumed by LOOK on the following cycle, so
// mixed-port read-during-write remains unowned, just as for the tag banks.
wire victim_we=reset_n && ((state==INIT && walk[WAY_BITS-1:0]==0) || state==ALLOC);
wire [SET_BITS-1:0] victim_wa=state==INIT ? walk[INDEX_BITS-1:WAY_BITS] : set_index;
wire [WAY_BITS-1:0] victim_w=state==INIT ? {WAY_BITS{1'b0}} : slot[WAY_BITS-1:0]+1'b1;
always @(posedge clk) begin
    if(victim_we) victim[victim_wa]<=victim_w;
    victim_q<=victim[meta_ra];
`ifdef CACHE_POISON_COLLISIONS
    if(victim_we && victim_wa==meta_ra) victim_q<=~victim_w;
`endif
end
'''+anchor)
 s=s.replace('victim[set_index]','victim_q')
 s=rep(s,'                if(walk[WAY_BITS-1:0]==0) victim[walk[INDEX_BITS-1:WAY_BITS]]<=0;\n','')
 s=rep(s,'                victim_q<=slot[WAY_BITS-1:0]+1\'b1;\n','')
 O.mkdir(exist_ok=False);(O/'gpu_color_depth_cache.sv').write_text(s);(O/'baseline.sv').write_text(old);shutil.copy2(__file__,O/'recipe.py')
 (O/'candidate.patch').write_text(''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile='parent/cache',tofile='storage/cache')))
 d=dict(source_sha256=sha(O/'gpu_color_depth_cache.sv'),baseline_sha256=sha(B),recipe_sha256=sha(Path(__file__)),victim_read='one-cycle via meta_ra',data_bank_max_words=2048,bank_only_when_data_bits_gt=11,extra_transaction_cycles=0,gpu32_data_banks=4,gpu32_added_bank_selector_bits=2,physical_resource_claim=False,production_modified=False,quartus_invoked=False,deployment_ready=False)
 (O/'prepared.json').write_text(json.dumps(d,indent=2)+'\n');print(d['source_sha256'])
if __name__=='__main__':assert sys.argv[1:]==['prepare'];prepare()
