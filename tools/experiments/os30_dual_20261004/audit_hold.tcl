# Read-only hold path reporting. No timing exceptions or constraint changes.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {
    error "Usage: audit_hold.tcl <project> <output-directory>"
}
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open $project
create_timing_netlist -model slow
read_sdc
set summary [open [file join $destination summary.tsv] w]
puts $summary "model\ttemperature\tcheck\tslack\tfrom\tto"
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    set temperature [get_operating_conditions_info $corner -temperature]
    set_operating_conditions $corner
    update_timing_netlist
    foreach_in_collection path [get_timing_paths -hold -npaths 12 -nworst 1] {
        puts $summary "$model\t$temperature\thold\t[get_path_info $path -slack]\t[get_node_info -name [get_path_info $path -from]]\t[get_node_info -name [get_path_info $path -to]]"
    }
    report_timing -hold -npaths 12 -nworst 1 -detail full_path \
        -file [file join $destination "${model}_${temperature}_hold.rpt"]
}
close $summary
delete_timing_netlist
project_close
