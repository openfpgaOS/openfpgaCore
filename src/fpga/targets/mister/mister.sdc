#------------------------------------------------------------------------------
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileType: SOURCE
# SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
#------------------------------------------------------------------------------
#
# openfpgaOS MiSTer core constraints.
# sys/sys_top.sdc declares the root clocks, runs derive_pll_clocks, and
# decouples the core PLL group (emu|pll|pll_inst|altera_pll_i|...) from
# the framework HDMI/SPI/50 MHz domains.  This file adds the intra-core
# relationships and the SDRAM I/O timing.
#
# emu|pll (pll_sys) outputs:
#   general[0] -> clk_cpu / clk_ram_controller (100 MHz)
#   general[1] -> clk_ram_chip                 (100 MHz @ 6750 ps)
# emu|pllv (pll_vid) output:
#   general[0] -> clk_vid                      (24.576 MHz)
#
# clk_cpu/clk_ram_chip are phase-related (one group, analyzed together).
# clk_vid lives in its own PLL and instance hierarchy, so it does NOT
# match sys_top.sdc's core-pll wildcard — the single-group form below
# declares it asynchronous to every other clock in the design (the
# scanout line buffer is the CDC into the pixel domain).
set_clock_groups -asynchronous \
 -group { emu|pllv|pll_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk }

# SDRAM I/O timing.  SDRAM CK is a DDIO-forwarded INVERTED copy of the
# 100 MHz controller clock (the standard MiSTer core scheme — see emu.sv
# sdramclk_ddr): the chip samples half a period after the IOB launch edge,
# and clock-vs-data pin delays are matched by the shared IOB structure.
# The old scheme forwarded the raw PLL general[1] output (6750 ps — the
# Pocket board's tuned phase) as a data signal; that left DQM transitions
# marginal at the chip (HW-proven sub-word write corruption, 2026-07-02).
create_generated_clock -name sdram_clk_pin \
  -source [get_pins {emu|pll|pll_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}] \
  -invert \
  [get_ports {SDRAM_CLK}]

set_output_delay -clock sdram_clk_pin -max  3.0 [get_ports {SDRAM_A[*] SDRAM_BA[*] SDRAM_DQ[*] SDRAM_DQML SDRAM_DQMH SDRAM_nRAS SDRAM_nCAS SDRAM_nWE SDRAM_CKE SDRAM_nCS}]
set_output_delay -clock sdram_clk_pin -min -1.0 [get_ports {SDRAM_A[*] SDRAM_BA[*] SDRAM_DQ[*] SDRAM_DQML SDRAM_DQMH SDRAM_nRAS SDRAM_nCAS SDRAM_nWE SDRAM_CKE SDRAM_nCS}]

set_false_path -from [get_ports {SDRAM_DQ[*]}]
