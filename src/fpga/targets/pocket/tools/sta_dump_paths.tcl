# One-shot diagnostic: dump the top failing setup paths on the 100 MHz domain
# with full From/To detail, plus an endpoint histogram, to text files.
# Usage: quartus_sta -t sta_dump_paths.tcl
project_open ap_core
create_timing_netlist -model slow
read_sdc
update_timing_netlist

report_timing -setup -npaths 200 -less_than_slack 0 \
    -to_clock [get_clocks {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}] \
    -file os30_failing_paths.txt

report_timing -setup -npaths 30 -less_than_slack 0 -detail full_path \
    -to_clock [get_clocks {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}] \
    -file os30_worst30_detail.txt

project_close
