# Read-only rotating-bank timing. No assignment or constraint changes.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {
    error "Usage: audit_pingpong.tcl <project> <output-directory>"
}
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open $project
create_timing_netlist -model slow
read_sdc
set summary [open [file join $destination summary.tsv] w]
puts $summary "model\ttemperature\tgroup\tsources\tendpoints\tslack\tfrom\tto"
set groups {
    bank_capture {*} {*|VexiiRiscv:cpu|decode_pingpong_bank*}
    bank_outputs {*|VexiiRiscv:cpu|decode_pingpong_bank*} {*|VexiiRiscv:cpu|*}
    pointer_outputs {*|VexiiRiscv:cpu|decode_pingpong_read_bank*} {*|VexiiRiscv:cpu|*}
}
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    if {$model ne "slow"} {continue}
    set_operating_conditions $corner
    update_timing_netlist
    set temperature [get_operating_conditions_info $corner -temperature]
    foreach {label source_pattern endpoint_pattern} $groups {
        set sources [get_registers $source_pattern]
        set endpoints [get_registers $endpoint_pattern]
        set source_count [get_collection_size $sources]
        set endpoint_count [get_collection_size $endpoints]
        if {$source_count == 0 || $endpoint_count == 0} {
            error "Expected ping-pong registers not found: $label ($source_count/$endpoint_count)"
        }
        set paths [get_timing_paths -setup -from $sources -to $endpoints -npaths 1]
        foreach_in_collection path $paths {
            set slack [get_path_info $path -slack]
            set source [get_node_info -name [get_path_info $path -from]]
            set target [get_node_info -name [get_path_info $path -to]]
            puts $summary "$model\t$temperature\t$label\t$source_count\t$endpoint_count\t$slack\t$source\t$target"
        }
        report_timing -setup -from $sources -to $endpoints -npaths 12 -nworst 1 -detail full_path \
            -file [file join $destination "${model}_${temperature}_${label}.rpt"]
    }
}
close $summary
delete_timing_netlist
project_close
