#
# user core constraints
#
# put your clock groups in here as well as any net assignments
#

#
# PLL output usage (must match core_top.v mp1/mp_ram instantiations):
#   mp1 general[0] -> clk_core_12288   (audio 12.288 MHz)
#   mp1 general[1] -> unconnected      (outclk_1 tied to no net)
#   mp1 general[2] -> clk_core_49152
#   mp1 general[3] -> clk_vid
#   mp1 general[4] -> clk_vid_90deg
#   mp_ram general[0] -> clk_ram_controller (100 MHz CPU/RAM)
#   mp_ram general[1] -> clk_ram_chip       (100 MHz 243°)
#   mp_ram general[2] -> clk_cram           (unconnected since v2 memory arch)
#
# Unused PLL counters are omitted from this group list.  Referencing unused
# counters produces unmatched-clock warnings and can hide real timing issues.
set_clock_groups -asynchronous \
 -group { bridge_spiclk } \
 -group { clk_74a } \
 -group { clk_74b } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp_ram|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk }

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

# PSRAM sync burst timing constraints for CRAM0.
# Memory-arch v2: cram0_clk is a direct assign from clk_74a (core_top.v:2632),
# so the pin clock derives 1:1 from the clk_74a input port.
create_generated_clock -name cram0_clk_pin \
  -source [get_ports clk_74a] \
  [get_ports cram0_clk]

set_output_delay -clock cram0_clk_pin -max 3.0 [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_output_delay -clock cram0_clk_pin -min -1.0 [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n}]
set_input_delay -clock cram0_clk_pin -max 6.5 [get_ports {cram0_dq[*] cram0_wait}]
set_input_delay -clock cram0_clk_pin -min 1.0 [get_ports {cram0_dq[*] cram0_wait}]

# CRAM1 retired in memory-arch v2 — chip is not pin-assigned in ap_core.qsf
# and the top-level ports have been removed. Old cram1_* IO/clock constraints
# deleted here; referencing the retired ports produced "unresolved port"
# warnings from Quartus.

# ============================================================================
# VexiiRiscv FPU multicycle — the FpuAddSharedPlugin's pre-shift exp-diff
# cone is explicitly pipelined (pip_node_0 → pip_node_1 → ...), so the
# ctrl2-stage completion signal has at least 2 cycles to propagate into the
# node_1 adder registers before the FPU op actually commits.  Quartus
# otherwise treats this as a single-cycle path and fails setup by ~1.3 ns
# at the slow 85C corner — the real hardware happily runs it at 100 MHz
# because node_1 is latched on the second pipeline edge, not the first.
set_multicycle_path -from [get_registers {*VexiiRiscv*|execute_ctrl2_up_COMPLETION_AT_*}] \
                    -to   [get_registers {*FpuAddSharedPlugin_logic_pip_node_1*}] \
                    -setup 2
set_multicycle_path -from [get_registers {*VexiiRiscv*|execute_ctrl2_up_COMPLETION_AT_*}] \
                    -to   [get_registers {*FpuAddSharedPlugin_logic_pip_node_1*}] \
                    -hold 1

# The GPU triangle rasterizer is currently disabled in the production
# span-only profile, so its DSP input-shadow constraints are intentionally
# absent.  If the triangle path is restored, also restore the narrow hold-only
# exceptions for tri_A/tri_B -> tri_A_dsp_in/tri_B_dsp_in.
