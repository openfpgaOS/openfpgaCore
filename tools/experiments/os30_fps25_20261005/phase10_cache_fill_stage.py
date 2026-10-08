#!/usr/bin/env python3
"""Private registered posted-write hazard check, preserving read-stage v1."""
from pathlib import Path
import hashlib,json,difflib,shutil
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'cache-read-stage-v1';OUT=P/'cache-read-stage-v2'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def rep(s,a,b):
    assert s.count(a)==1,(a,s.count(a));return s.replace(a,b)
def main():
    s=(BASE/'gpu_color_depth_cache.sv').read_text()
    s=rep(s,'parameter REGISTER_READ_LOOKUP = 0','''parameter REGISTER_READ_LOOKUP = 0,
    // Break address/posted-line compare -> downstream AR-valid timing.
    // Stable fill ownership makes a one-cycle-late B retirement conservative.
    parameter REGISTER_FILL_HAZARD = 0''')
    s=rep(s,'MAINT_DONE=25, R_ACCEPT=26;','MAINT_DONE=25, R_ACCEPT=26, FILL_CHECK=27;')
    s=rep(s,'reg fill_hazard;','''reg fill_hazard;
reg fill_hazard_r;
wire fill_blocked=REGISTER_FILL_HAZARD ? fill_hazard_r : fill_hazard;
// FILL_CHECK is visited on both fill entrances before AR can become valid.
// During FILL_CHECK/FILL_AR addr cannot change and no downstream AW is
// generated, so posted hazards can only disappear as B responses retire.
always @(posedge clk) begin
    if(!reset_n) fill_hazard_r<=1'b1;
    else if(state==FILL_CHECK || state==FILL_AR) fill_hazard_r<=fill_hazard;
end''')
    s=rep(s,'m_arvalid=!fill_hazard;','m_arvalid=!fill_blocked;')
    s=rep(s,'state<=read_op ? FILL_AR : WCLEAR;','state<=read_op ? (REGISTER_FILL_HAZARD ? FILL_CHECK : FILL_AR) : WCLEAR;')
    s=rep(s,"if(word_valid!=4'hf) begin fill_new<=0; state<=FILL_AR; end","if(word_valid!=4'hf) begin fill_new<=0; state<=REGISTER_FILL_HAZARD ? FILL_CHECK : FILL_AR; end")
    s=rep(s,'            FILL_AR: if(m_arready && !fill_hazard) begin beat<=0; fwd_stop<=0; state<=FILL_R; end','''            FILL_CHECK: state<=FILL_AR;
            FILL_AR: if(m_arready && !fill_blocked) begin beat<=0; fwd_stop<=0; state<=FILL_R; end''')
    OUT.mkdir(exist_ok=False);(OUT/'gpu_color_depth_cache.sv').write_text(s)
    shutil.copy2(BASE/'gpu_color_depth_cache.sv',OUT/'baseline.sv');shutil.copy2(__file__,OUT/'recipe.py')
    (OUT/'candidate.patch').write_text(''.join(difflib.unified_diff((BASE/'gpu_color_depth_cache.sv').read_text().splitlines(True),s.splitlines(True),fromfile='read-stage-v1',tofile='read-stage-v2')))
    (OUT/'prepared.json').write_text(json.dumps(dict(parent_source_sha256=sha(BASE/'gpu_color_depth_cache.sv'),source_sha256=sha(OUT/'gpu_color_depth_cache.sv'),read_lookup=1,fill_hazard=1,default_fill_hazard=0,added_data_register_bits=1,added_states=1,private_only=True,fit_run=False,deployed=False),indent=2)+'\n')
    print(sha(OUT/'gpu_color_depth_cache.sv'))
if __name__=='__main__':main()
