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
 -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk }

# PSRAM sync burst timing constraints for CRAM0
create_generated_clock -name cram0_clk_pin \
  -source [get_pins {ic|mp_ram|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk}] \
  [get_ports cram0_clk]

set_output_delay -clock cram0_clk_pin -max 3.0 [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_output_delay -clock cram0_clk_pin -min -1.0 [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_input_delay -clock cram0_clk_pin -max 6.5 [get_ports {cram0_dq[*] cram0_wait}]
set_input_delay -clock cram0_clk_pin -min 1.0 [get_ports {cram0_dq[*] cram0_wait}]

# PSRAM sync burst timing constraints for CRAM1
create_generated_clock -name cram1_clk_pin \
  -source [get_pins {ic|mp_ram|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk}] \
  [get_ports cram1_clk]

set_output_delay -clock cram1_clk_pin -max 3.0 [get_ports {cram1_a[*] cram1_dq[*] cram1_adv_n cram1_cre cram1_ce0_n cram1_ce1_n cram1_oe_n cram1_we_n cram1_ub_n cram1_lb_n}]
set_output_delay -clock cram1_clk_pin -min -1.0 [get_ports {cram1_a[*] cram1_dq[*] cram1_adv_n cram1_cre cram1_ce0_n cram1_ce1_n cram1_oe_n cram1_we_n cram1_ub_n cram1_lb_n}]
set_input_delay -clock cram1_clk_pin -max 6.5 [get_ports {cram1_dq[*] cram1_wait}]
set_input_delay -clock cram1_clk_pin -min 1.0 [get_ports {cram1_dq[*] cram1_wait}]

# CRAM clock pins are gated versions of PLL outclk_2. The IOB capture registers
# (cram_dq_r) are clocked by the controller clock (outclk_0), but psram.sv has
# fabric pipeline registers (cram_dq_r -> cram_dq_r2) that handle the CDC.
# Declare CRAM pin clocks as asynchronous to the controller clock to prevent
# false cross-domain timing analysis through the gated clock path.
set_clock_groups -asynchronous \
  -group { cram0_clk_pin } \
  -group { cram1_clk_pin } \
  -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk }
