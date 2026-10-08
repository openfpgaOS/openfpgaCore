#!/usr/bin/env python3
"""Private optional cache read-lookup register boundary; no production edits."""
import difflib,hashlib,json,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v1/frozen/src/fpga/common/gpu_color_depth_cache.sv'
OUT=P/'cache-read-stage-v1';sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
 assert s.count(a)==1,(a[:80],s.count(a));return s.replace(a,b)
def main():
 s=BASE.read_text()
 s=rep(s,'    parameter MAX_WRITE_WORDS = 8','''    parameter MAX_WRITE_WORDS = 8,
    // Preserve AW/W timing. Capture AR before its hot/tag/data lookup, using
    // existing addr/left registers; one extra cycle for overlapping reads.
    parameter REGISTER_READ_LOOKUP = 0''')
 s=rep(s,'MAINT_WAIT=23, MAINT=24, MAINT_DONE=25;','MAINT_WAIT=23, MAINT=24, MAINT_DONE=25, R_ACCEPT=26;')
 s=rep(s,'wire [ADDR_W-1:0] lookup_addr=state==IDLE ? (s_awvalid ? s_awaddr : s_araddr) : addr;', '''wire [ADDR_W-1:0] lookup_addr=state==IDLE
    ? (REGISTER_READ_LOOKUP ? s_awaddr : (s_awvalid ? s_awaddr : s_araddr)) : addr;''')
 s=rep(s,'        IDLE: data_ra={hot_slot,lookup_addr[2+:WORD_BITS]};', '''        IDLE: data_ra={hot_slot,lookup_addr[2+:WORD_BITS]};
        R_ACCEPT: data_ra={hot_slot,word_index};''')
 s=rep(s,'''                        if((hot0_hit || hot1_hit) && cacheable(s_araddr)) begin
                            slot<=hot_slot; hits<=hits+1; state<=R_WORD;
                        end else state<=LOOK;''','''                        if(REGISTER_READ_LOOKUP) state<=R_ACCEPT;
                        else if((hot0_hit || hot1_hit) && cacheable(s_araddr)) begin
                            slot<=hot_slot; hits<=hits+1; state<=R_WORD;
                        end else state<=LOOK;''')
 s=rep(s,'            LOOK_WAIT: state<=LOOK;', '''            // IDLE already accepted and owns the read: AW and flush cannot
            // overtake it. Both RAMs now see registered addr. Their outputs
            // are ready when R_WORD/LOOK execute on the next cycle.
            R_ACCEPT: begin
                if((hot0_hit || hot1_hit) && cacheable(addr)) begin
                    slot<=hot_slot; hits<=hits+1; state<=R_WORD;
                end else state<=LOOK;
            end
            LOOK_WAIT: state<=LOOK;''')
 OUT.mkdir(exist_ok=False);(OUT/'gpu_color_depth_cache.sv').write_text(s)
 shutil.copy2(BASE,OUT/'baseline.sv');shutil.copy2(__file__,OUT/'recipe.py')
 (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff(BASE.read_text().splitlines(True),s.splitlines(True),fromfile='compact-v1/cache.sv',tofile='read-stage/cache.sv')))
 (OUT/'prepared.json').write_text(json.dumps(dict(private_only=True,parent_source_sha256=sha(BASE),source_sha256=sha(OUT/'gpu_color_depth_cache.sv'),parameter='REGISTER_READ_LOOKUP',default=0,candidate_value=1,added_data_registers=0,added_states=1,cacheable_read_cycles_added=1,AW_W_behavior_unchanged=True,quartus_run=False,deployed=False),indent=2)+'\n')
 print(json.dumps(json.loads((OUT/'prepared.json').read_text()),indent=2))
if __name__=='__main__':main()
