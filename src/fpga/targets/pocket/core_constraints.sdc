#
# user core constraints
#
# put your clock groups in here as well as any net assignments
#

set_clock_groups -asynchronous \
 -group { bridge_spiclk } \
 -group { clk_74a } \
 -group { clk_74b } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[3].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp1|mf_pllbase_inst|altera_pll_i|general[4].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk }

# PSRAM sync burst timing constraints for CRAM0
# CRAM0 clock comes from PLL outclk_2 (clk_cram)
create_generated_clock -name cram0_clk_pin \
  -source [get_pins {ic|mp_ram|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk}] \
  [get_ports cram0_clk]

set_output_delay -clock cram0_clk_pin -max 3.0 [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_output_delay -clock cram0_clk_pin -min -1.0 [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_input_delay -clock cram0_clk_pin -max 6.5 [get_ports {cram0_dq[*] cram0_wait}]
set_input_delay -clock cram0_clk_pin -min 1.0 [get_ports {cram0_dq[*] cram0_wait}]

# CRAM1: runs on clk_74a (bridge clock), async access only — no sync burst.
# The psram controller handles its own timing with state machine delays.
# Relaxed output delay: async PSRAM doesn't need tight setup to clock edge.
set_output_delay -clock clk_74a -max 2.0 [get_ports {cram1_a[*] cram1_dq[*] cram1_adv_n cram1_cre cram1_ce0_n cram1_ce1_n cram1_oe_n cram1_we_n cram1_ub_n cram1_lb_n}]
set_output_delay -clock clk_74a -min -1.0 [get_ports {cram1_a[*] cram1_dq[*] cram1_adv_n cram1_cre cram1_ce0_n cram1_ce1_n cram1_oe_n cram1_we_n cram1_ub_n cram1_lb_n}]
set_input_delay -clock clk_74a -max 8.0 [get_ports {cram1_dq[*] cram1_wait}]
set_input_delay -clock clk_74a -min 1.0 [get_ports {cram1_dq[*] cram1_wait}]

# CRAM1 clock pin is now driven by clk_74a assign (not PLL).
# Declare it as false path — the async psram controller handles its own timing.
set_false_path -to [get_ports cram1_clk]

# CRAM0 clock pin vs controller clock: psram.sv has fabric pipeline registers
# that handle the CDC. Declare as asynchronous.
set_clock_groups -asynchronous \
  -group { cram0_clk_pin } \
  -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk }
