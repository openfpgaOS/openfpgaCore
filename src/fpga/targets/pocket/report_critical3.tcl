project_open ap_core
create_timing_netlist
read_sdc
update_timing_netlist

puts "=== WORST clk_74a PATH - FULL DETAIL ==="
report_timing -setup -npaths 1 -to_clock clk_74a -detail full_path -panel_name "Critical 74a"

puts "\n=== WORST CPU PATH - FULL DETAIL ==="
report_timing -setup -npaths 1 -to_clock {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk} -detail full_path -panel_name "Critical CPU"

puts "\n=== WORST clk_74a - FROM/TO NAMES ==="
set paths [get_timing_paths -setup -npaths 5 -to_clock clk_74a]
foreach_in_collection path $paths {
    set slack [get_path_info $path -slack]
    set from [get_node_info -name [get_path_info $path -from]]
    set to [get_node_info -name [get_path_info $path -to]]
    puts [format "Slack: %8.3f  From: %s" $slack $from]
    puts [format "               To:   %s" $to]
    puts ""
}

puts "\n=== WORST CPU - FROM/TO NAMES ==="
set paths [get_timing_paths -setup -npaths 5 -to_clock {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}]
foreach_in_collection path $paths {
    set slack [get_path_info $path -slack]
    set from [get_node_info -name [get_path_info $path -from]]
    set to [get_node_info -name [get_path_info $path -to]]
    puts [format "Slack: %8.3f  From: %s" $slack $from]
    puts [format "               To:   %s" $to]
    puts ""
}

project_close
