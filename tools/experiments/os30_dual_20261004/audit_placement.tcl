# Read existing fitted node locations; no assignments or timing constraints change.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {
    error "Usage: audit_placement.tcl <project> <output-directory>"
}
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open [lindex $quartus(args) 0]
create_timing_netlist -model slow
set fd [open [file join $destination registers.tsv] w]
puts $fd "name\tlocation"
foreach_in_collection node [get_registers *] {
    puts $fd "[get_node_info -name $node]\t[get_node_info -location $node]"
}
close $fd
delete_timing_netlist
project_close
