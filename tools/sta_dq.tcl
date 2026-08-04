# Per-class STA probe: worst setup slack from the dram_dq input pins into
# the DDIO capture registers.  The 2026-08-01 field forensics proved the
# sweep's single-WNS ranking is path-class-blind: a seed whose worst paths
# are ALL DQ capture (os25-s23, -0.91 -- every SDRAM read garbage, blank
# boot) ranks identically to one whose wall is CPU-internal and survivable
# (os25-s21, -0.49).  This emits a separate DQ-class column the sweep can
# veto on (SWEEP_DQ_VETO, default -0.40 -- the empirically-booting bound).
project_open ap_core
create_timing_netlist -model slow
read_sdc
update_timing_netlist
set r [report_timing -setup -from [get_ports {dram_dq[*]}] -npaths 1]
post_message -type info "DQCLASS_WNS=[lindex $r 1]"
delete_timing_netlist
project_close
