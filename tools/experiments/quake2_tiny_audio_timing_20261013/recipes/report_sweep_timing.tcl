# Check setup, hold, recovery, removal and pulse width at every corner.
# Run in a completed Quartus project directory:
#   quartus_sta -t /path/to/report_core_timing.tcl <project> <report-directory>
# Negative slack or missing setup/hold/pulse-width checks exit unsuccessfully.
# Unconstrained paths still require interface-specific review.

package require ::quartus::project
package require ::quartus::sta

if {[llength $quartus(args)] != 2} {
    error "Usage: report_core_timing.tcl <project> <report-directory>"
}
set project [lindex $quartus(args) 0]
set destination [lindex $quartus(args) 1]
file mkdir $destination
project_open $project
create_timing_netlist -model slow
read_sdc
update_timing_netlist

# Narrow, audited exception: the SDRAM DDIO rising sample is not consumed.
# Prove its exact routed connectivity afresh for every fitted seed. Never
# change the SDC or hide the raw paths; qualify only after excluding these
# sixteen disconnected sinks from an additional timing query.
set proof [open [file join $destination ddio_proof.tsv] w]
puts $proof "role\tbit\tname\ttype\tlocation\tfanout_count\tfanouts"
set ddio_regs [get_registers {*isr0*u_dq_ddio_in*}]
set counts [dict create high 0 low 0 falling 0]
foreach_in_collection reg $ddio_regs {
    set name [get_node_info -name $reg]
    if {![regexp {^core_top:ic\|io_sdram:isr0\|altddio_in:u_dq_ddio_in\|ddio_in_[^|]+:auto_generated\|(dataout_h|dataout_l|ddio_ina)\[([0-9]+)\](~DFFLO)?$} $name ignored signal bit suffix]} {
        error "Unexpected SDRAM DDIO node: $name"
    }
    set role [dict get [dict create dataout_h high dataout_l low ddio_ina falling] $signal]
    if {$bit < 0 || $bit > 15 || ($role eq "falling") != ($suffix eq "~DFFLO")} {error "Unexpected DDIO role: $name"}
    dict incr counts $role
    set exact [get_registers [string map {"[" "?" "]" "?"} $name]]
    if {[get_collection_size $exact] != 1} {error "Ambiguous DDIO node: $name"}
    set fanouts [get_fanouts $exact]
    set names [list]
    foreach_in_collection endpoint $fanouts {lappend names [get_node_info -name $endpoint]}
    puts $proof "$role\t$bit\t$name\t[get_node_info -type $reg]\t[get_node_info -location $reg]\t[get_collection_size $fanouts]\t[join $names ,]"
    if {$role eq "high" && [get_collection_size $fanouts] != 0} {error "Used high DDIO sample: $name"}
    if {$role eq "low" && [get_collection_size $fanouts] != 4} {error "Changed low DDIO connectivity: $name"}
}
close $proof
foreach role {high low falling} {if {[dict get $counts $role] != 16} {error "Expected16 DDIO $role nodes"}}
set high_ddio [get_registers {*isr0*u_dq_ddio_in*dataout_h*}]
set low_ddio [get_registers {*isr0*u_dq_ddio_in*dataout_l*}]
set falling_ddio [get_registers {*isr0*u_dq_ddio_in*ddio_ina*}]
set all_sinks [get_keepers *]
set high_sinks [get_keepers {*isr0*u_dq_ddio_in*dataout_h*}]
if {[get_collection_size $all_sinks] != [get_collection_size [get_registers *]] + [get_collection_size [get_ports *]] || [get_collection_size $high_sinks] != 16} {
    error "Unexpected timing endpoint inventory"
}
set used_sinks [remove_from_collection $all_sinks $high_sinks]
if {[get_collection_size $used_sinks] != [get_collection_size $all_sinks] - 16} {error "Incorrect unused sink removal"}
set ddio_checks [open [file join $destination ddio_checks.tsv] w]
puts $ddio_checks "model\ttemperature\tcheck\tslack"

