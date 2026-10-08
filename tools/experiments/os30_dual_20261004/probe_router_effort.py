#!/usr/bin/env python3
"""Tiny private Cyclone V fit to distinguish parsed and ignored router effort."""
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'tools'))
import sweep_target_matrix as matrix
OUT=ROOT/'docs/measurements/os30-dual-20261004/router-effort-option-probe-v1'
OUT.mkdir(exist_ok=False)
results=[]
for value in ['2.0','4.0']:
    p=OUT/('effort'+value);p.mkdir()
    (p/'ap_core.v').write_text('''module ap_core(input clk, input reset, input [7:0] din, output reg [7:0] dout);
reg [7:0] a,b,c;
always @(posedge clk) begin
 if (reset) begin a<=0;b<=0;c<=0;dout<=0; end
 else begin a<=din+c;b<=a^din;c<=b+8'd3;dout<=c^a;end
end
endmodule
''')
    (p/'ap_core.qpf').write_text('PROJECT_REVISION = "ap_core"\n')
    (p/'ap_core.qsf').write_text('''set_global_assignment -name FAMILY "Cyclone V"
set_global_assignment -name DEVICE 5CEBA4F23C8
set_global_assignment -name TOP_LEVEL_ENTITY ap_core
set_global_assignment -name VERILOG_FILE ap_core.v
set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files
set_global_assignment -name SDC_FILE timing.sdc
set_global_assignment -name NUM_PARALLEL_PROCESSORS 2
set_global_assignment -name SEED 2
set_global_assignment -name ROUTER_TIMING_OPTIMIZATION_LEVEL MAXIMUM
set_global_assignment -name ROUTER_EFFORT_MULTIPLIER '''+value+'\n')
    (p/'timing.sdc').write_text('create_clock -period 10 [get_ports clk]\nderive_clock_uncertainty\n')
    (p/'parser.tcl').write_text('''package require ::quartus::project
project_open ap_core
puts "ROUTER_EFFORT=[get_global_assignment -name ROUTER_EFFORT_MULTIPLIER]"
project_close
''')
    record={'requested':value,'inputs':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file()},'commands':[]}
    for stage,cmd in [('parser',['quartus_sh','-t','parser.tcl']),('map',['quartus_map','ap_core']),('fit',['quartus_fit','ap_core'])]:
        invocation=matrix.docker(OUT,'pocket',p,cmd)
        with (p/(stage+'.log')).open('w') as stream:
            proc=subprocess.run(invocation,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        record['commands'].append({'stage':stage,'command':invocation,'returncode':proc.returncode})
        if proc.returncode:break
    log=(p/'fit.log').read_text(errors='replace') if (p/'fit.log').exists() else ''
    record['deprecation_warning_12288']='Warning (12288)' in log
    report=p/'output_files/ap_core.fit.rpt'
    record['router_effort_report_rows']=[l.strip() for l in report.read_text(errors='replace').splitlines() if 'Router Effort' in l or 'ROUTER_EFFORT_MULTIPLIER' in l] if report.exists() else []
    results.append(record)
    (OUT/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(record),flush=True)
