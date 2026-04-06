project_open ap_core
create_timing_netlist
read_sdc
update_timing_netlist

puts "=== TOP 10 WORST SETUP PATHS (clk_74a) ==="
report_timing -setup -npaths 10 -to_clock clk_74a -detail path_only

puts "\n\n=== TOP 10 WORST SETUP PATHS (CPU 100MHz) ==="
report_timing -setup -npaths 10 -to_clock {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk} -detail path_only

project_close
