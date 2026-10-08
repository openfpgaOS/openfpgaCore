# Read-only focused RAW frontend timing. No assignment or constraint changes.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {
    error "Usage: audit_frontend.tcl <project> <output-directory>"
}
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open $project
create_timing_netlist -model slow
read_sdc
set summary [open [file join $destination summary.tsv] w]
puts $summary "model\ttemperature\tgroup\tendpoints\tslack\tfrom\tto"
set groups {
    raw_all {*}
    raw_from_fetch {*|VexiiRiscv:cpu|fetch*}
    raw_from_aligner {*|VexiiRiscv:cpu|AlignerPlugin*}
    raw_from_execute {*|VexiiRiscv:cpu|execute*}
}
set endpoints [get_registers {*|VexiiRiscv:cpu|decode_ctrls_1_up_Decode_INSTRUCTION_RAW*}]
set count [get_collection_size $endpoints]
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    if {$model ne "slow"} {continue}
    set_operating_conditions $corner
    update_timing_netlist
    set temperature [get_operating_conditions_info $corner -temperature]
    foreach {label pattern} $groups {
        set sources [get_registers $pattern]
        set paths [get_timing_paths -setup -from $sources -to $endpoints -npaths 1]
        foreach_in_collection path $paths {
            set slack [get_path_info $path -slack]
            set source [get_node_info -name [get_path_info $path -from]]
            set target [get_node_info -name [get_path_info $path -to]]
            puts $summary "$model\t$temperature\t$label\t$count\t$slack\t$source\t$target"
        }
        report_timing -setup -from $sources -to $endpoints -npaths 12 -nworst 1 -detail full_path \
            -file [file join $destination "${model}_${temperature}_${label}.rpt"]
    }
}
close $summary
delete_timing_netlist
project_close
