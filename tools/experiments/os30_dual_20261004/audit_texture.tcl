# Read-only postfit targets for the early texture-offer and decode-bank changes.
package require ::quartus::project
package require ::quartus::sta
if {[llength $quartus(args)] != 2} {error "Expected project and output directory"}
set destination [lindex $quartus(args) 1]
project_open [lindex $quartus(args) 0]
create_timing_netlist -model slow
read_sdc
set pointer_nodes [open [file join $destination pointer_nodes.tsv] w]
puts $pointer_nodes "node\ttype\tlocation\tdirect_input"
foreach_in_collection node [get_nodes -nowarn {*decode_pingpong_read_bank*}] {
    set name [get_node_info $node -name]
    foreach edge [get_node_info $node -synch_edges] {
        puts $pointer_nodes "$name\t[get_node_info $node -type]\t[get_node_info $node -location]\t[get_node_info [get_edge_info $edge -src] -name]"
    }
}
close $pointer_nodes
set summary [open [file join $destination summary.tsv] w]
puts $summary "model\ttemperature\tgroup\tendpoints\tslack\tfrom\tto"
set groups {
    texture_a_address {*|gpu_tex_cache:tex_cache|*data_mem*porta_address_reg*} {*}
    texture_b_address {*|gpu_tex_cache:tex_cache|*data_mem*portb_address_reg*} {*}
    texture_tag_to_a {*|gpu_tex_cache:tex_cache|*data_mem*porta_address_reg*} {*|gpu_tex_cache:tex_cache|*tagv_mem*}
    decode_bank_capture {*|VexiiRiscv:cpu|decode_pingpong_bank*} {*}
    decode_pointer_capture {*|VexiiRiscv:cpu|decode_pingpong_read_bank*} {*}
    decode_stage2_capture {*|VexiiRiscv:cpu|decode_ctrls_2_up*} {*}
    decode_stage3_capture {*|VexiiRiscv:cpu|decode_ctrls_3_up*} {*}
    decode_head_masks {*|VexiiRiscv:cpu|decode_ctrls_1_up_LANE_SEL*} {*}
    packer_mode_outputs {*|VexiiRiscv:cpu|*} {*|VexiiRiscv:cpu|FpuPackerPlugin_logic_pip_node_1_s0_VALUE_mode*}
    packer_mode_float_rf {*|VexiiRiscv:cpu|RegFileMem_1:float_RegFilePlugin_logic_regfile_fpga|*} {*|VexiiRiscv:cpu|FpuPackerPlugin_logic_pip_node_1_s0_VALUE_mode*}
}
foreach_in_collection corner [get_available_operating_conditions] {
    set model [get_operating_conditions_info $corner -model]
    if {$model ne "slow"} {continue}
    set_operating_conditions $corner
    update_timing_netlist
    set temperature [get_operating_conditions_info $corner -temperature]
    foreach {label to_pattern from_pattern} $groups {
        set targets [get_registers $to_pattern]
        set sources [get_registers $from_pattern]
        set count [get_collection_size $targets]
        set paths [get_timing_paths -setup -from $sources -to $targets -npaths 1]
        if {[get_collection_size $paths] == 0} {
            puts $summary "$model\t$temperature\t$label\t$count\tno_paths\t\t"
            continue
        }
        foreach_in_collection path $paths {
            puts $summary "$model\t$temperature\t$label\t$count\t[get_path_info $path -slack]\t[get_node_info -name [get_path_info $path -from]]\t[get_node_info -name [get_path_info $path -to]]"
        }
        report_timing -setup -from $sources -to $targets -npaths 8 -nworst 1 -detail full_path \
            -file [file join $destination "${model}_${temperature}_${label}.rpt"]
    }
}
close $summary
delete_timing_netlist
project_close
