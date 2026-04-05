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
 -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp_ram|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp_ram|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk }

# SDRAM I/O timing
# The io_sdram controller uses clk_ram_chip (243° phase shift) internally
# to manage setup/hold timing. The phase relationship is fixed by the PLL,
# not by the fitter. Constrain output delays loosely; mark DQ input as
# false path since the controller samples at the correct phase internally.
create_generated_clock -name dram_clk_pin \
  -source [get_pins {ic|mp_ram|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}] \
  [get_ports dram_clk]

set_output_delay -clock dram_clk_pin -max  3.0 [get_ports {dram_a[*] dram_ba[*] dram_dq[*] dram_dqm[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]
set_output_delay -clock dram_clk_pin -min -1.0 [get_ports {dram_a[*] dram_ba[*] dram_dq[*] dram_dqm[*] dram_ras_n dram_cas_n dram_we_n dram_cke}]

# DQ input: sampled by clk_ram_chip (phase-shifted), not clk_ram_controller.
# The cross-clock path from dram_clk_pin to clk_ram_controller is handled
# by the PLL phase relationship, not fitter placement.
set_false_path -from [get_ports {dram_dq[*]}]

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
