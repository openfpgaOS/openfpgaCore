# Read-only input margin for an existing D-cache hit vector. No source change.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {
    error "Usage: audit_lsu_hit.tcl <project> <output-directory>"
}
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open $project
create_timing_netlist -model slow
read_sdc
set summary [open [file join $destination summary.tsv] w]
puts $summary "model\ttemperature\tgroup\tendpoints\tslack\tfrom\tto"
set endpoints [get_registers {*|VexiiRiscv:cpu|execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HITS_lane0*}]
set count [get_collection_size $endpoints]
if {$count < 2} {error "Existing two-way hit vector not found"}
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    if {$model ne "slow"} {continue}
    set_operating_conditions $corner
    update_timing_netlist
    set temperature [get_operating_conditions_info $corner -temperature]
    set paths [get_timing_paths -setup -to $endpoints -npaths 1]
    foreach_in_collection path $paths {
        set slack [get_path_info $path -slack]
        set source [get_node_info -name [get_path_info $path -from]]
        set target [get_node_info -name [get_path_info $path -to]]
        puts $summary "$model\t$temperature\thit_capture\t$count\t$slack\t$source\t$target"
    }
    report_timing -setup -to $endpoints -npaths 12 -nworst 1 -detail full_path \
        -file [file join $destination "${model}_${temperature}_hit_capture.rpt"]
    set shadows [get_registers -nowarn {*|VexiiRiscv:cpu|execute_ctrl4_up_LsuL1Plugin_logic_WAYS_HIT_lane0*}]
    set shadow_count [get_collection_size $shadows]
    if {$shadow_count > 0} {
        foreach label {shadow_capture shadow_outputs} {
            if {$label eq "shadow_capture"} {
                set paths [get_timing_paths -setup -to $shadows -npaths 1]
                report_timing -setup -to $shadows -npaths 12 -nworst 1 -detail full_path \
                    -file [file join $destination "${model}_${temperature}_${label}.rpt"]
            } else {
                set paths [get_timing_paths -setup -from $shadows -npaths 1]
                report_timing -setup -from $shadows -npaths 12 -nworst 1 -detail full_path \
                    -file [file join $destination "${model}_${temperature}_${label}.rpt"]
            }
            foreach_in_collection path $paths {
                set slack [get_path_info $path -slack]
                set source [get_node_info -name [get_path_info $path -from]]
                set target [get_node_info -name [get_path_info $path -to]]
                puts $summary "$model\t$temperature\t$label\t$shadow_count\t$slack\t$source\t$target"
            }
        }
    }
}
close $summary
delete_timing_netlist
project_close
