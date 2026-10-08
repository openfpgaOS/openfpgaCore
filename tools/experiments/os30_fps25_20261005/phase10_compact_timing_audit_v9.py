#!/usr/bin/env python3
"""Read-only STA export from copied completed compact-v9 netlist; never fit."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v9/fit-seed2';OUT=P/'compact-timing-audit-v9'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
TCL='''package require ::quartus::project
package require ::quartus::sta
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open [lindex $quartus(args) 0]
create_timing_netlist -model slow
read_sdc
set endpoints_file [open [file join $destination endpoints.tsv] w]
set census_file [open [file join $destination census.tsv] w]
puts $endpoints_file "model\ttemperature\tslack\trelationship\teffective_period\tfrom\tto"
puts $census_file "model\ttemperature\texamined\tbelow_90\tlast_slack\tcomplete"
set threshold [expr {10.0-1000.0/90.0}]
foreach_in_collection corner [get_available_operating_conditions] {
 set model [get_operating_conditions_info $corner -model]
 set temperature [get_operating_conditions_info $corner -temperature]
 if {$model ne "slow"} {continue}
 set_operating_conditions $corner
 update_timing_netlist
 set stem "${model}_${temperature}"
 report_timing -setup -npaths 40 -detail full_path -file [file join $destination "${stem}_global.rpt"]
 foreach {label pattern} {cpu {*|cpu_system:cpu|*} geometry {*|geometry_load_alias:geometry_load|*} gpu_internal {*|gpu_core:gpu|*} sdram {*|io_sdram:isr0|*} render_cache {*|gpu_color_depth_cache:gpu_render_cache|*}} {
  set endpoints [get_registers $pattern]
  report_timing -setup -to $endpoints -npaths 40 -nworst 1 -detail full_path -file [file join $destination "${stem}_${label}.rpt"]
 }
 report_timing -setup -to [get_registers {*|decode_ctrls_1_up_Decode_INSTRUCTION_0*}] -npaths 32 -nworst 1 -detail full_path -file [file join $destination "${stem}_rvc.rpt"]
 report_timing -setup -from [get_registers {*|decode_ctrls_2_up_RS*}] -to [get_registers {*|cpu_system:cpu|*}] -npaths 20 -nworst 1 -detail full_path -file [file join $destination "${stem}_dispatch.rpt"]
 report_timing -setup -from [get_registers {*|AlignerPlugin_logic_buffer*}] -to [get_registers {*|PcPlugin_logic_harts_0*}] -npaths 12 -nworst 1 -detail full_path -file [file join $destination "${stem}_aligner_pc.rpt"]
 report_timing -setup -from [get_registers {*|dv_du*}] -to [get_registers {*|spanprod_depth_du*}] -npaths 4 -detail full_path -file [file join $destination "${stem}_gradient.rpt"]
 # One worst path per register endpoint; expand until the complete sub90
 # set is covered. No report-depth cap may silently imply timing closure.
 set limit 1000
 while {1} {
  set paths [get_timing_paths -setup -to [get_registers *] -npaths $limit -nworst 1]
  set count [get_collection_size $paths]
  set last 0
  foreach_in_collection path $paths {set last [get_path_info $path -slack]}
  if {$count < $limit || $last >= $threshold} {break}
  if {$limit >= 32000} {error "Incomplete endpoint census at 32000 paths"}
  set limit [expr {$limit*2}]
 }
 set below 0
 foreach_in_collection path $paths {
  set slack [get_path_info $path -slack]
  set relation [get_path_info $path -clock_relationship]
  if {abs($relation-10.0)<0.000001 && $slack<$threshold} {
   incr below
   set period [expr {$relation-$slack}]
   puts $endpoints_file "$model\t$temperature\t$slack\t$relation\t$period\t[get_node_info -name [get_path_info $path -from]]\t[get_node_info -name [get_path_info $path -to]]"
  }
 }
 puts $census_file "$model\t$temperature\t$count\t$below\t$last\t1"
 # Export every endpoint below the threshold with complete routing detail.
 # Other-clock paths may occur in this report and retain their relationship.
 report_timing -setup -to [get_registers *] -npaths $limit -nworst 1 -less_than_slack $threshold -detail full_path -file [file join $destination "${stem}_below90.rpt"]
}
close $endpoints_file
close $census_file
delete_timing_netlist
project_close
'''
def main():
 assert sys.argv[1:]==['export-after-root-confirmation']
 assert (BASE/'complete.json').exists(), 'Wait for completed fit and STA first'
 OUT.mkdir(exist_ok=False)
 source=BASE/'jobs/pocket-os30-s2';shutil.copytree(source,OUT/'job')
 job=OUT/'job/src/fpga/targets/pocket';tcl=OUT/'audit.tcl';tcl.write_text(TCL)
 sys.path.insert(0,str(ROOT/'tools'));import sweep_target_matrix as fit
 cmd=fit.docker(OUT,'pocket',job,['quartus_sta','-t',str(tcl),'ap_core',str(OUT/'reports')])
 inputs={str(p.relative_to(source)):sha(p) for p in source.rglob('*') if p.is_file() and (p.suffix in ['.qsf','.sdc','.qpf'] or 'db' in p.parts or 'incremental_db' in p.parts)}
 (OUT/'invocation.json').write_text(json.dumps(dict(command=cmd,scope=__doc__,completed_parent_sha256=sha(BASE/'complete.json'),inputs_sha256=inputs,tcl_sha256=sha(tcl),fit_invoked=False,endpoint_threshold_mhz=90),indent=2)+'\n')
 shutil.copy2(__file__,OUT/'recipe.py')
 with (OUT/'sta.log').open('x') as o:subprocess.run(cmd,stdout=o,stderr=subprocess.STDOUT,check=True)
 for name,digest in inputs.items():assert sha(source/name)==digest,name
if __name__=='__main__':main()
