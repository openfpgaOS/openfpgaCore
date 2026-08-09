# Per-class SDRAM DQ probe, BOTH directions -- the read-capture window and the
# write-launch window are opposite ends of the SAME chip-clock phase tradeoff
# (mf_pllram_*.v phase_shift1).  sta_dq.tcl reports only the read side, which
# is enough to VETO a seed but not enough to CENTER the phase: pushing phase to
# buy read margin spends write margin, so a phase campaign must watch both.
project_open ap_core
create_timing_netlist -model slow
read_sdc
update_timing_netlist
# READ: pad -> the DDIO capture register the controller actually reads.  The
# -to filter is load-bearing: unfiltered, the worst path is always the DANGLING
# dataout_h register (phy_dq_ddio_h_unused, io_sdram.v:324/335, zero fanout),
# which understates real read margin by ~4.9 ns.  See tools/sta_dq.tcl.
set DQ_USED [get_registers {*ddio_ina*DFFLO* *phy_dq_latched*}]
set r [report_timing -setup -from [get_ports {dram_dq[*]}] -to $DQ_USED -npaths 1]
post_message -type info "DQ_READ_WNS=[lindex $r 1]"
set rh [report_timing -hold -from [get_ports {dram_dq[*]}] -to $DQ_USED -npaths 1]
post_message -type info "DQ_READ_HOLD=[lindex $rh 1]"
# The phantom, reported alongside so a regression in the filter is obvious.
set rp [report_timing -setup -from [get_ports {dram_dq[*]}] -npaths 1]
post_message -type info "DQ_READ_ANYDEST=[lindex $rp 1]"
# WRITE: launch registers -> dram_dq / dram_dqm pads.
set w [report_timing -setup -to [get_ports {dram_dq[*] dram_dqm[*]}] -npaths 1]
post_message -type info "DQ_WRITE_WNS=[lindex $w 1]"
set wh [report_timing -hold -to [get_ports {dram_dq[*] dram_dqm[*]}] -npaths 1]
post_message -type info "DQ_WRITE_HOLD=[lindex $wh 1]"
# COMMAND/ADDRESS launch, for completeness (same launch clock as writes).
set c [report_timing -setup -to [get_ports {dram_a[*] dram_ba[*] dram_ras_n dram_cas_n dram_we_n}] -npaths 1]
post_message -type info "DQ_CMD_WNS=[lindex $c 1]"
delete_timing_netlist
project_close
