#!/usr/bin/env python3
"""Independent count observer/state closure for the private CRAM port edit."""
from pathlib import Path
import hashlib,json,re
ROOT=Path(__file__).resolve().parents[3];ART=ROOT/'docs/measurements/os30-dual-20261004'
SRC=ART/'port-idle-count-v1';OUT=ART/'port-idle-count-emitted-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    old=(SRC/'reference.v').read_text();new=(SRC/'cpu_target_port_per.v').read_text()
    assert sha(SRC/'reference.v')=='119c0f1f6baa76448e483a6f7782ae19119478bca91bebbc2e9206922ec3339d'
    assert sha(SRC/'cpu_target_port_per.v')=='51105011bd13441fb754b76394f2cdee38542ad085474f2ec5ef811f4b6514a3'
    before='''        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            if (wr_grant) begin
                wr_burst_len   <= per_awlen;
                wr_burst_count <= 8'b0;
'''
    after='''        WR_IDLE: begin
            m_awvalid <= 1'b0;
            m_wvalid  <= 1'b0;
            // The count is unobserved in IDLE; every grant starts at zero.
            // Clear from local state, without the upstream grant/address cone.
            wr_burst_count <= 8'b0;
            if (wr_grant) begin
                wr_burst_len   <= per_awlen;
'''
    assert old.count(before)==new.count(after)==1 and old.replace(before,after,1)==new
    lines=[x.strip() for x in old.splitlines() if re.search(r'\bwr_burst_count\b',x)]
    assert len(lines)==6 and lines[0]=='reg [7:0] wr_burst_count;'
    assert lines[1]=='wire wr_beat_is_last = (wr_burst_count == wr_burst_len);'
    assert lines[2:]==["wr_burst_count     <= 8'b0;","wr_burst_count <= 8'b0;","wr_burst_count <= wr_burst_count + 8'd1;","wr_burst_count <= wr_burst_count + 8'd1;"]
    assert len(re.findall(r'\bwr_beat_is_last\b',old))==3
    idle=old.split('        WR_IDLE: begin',1)[1].split('        WR_AW: begin',1)[0]
    assert 'wr_beat_is_last' not in idle
    assert re.findall(r'wr_state\s*<=\s*(\w+)',idle)==['WR_AW']
    assert 'if (per_wvalid)' in idle and 'm_wlast  <= per_wlast;' in idle
    aw=old.split('        WR_AW: begin',1)[1].split('        WR_W: begin',1)[0]
    w=old.split('        WR_W: begin',1)[1].split('        WR_B: begin',1)[0]
    assert aw.count('if (wr_beat_is_last)')==w.count('if (wr_beat_is_last)')==1
    assert 'if (m_wready && m_wvalid)' in aw and 'if (m_wready && m_wvalid)' in w
    assert 'wr_burst_count' not in old.split('        WR_B: begin',1)[1]
    cases=0
    for state in range(4):
      for a in range(256):
       for b in (range(256) if state==0 else (a,)):
        for grant in range(2):
         for accept in range(2):
          for reset in range(2):
           aa=0 if reset or (state==0 and grant) else ((a+1)&255) if state in (1,2) and accept else a
           bb=0 if reset or state==0 else ((b+1)&255) if state in (1,2) and accept else b
           if reset or state!=0 or grant:assert aa==bb
           # Otherwise both remain IDLE and none of their output/state
           # expressions read either count; the relation permits a!=b.
           cases+=1
    m=json.loads((SRC/'manifest.json').read_text())
    assert m['full_burst_cases_per_source']==3072 and m['runtime_logs_identical']
    for p,h in m['runtime_logs'].items():assert sha(Path(p))==h
    assert (SRC/'reference-runtime.log').read_bytes()==(SRC/'candidate-runtime.log').read_bytes()
    OUT.mkdir(exist_ok=False)
    result=dict(passed=True,candidate_sha256=sha(SRC/'cpu_target_port_per.v'),exact_only_idle_count_clear_relocation=True,
      sole_observer_used_only_in_aw_and_w=True,all_exits_from_idle_clear_both_counts_before_use=True,
      reset_increment_length_and_all_interface_updates_unchanged=True,
      relational_cases_including_reset=cases,retained_actual_module_burst_cases_per_source=3072,
      full_runtime_logs_identical=True,counter_may_differ_only_while_unobserved_idle=True,
      no_extra_registers_or_cycles=True,source_manifest_sha256=sha(SRC/'manifest.json'))
    (OUT/'manifest.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
