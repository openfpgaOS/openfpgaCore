# Read-only endpoint census; no timing exceptions or input modifications.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {error "Expected project and output directory"}
set destination [lindex $quartus(args) 1]
project_open [lindex $quartus(args) 0]
create_timing_netlist -model slow
read_sdc
set output [open [file join $destination endpoints.tsv] w]
set summary [open [file join $destination summary.tsv] w]
puts $output "model\ttemperature\tslack\tfrom\tto"
puts $summary "model\ttemperature\tpaths_examined\tbelow_threshold\tlast_slack\tcomplete"
set threshold -1.111111111
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    if {$model ne "slow"} {continue}
    set_operating_conditions $corner
    update_timing_netlist
    set temperature [get_operating_conditions_info $corner -temperature]
    set paths [get_timing_paths -setup -to [get_registers *] -npaths 1000 -nworst 1]
    set count [get_collection_size $paths]
    set below 0
    set last 0
    foreach_in_collection path $paths {
        set last [get_path_info $path -slack]
        if {$last < $threshold} {
            incr below
            puts $output "$model\t$temperature\t$last\t[get_node_info -name [get_path_info $path -from]]\t[get_node_info -name [get_path_info $path -to]]"
        }
    }
    set complete [expr {$count < 1000 || $last >= $threshold}]
    puts $summary "$model\t$temperature\t$count\t$below\t$last\t$complete"
    if {!$complete} {error "Endpoint bound reached below threshold; census incomplete"}
    report_timing -setup -to [get_registers *] -npaths [expr {$below+1}] -nworst 1 -detail full_path \
        -file [file join $destination "${model}_${temperature}_below_threshold.rpt"]
}
close $output
close $summary
delete_timing_netlist
project_close
