#!/usr/bin/env python3
"""Read-only STA export from copied completed compact-LOD5 netlist; no fit."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v1/fit-seed2';OUT=P/'compact-timing-audit-v1'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 assert (BASE/'complete.json').exists()
 OUT.mkdir(exist_ok=False)
 source=BASE/'jobs/pocket-os30-s2';shutil.copytree(source,OUT/'job')
 job=OUT/'job/src/fpga/targets/pocket';tcl=OUT/'audit.tcl'
 tcl.write_text('''package require ::quartus::project
package require ::quartus::sta
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open [lindex $quartus(args) 0]
create_timing_netlist -model slow
read_sdc
foreach_in_collection corner [get_available_operating_conditions] {
 set model [get_operating_conditions_info $corner -model]
 set temperature [get_operating_conditions_info $corner -temperature]
 if {$model ne "slow"} {continue}
 set_operating_conditions $corner
 update_timing_netlist
 set stem "${model}_${temperature}"
 report_timing -setup -npaths 40 -detail full_path -file [file join $destination "${stem}_global.rpt"]
 foreach {label pattern} {cpu {*|cpu_system:cpu|*} geometry {*|geometry_load_alias:geometry_load|*} gpu_internal {*|gpu_core:gpu|*}} {
  set endpoints [get_registers $pattern]
  report_timing -setup -to $endpoints -npaths 12 -detail full_path -file [file join $destination "${stem}_${label}.rpt"]
 }
 report_timing -setup -from [get_registers {*|dv_du*}] -to [get_registers {*|spanprod_depth_du*}] -npaths 4 -detail full_path -file [file join $destination "${stem}_gradient.rpt"]
}
delete_timing_netlist
project_close
''')
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 cmd=fit.docker(OUT,'pocket',job,['quartus_sta','-t',str(tcl),'ap_core',str(OUT/'reports')])
 inputs={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file() and (p.suffix in ['.qsf','.sdc','.qpf'] or 'db' in p.parts or 'incremental_db' in p.parts)}
 (OUT/'invocation.json').write_text(json.dumps(dict(command=cmd,scope=__doc__,completed_parent_sha256=sha(BASE/'complete.json'),inputs_sha256=inputs,tcl_sha256=sha(tcl)),indent=2)+'\n')
 shutil.copy2(__file__,OUT/'recipe.py')
 with (OUT/'sta.log').open('x') as o:subprocess.run(cmd,stdout=o,stderr=subprocess.STDOUT,check=True)
if __name__=='__main__':main()
