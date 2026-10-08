#!/usr/bin/env python3
"""Read-only STA export from copied completed compact-v13 netlist; never fit."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
P=ROOT/'docs/measurements/os30-fps25-20261005/phase10'
BASE=P/'compact-lod5-core-v13/fit-seed2';OUT=P/'compact-timing-audit-v13'
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
set targeted_file [open [file join $destination targeted.tsv] w]
puts $targeted_file "model\ttemperature\tlabel\tstart_registers\tend_registers\tpath_count"
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
 report_timing -setup -from [get_registers {*|gpu_core:gpu|ring_rd*}] -to [get_registers {*|axi_periph_slave:periph|periph_rd_data_r*}] -npaths 12 -nworst 1 -detail full_path -file [file join $destination "${stem}_ring_status.rpt"]
 report_timing -setup -from [get_registers {*|execute_ctrl4_up_LsuL1Plugin_logic_BANKS_MUXES_lane0_0*}] -to [get_registers {*|RegFileMem:integer_RegFilePlugin_logic_regfile_fpga|*}] -npaths 20 -nworst 1 -detail full_path -file [file join $destination "${stem}_cpu_writeback.rpt"]
 report_timing -setup -from [get_registers {*|gpu_tex_cache:tex_cache|altsyncram:tagv_mem_rtl_0|*}] -to [get_registers {*|gpu_tex_cache:tex_cache|altsyncram:data_mem_rtl_0|*}] -npaths 20 -nworst 1 -detail full_path -file [file join $destination "${stem}_texture_ram.rpt"]
 report_timing -setup -to [get_registers {*|audio_mixer:audio_mixer_inst|accum_*}] -npaths 20 -nworst 1 -detail full_path -file [file join $destination "${stem}_audio_accum.rpt"]
 report_timing -setup -from [get_registers {*|dv_du*}] -to [get_registers {*|spanprod_depth_du*}] -npaths 4 -detail full_path -file [file join $destination "${stem}_gradient.rpt"]
 # Explicit old/new cones. A removed combinational path may legitimately
 # be empty; record matched registers and the independent timing-path count.
 foreach {label starts ends} {
  span_count_drain {{*|gpu_core:gpu|altsyncram:spanprod_count_rtl_0|*}} {{*|gpu_core:gpu|z_acc_mask*} {*|gpu_core:gpu|fbwq_req_*}}
  span_flags_drain {{*|gpu_core:gpu|spanprod_cur_count_valid*} {*|gpu_core:gpu|spanprod_cur_valid*}} {{*|gpu_core:gpu|z_acc_mask*} {*|gpu_core:gpu|fbwq_req_*}}
  span_step_state {{*|gpu_core:gpu|spanprod_calc_step*}} {{*|gpu_core:gpu|state*}}
  span_step_values {{*|gpu_core:gpu|spanprod_calc_step*}} {{*|gpu_core:gpu|spanprod_*_start_r*} {*|gpu_core:gpu|spanprod_z_addr_r*}}
  cpu_rs3_fetch {{*|cpu_system:cpu|*|decode_ctrls_2_up_RS3_PHYS*}} {{*|cpu_system:cpu|*|fetch_logic_ctrls_*} {*|cpu_system:cpu|*|AlignerPlugin_logic_buffer*} {*|cpu_system:cpu|*|PcPlugin_logic_harts_0*}}
 } {
  set start_registers [get_registers $starts]
  set end_registers [get_registers $ends]
  set selected_paths [get_timing_paths -setup -from $start_registers -to $end_registers -npaths 20 -nworst 1]
  puts $targeted_file "$model\t$temperature\t$label\t[get_collection_size $start_registers]\t[get_collection_size $end_registers]\t[get_collection_size $selected_paths]"
  report_timing -setup -from $start_registers -to $end_registers -npaths 20 -nworst 1 -detail full_path -file [file join $destination "${stem}_${label}.rpt"]
 }
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
close $targeted_file
delete_timing_netlist
project_close
'''
def main():
 assert sys.argv[1:]==['export-after-root-confirmation']
 assert (BASE/'complete.json').exists(), 'Wait for completed fit and STA first'
 assert (BASE.parent/'final-manifest.json').exists(), 'Wait for the sealed routed result'
 assert not json.loads((BASE.parent/'result.json').read_text())['exceeds_90mhz'], 'No contingency export is needed when both slow corners exceed 90 MHz'
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
