# Additional hold margin for os20's timing-optimized placement. Derive the
# device uncertainty first so -add preserves it. Setup remains at 100 MHz.
derive_clock_uncertainty

# The Analogizer/video clocks share a PLL and have coincident edges.
set_clock_uncertainty -add -hold -enable_same_physical_edge \
    -from [get_clocks {ic|mp1|mf_pllbase_inst|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk}] \
    -to [get_clocks {ic|mp1|mf_pllbase_inst|altera_pll_i|general[3].gpll~PLL_OUTPUT_COUNTER|divclk}] 0.150

# Leave another 50 ps for same-edge CPU/GPU/SDRAM-controller captures.
set_clock_uncertainty -add -hold -enable_same_physical_edge \
    -from [get_clocks {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}] \
    -to [get_clocks {ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}] 0.050
