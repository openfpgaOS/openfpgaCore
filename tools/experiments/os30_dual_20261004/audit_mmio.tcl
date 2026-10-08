# Read-only mux mapping audit for the completed peripheral-v1 experiment.
package require ::quartus::project
package require ::quartus::sta
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
project_open $project
create_timing_netlist -model slow
read_sdc
update_timing_netlist
set output [open [file join $destination nodes.tsv] w]
set summary [open [file join $destination summary.tsv] w]
puts $summary "node\ttype\tlocation\tsources"
puts $output "node\trelation\tother"
set found 0
foreach pattern {*periph*periph_rd_data_r*~24* *periph*periph_rd_data_r*~25* *periph*Mux206~*} {
    set nodes [get_nodes $pattern]
    foreach_in_collection node $nodes {
        set name [get_node_info $node -name]
        set kind [get_node_info $node -type]
        if {![string match {*|combout} $name]} {continue}
        incr found
        set inputs [get_fanins -synch [get_nodes $name]]
        puts $summary "$name\t$kind\t[get_node_info $node -location]\t[get_collection_size $inputs]"
        foreach edge [get_node_info $node -synch_edges] {
            set previous [get_edge_info $edge -src]
            puts $output "$name\tdirect_input\t[get_node_info $previous -name]"
            foreach before [get_node_info $previous -synch_edges] {
                set previous2 [get_edge_info $before -src]
                puts $output "$name\tprevious_input\t[get_node_info $previous2 -name]"
            }
        }
        foreach_in_collection input $inputs {
            puts $output "$name\tregister_source\t[get_node_info $input -name]"
        }
    }
}
puts $summary "Matched combinational nodes: $found"
close $output
close $summary
if {$found == 0} {error "No measured MMIO nodes matched"}
delete_timing_netlist
project_close
