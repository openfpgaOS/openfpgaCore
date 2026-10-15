package require ::quartus::project
package require ::quartus::sta
set project [lindex $quartus(args) 0]
set dest [file normalize [lindex $quartus(args) 1]]
file mkdir $dest
project_open $project
create_timing_netlist -model slow
read_sdc
update_timing_netlist
set f [open [file join $dest connectivity.tsv] w]
puts $f "name\ttype\tlocation\tfanout_edge_count\tendpoint_fanouts"
set regs [get_registers {*isr0*u_dq_ddio_in*}]
set high_count 0
set high_fanout 0
set low_count 0
set low_fanout 0
foreach_in_collection r $regs {
 set name [get_node_info -name $r]
 set exact [get_registers [string map {"[" "?" "]" "?"} $name]]
 if {[get_collection_size $exact] != 1} {error "Expected exactly one matched register for $name"}
 set fanouts [get_fanouts $exact]
 set names [list]
 foreach_in_collection x $fanouts {lappend names [get_node_info -name $x]}
 puts $f "$name\t[get_node_info -type $r]\t[get_node_info -location $r]\t[llength [get_node_info -fanout_edges $r]]\t$names"
 if {[string match *dataout_h* $name]} {incr high_count;incr high_fanout [get_collection_size $fanouts]}
 if {[string match *dataout_l* $name]} {incr low_count;incr low_fanout [get_collection_size $fanouts]}
}
close $f
puts "HIGH_COUNT $high_count HIGH_FANOUT $high_fanout LOW_COUNT $low_count LOW_FANOUT $low_fanout"
set high [get_registers {*isr0*u_dq_ddio_in*dataout_h*}]
set low [get_registers {*isr0*u_dq_ddio_in*dataout_l*}]
set internal_low [get_registers {*isr0*u_dq_ddio_in*ddio_ina*}]
foreach_in_collection corner [get_available_operating_conditions] {
 set model [get_operating_conditions_info $corner -model]
 set temp [get_operating_conditions_info $corner -temperature]
 if {$model ne "slow"} {continue}
 set_operating_conditions $corner
 update_timing_netlist
 report_timing -setup -from [get_ports {dram_dq[*]}] -to $internal_low -npaths 20 -detail full_path -file [file join $dest "${model}_${temp}_input_low_setup.rpt"]
 report_timing -setup -from [get_ports {dram_dq[*]}] -to $high -npaths 20 -detail full_path -file [file join $dest "${model}_${temp}_input_high_setup.rpt"]
 report_timing -setup -from $low -npaths 20 -detail full_path -file [file join $dest "${model}_${temp}_low_output_setup.rpt"]
 report_timing -setup -from $high -npaths 20 -detail full_path -file [file join $dest "${model}_${temp}_high_output_setup.rpt"]
}
delete_timing_netlist
project_close
