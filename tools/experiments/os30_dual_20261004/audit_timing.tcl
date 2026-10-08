# Post-fit read-only timing analysis. No constraints, assignments or RTL edits.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {
    error "Usage: audit_timing.tcl <project> <output-directory>"
}
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open $project
create_timing_netlist -model slow
read_sdc
set placements [open [file join $destination registers.tsv] w]
puts $placements "name\tlocation"
foreach_in_collection node [get_registers *] {
    puts $placements "[get_node_info -name $node]\t[get_node_info -location $node]"
}
close $placements
set boundaries [open [file join $destination mmio_boundaries.tsv] w]
puts $boundaries "name\ttype\tlocation"
foreach_in_collection node [get_nodes -nowarn {*periph*sysreg_rdata*}] {
    set name [get_node_info $node -name]
    if {[string match {*|combout} $name]} {
        puts $boundaries "$name\t[get_node_info $node -type]\t[get_node_info $node -location]"
    }
}
close $boundaries
set cpu_boundaries [open [file join $destination cpu_boundaries.tsv] w]
puts $cpu_boundaries "group\tname\ttype\tlocation"
foreach {label pattern} {
    fp_rs1_early {*fp_rs1_priority_early_data*}
    fp_rs1_late {*fp_rs1_priority_late_data*}
    fp_rs3_early {*fp_rs3_priority_early_data*}
    fp_rs3_late {*fp_rs3_priority_late_data*}
    fp_rs2_early {*fp_rs2_priority_early_data*}
    fp_rs2_late {*fp_rs2_priority_late_data*}
    integer_rs1_early {*integer_rs1_priority_early_data*}
    integer_rs1_late {*integer_rs1_priority_late_data*}
    decode_stage2_ready {*decode_ctrls_1_down_ready*}
    aligner_last0 {*AlignerPlugin_logic_buffer_readers_0_lastFromBuffer*}
    aligner_last1 {*AlignerPlugin_logic_buffer_readers_1_lastFromBuffer*}
} {
    foreach_in_collection node [get_nodes -nowarn $pattern] {
        set name [get_node_info $node -name]
        if {[string match {*|combout} $name]} {
            puts $cpu_boundaries "$label\t$name\t[get_node_info $node -type]\t[get_node_info $node -location]"
        }
    }
}
close $cpu_boundaries
set summary [open [file join $destination summary.tsv] w]
puts $summary "model\ttemperature\tgroup\tendpoints\tslack\tfrom\tto"
set groups {
    cpu_all {*|VexiiRiscv:cpu|*}
    cpu_execute {*|VexiiRiscv:cpu|execute*}
    cpu_float {*|VexiiRiscv:cpu|*float*}
    cpu_float_rs1 {*|VexiiRiscv:cpu|execute_ctrl2_up_float_RS1_lane0*}
    cpu_float_rs3 {*|VexiiRiscv:cpu|execute_ctrl2_up_float_RS3_lane0*}
    cpu_float_rs2 {*|VexiiRiscv:cpu|execute_ctrl2_up_float_RS2_lane0*}
    cpu_src1 {*|VexiiRiscv:cpu|execute_ctrl2_up_early0_SrcPlugin_SRC1_lane0*}
    cpu_fp_match {*|VexiiRiscv:cpu|fp_match_*}
    cpu_int_match {*|VexiiRiscv:cpu|int_rs2_write_match_*}
    cpu_fpu {*|VexiiRiscv:cpu|Fpu*}
    cpu_integer {*|VexiiRiscv:cpu|integer*}
    cpu_integer_regfile {*|VexiiRiscv:cpu|RegFileMem:integer*}
    cpu_fetch {*|VexiiRiscv:cpu|fetch*}
    cpu_aligner {*|VexiiRiscv:cpu|AlignerPlugin*}
    cpu_dispatch {*|VexiiRiscv:cpu|DispatchPlugin*}
    cpu_lsu {*|VexiiRiscv:cpu|Lsu*}
    gpu {*|gpu_core:gpu|*}
    render_cache {*|gpu_color_depth_cache:*|*}
    memory_fabric {*|axi_sdram*|*}
    peripheral {*|axi_periph_slave:periph|*}
    sdram_burst {*|io_sdram:isr0|burst_data*}
    sdram_word {*|io_sdram:isr0|word_q*}
    audio {*|audio_mixer:*|*}
}
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    if {$model ne "slow"} {continue}
    set_operating_conditions $corner
    update_timing_netlist
    set temperature [get_operating_conditions_info $corner -temperature]
    foreach {label pattern} $groups {
        set endpoints [get_registers $pattern]
        set count [get_collection_size $endpoints]
        if {$count == 0} {
            puts $summary "$model\t$temperature\t$label\t0\tno_paths\t\t"
            continue
        }
        set paths [get_timing_paths -setup -to $endpoints -npaths 1]
        if {[get_collection_size $paths] == 0} {
            puts $summary "$model\t$temperature\t$label\t$count\tno_paths\t\t"
            continue
        }
        foreach_in_collection path $paths {
            set slack [get_path_info $path -slack]
            set source [get_node_info -name [get_path_info $path -from]]
            set target [get_node_info -name [get_path_info $path -to]]
            puts $summary "$model\t$temperature\t$label\t$count\t$slack\t$source\t$target"
            puts "GROUP $model $temperature $label $slack $source -> $target"
        }
        report_timing -setup -to $endpoints -npaths 12 -nworst 1 -detail full_path \
            -file [file join $destination "${model}_${temperature}_${label}.rpt"]
    }
}
close $summary
delete_timing_netlist
project_close