set summary [open [file join $destination summary.tsv] w]
set clock_summary [open [file join $destination clocks.tsv] w]
set crossing_summary [open [file join $destination cross_setup.tsv] w]
puts $summary "model\ttemperature\tcheck\tslack"
puts $clock_summary "model\ttemperature\tcheck\tclock\tslack"
puts $crossing_summary "model\ttemperature\tsource_kind\tsource\ttarget\tslack\tused_slack"
set failed 0
set corner_count 0
set conditions [open [file join $destination conditions.tsv] w]
puts $conditions "model\ttemperature"
foreach_in_collection corner [get_available_operating_conditions] {
    incr corner_count
    set_operating_conditions $corner
    update_timing_netlist
    set model [get_operating_conditions_info $corner -model]
    set temperature [get_operating_conditions_info $corner -temperature]
    puts $conditions "$model\t$temperature"
    report_clock_fmax_summary -file [file join $destination "${model}_${temperature}_fmax.rpt"]
    foreach check {high_output low_input low_output} {
        switch $check {
            high_output {set args [list -from $high_ddio]}
            low_input {set args [list -from [get_ports {dram_dq[*]}] -to $falling_ddio]}
            low_output {set args [list -from $low_ddio]}
        }
        set paths [get_timing_paths -setup {*}$args -npaths 1]
        set slack no_paths
        foreach_in_collection path $paths {set slack [get_path_info $path -slack]}
        puts $ddio_checks "$model\t$temperature\t$check\t$slack"
        report_timing -setup {*}$args -npaths 20 -detail full_path \
            -file [file join $destination "${model}_${temperature}_ddio_${check}_setup.rpt"]
        if {$check eq "high_output"} {
            if {$slack ne "no_paths"} {error "High DDIO sample has a timing consumer"}
        } elseif {$slack eq "no_paths" || $slack < 0} {set failed 1}
    }
    foreach check {setup hold recovery removal} {
        set label "${model}_${temperature}_${check}"
        report_timing -$check -npaths 20 -detail full_path \
            -file [file join $destination "$label.rpt"]
        set paths [get_timing_paths -$check -npaths 1]
        if {[get_collection_size $paths] == 0} {
            puts $summary "$model\t$temperature\t$check\tno_paths"
            # A fully synchronous reset design can have no asynchronous checks.
            if {$check eq "setup" || $check eq "hold"} { set failed 1 }
        }
        foreach_in_collection path $paths {
            set slack [get_path_info $path -slack]
            puts $summary "$model\t$temperature\t$check\t$slack"
            puts "RESULT $label $slack"
            if {$slack < 0} { set failed 1 }
        }
        foreach_in_collection clk [get_clocks] {
            set name [get_clock_info -name $clk]
            set paths [get_timing_paths -$check -to_clock $clk -npaths 1]
            set slack no_paths
            foreach_in_collection path $paths {
                set slack [get_path_info $path -slack]
            }
            puts $clock_summary "$model\t$temperature\t$check\t$name\t$slack"
        }
    }
    # Fmax only describes same-clock paths. A negative input/cross-clock
    # setup path ending in main must not be excused by its >90 MHz estimate.
    set crossing_index 0
    foreach_in_collection target [get_clocks] {
        set target_name [get_clock_info -name $target]
        foreach_in_collection source [get_clocks] {
            set source_name [get_clock_info -name $source]
            if {$source_name eq $target_name} { continue }
            set paths [get_timing_paths -setup -from_clock $source -to_clock $target -npaths 1]
            set slack no_paths
            foreach_in_collection path $paths { set slack [get_path_info $path -slack] }
            set used_paths [get_timing_paths -setup -from_clock $source -to_clock $target -to $used_sinks -npaths 1]
            set used_slack no_paths
            foreach_in_collection path $used_paths { set used_slack [get_path_info $path -slack] }
            puts $crossing_summary "$model\t$temperature\tclock\t$source_name\t$target_name\t$slack\t$used_slack"
            if {$slack ne "no_paths"} {
                incr crossing_index
                report_timing -setup -from_clock $source -to_clock $target -npaths 1 -detail full_path \
                    -file [file join $destination "${model}_${temperature}_cross_${crossing_index}_setup.rpt"]
                if {$slack < 0} { set failed 1 }
            }
            if {$used_slack ne "no_paths"} {
                report_timing -setup -from_clock $source -to_clock $target -to $used_sinks -npaths 1 -detail full_path \
                    -file [file join $destination "${model}_${temperature}_cross_${crossing_index}_used_setup.rpt"]
                if {$used_slack < 0} {set failed 1}
            }
        }
        # get_ports includes all ports; only actual input startpoints yield
        # paths with -from. This also catches inputs without a launch clock.
        set paths [get_timing_paths -setup -from [get_ports *] -to_clock $target -npaths 1]
        set slack no_paths
        foreach_in_collection path $paths { set slack [get_path_info $path -slack] }
        set used_paths [get_timing_paths -setup -from [get_ports *] -to_clock $target -to $used_sinks -npaths 1]
        set used_slack no_paths
        foreach_in_collection path $used_paths { set used_slack [get_path_info $path -slack] }
        puts $crossing_summary "$model\t$temperature\tinput\tall_input_ports\t$target_name\t$slack\t$used_slack"
        if {$slack ne "no_paths"} {
            incr crossing_index
            report_timing -setup -from [get_ports *] -to_clock $target -npaths 1 -detail full_path \
                -file [file join $destination "${model}_${temperature}_input_${crossing_index}_setup.rpt"]
            if {$slack < 0} { set failed 1 }
        }
        if {$used_slack ne "no_paths"} {
            report_timing -setup -from [get_ports *] -to_clock $target -to $used_sinks -npaths 1 -detail full_path \
                -file [file join $destination "${model}_${temperature}_input_${crossing_index}_used_setup.rpt"]
            if {$used_slack < 0} {set failed 1}
        }
    }
    set pulse_report [file join $destination "${model}_${temperature}_pulse_width.rpt"]
    report_min_pulse_width -nworst 20 -detail summary -file $pulse_report
    set stream [open $pulse_report r]
    set pulse_slack no_paths
    while {[gets $stream line] >= 0} {
        if {![string match ";*" $line]} { continue }
        set value [string trim [lindex [split $line ";"] 1]]
        if {![string is double -strict $value]} { continue }
        if {$pulse_slack eq "no_paths" || $value < $pulse_slack} {
            set pulse_slack $value
        }
    }
    close $stream
    puts $summary "$model\t$temperature\tpulse_width\t$pulse_slack"
    puts "RESULT ${model}_${temperature}_pulse_width $pulse_slack"
    if {$pulse_slack eq "no_paths" || $pulse_slack < 0} { set failed 1 }
}
if {$corner_count == 0} { set failed 1 }
close $conditions
close $summary
close $clock_summary
close $crossing_summary
close $ddio_checks
delete_timing_netlist
project_close
if {$failed} { error "Whole-design timing failed; see $destination" }
