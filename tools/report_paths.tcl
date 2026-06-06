# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#
# Worst-setup-paths extractor for `make report FULL=true`.
# Usage: quartus_sta -t report_paths.tcl <project> <npaths> <outfile>

set proj    [lindex $quartus(args) 0]
set npaths  [lindex $quartus(args) 1]
set outfile [lindex $quartus(args) 2]

project_open $proj
create_timing_netlist -model slow
read_sdc
update_timing_netlist
report_timing -setup -npaths $npaths -detail summary -file $outfile
delete_timing_netlist
project_close
