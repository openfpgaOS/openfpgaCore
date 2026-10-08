#!/usr/bin/env python3
"""Read the private failed-fit post-map bank census; do not run a fitter."""
from pathlib import Path
import json,hashlib,subprocess
ROOT=Path(__file__).resolve().parents[3]
ART=ROOT/'docs/measurements/os30-dual-20261004'
FIT=ART/'fit-rvc-skid-second-buffer-draft-v1'
OUT=ART/'second-skid-bank-map-audit-v1'
OUT.mkdir(exist_ok=False)
job=Path(json.loads((FIT/'results.json').read_text())[0]['directory'])
tcl=OUT/'read_map.tcl'
tcl.write_text('''package require ::quartus::project
package require ::quartus::sta
project_open ap_core
create_timing_netlist -post_map -model slow
set output [open [file join [lindex $quartus(args) 1] registers.tsv] w]
puts $output "name"
foreach_in_collection node [get_registers *] {
  puts $output [get_node_info -name $node]
}
close $output
delete_timing_netlist
project_close
''')
command=json.loads((ART/'timing-audit-fullcache-v1/manifest.json').read_text())['command'][:]
command[command.index('--name')+1]='os30-second-bank-map-audit'
command[command.index('-w')+1]=str(job)
command[command.index('-t')+1]=str(tcl)
command[-1]=str(OUT)
paths=[job/'ap_core.qsf',job/'core_constraints.sdc',FIT/'frozen-sources.json',FIT/'experiment.json']
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
before={str(p):sha(p) for p in paths}
with (OUT/'run.log').open('w') as log:result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
unchanged=all(sha(Path(p))==v for p,v in before.items())
(OUT/'manifest.json').write_text(json.dumps(dict(command=command,inputs=before,input_hashes_unchanged=unchanged,exit_code=result.returncode,scope='Post-map register census only, no fitter/timing claim.'),indent=2)+'\n')
assert result.returncode==0 and unchanged
print(str(OUT))
