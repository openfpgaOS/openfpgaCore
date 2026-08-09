# Per-class STA probe: worst setup slack from the dram_dq input pins into the
# DDIO capture register THE CONTROLLER ACTUALLY READS.  The 2026-08-01 field
# forensics proved the sweep's single-WNS ranking is path-class-blind: a seed
# whose worst paths are all DQ capture ranks identically to one whose wall is
# CPU-internal and survivable.  This emits a separate DQ-class column the sweep
# can veto on (SWEEP_DQ_VETO, default -0.40).
#
# 2026-08-06 -- THE -to FILTER IS LOAD-BEARING, DO NOT DROP IT.  altddio_in has
# two outputs.  dataout_h (rising edge) goes to phy_dq_ddio_h_unused, which
# io_sdram.v declares (:324) and NEVER READS; dataout_l (falling edge) becomes
# phy_dq_latched and is the one that feeds word_q/burst_data (:405-425).  An
# unfiltered `-from [get_ports dram_dq[*]]` returns the WORST path, and that is
# always the dangling dataout_h register -- on os25 seed 14 it reported +0.353
# while the register actually in use (ddio_ina[*]~DFFLO) had +5.227, a 4.9 ns
# understatement.  Every DQ veto and the 2026-08-02 243->216 deg rephase
# ("-0.38 read floor") were decided on that phantom.  Constrain to the used
# capture: ddio_ina*DFFLO* is the Quartus name for the dataout_l register;
# phy_dq_latched covers the non-ALTERA_RESERVED_QIS (FF-pair) branch.
project_open ap_core
create_timing_netlist -model slow
read_sdc
update_timing_netlist
set r [report_timing -setup -from [get_ports {dram_dq[*]}] \
                     -to [get_registers {*ddio_ina*DFFLO* *phy_dq_latched*}] -npaths 1]
post_message -type info "DQCLASS_WNS=[lindex $r 1]"
delete_timing_netlist
project_close
