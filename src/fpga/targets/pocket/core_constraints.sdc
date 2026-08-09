#
# user core constraints
#
# put your clock groups in here as well as any net assignments
#

#
# PLL output usage (must match core_top.v mp1/mp_ram instantiations):
#   mp1 general[0] -> clk_core_12288       (audio 12.288 MHz)
#   mp1 general[1] -> unused (outclk_1 disconnected; freed a global clock net)
#   mp1 general[2] -> clk_core_49152
#   mp1 general[3] -> clk_vid              (video 24.576 MHz)
#   mp1 general[4] -> clk_vid_90deg        (video DDR 24.576 MHz @90)
#   mp_ram general[0] -> clk_ram_controller (100 MHz CPU/RAM)
#   mp_ram general[1] -> clk_ram_chip       (100 MHz 243°)
#   mp_ram general[2] -> clk_cram           (unconnected since v2 memory arch)
#
# Unused PLL counters are omitted from this group list.  Referencing unused
# counters produces unmatched-clock warnings and can hide real timing issues.
#
# clk_core_49152 (general[2]) MUST be grouped WITH clk_vid/clk_vid_90deg
# (general[3]/[4]), never in its own async group: all three divide the same
# mp1 VCO with 0 ps phase, so every clk_vid edge coincides with a
# clk_core_49152 edge — the domains are mesochronous, not asynchronous.  The
# scanout consumes raw clk_vid raster counters (x_count/y_count/line_start)
# in the clk_core_49152 domain; with the pair split into separate groups
# those paths are cut from STA and the coincident-edge capture becomes a
# per-fit routing-skew lottery (the 2026-08 FB/terminal wiggle + os30 shear:
# torn x_count capture displaced scanlines ±1 px, placement-sensitive and
# invisible to STA).  Grouped, STA times the crossing (20.3 ns setup to the
# mid edge; hold at the coincident edge gets fitter min-delay padding) and
# any future razor FAILS timing instead of shipping.
set_clock_groups -asynchronous \
 -group { bridge_spiclk } \
 -group { clk_74a } \
 -group { clk_74b } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp1|mf_pllbase_inst|altera_pll_i|general[2].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp1|mf_pllbase_inst|altera_pll_i|general[3].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp1|mf_pllbase_inst|altera_pll_i|general[4].gpll~PLL_OUTPUT_COUNTER|divclk } \
 -group { ic|mp_ram|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk \
          ic|mp_ram|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk }

# NO false_path here any more.  The stripe-fix phase detector used to sample
# clk_vid as data at a COINCIDENT edge (unfixable by construction: -1.5 ns
# hold, and it was the 2026-08 +/-1 px image jitter), so it had to be
# exempted.  It now samples clk_vid_90deg instead (core_top.v ~3005), whose
# transitions sit 10.17 ns from every clk_core_49152 edge — a real,
# deterministic, TIMEABLE path.  Leave it in the analysis: it is the
# tripwire that catches anyone re-pointing the detector at a coincident
# clock.

# ============================================================================
# APF bridge SPI I/O timing (F4, bridge-corruption mitigation 2026-06).
# These pins previously had NO set_input_delay/set_output_delay at all:
# bridge_spiclk got a create_clock (apf_constraints.sdc) and its own async
# clock group above, so STA never reported a single pad-to-register path for
# the RX capture flops in io_bridge_peripheral.v (clocked directly on
# bridge_spiclk).  That STA silence was vacuous — the paths were
# unconstrained, not proven.  The Pocket host publishes no formal timing
# budget for this link, so constrain a conservative +/-2 ns data-valid
# window around the bridge_spiclk edge (~15% of the 13.468 ns bit cell) so
# the fitter must place the capture IOB paths tightly and STA actually
# analyzes them.  bridge_spimosi/bridge_spimiso are inout (4-bit-bus style
# APF phy): the input delays cover the host->FPGA RX direction; the
# matching output delays cover the FPGA->host TX direction.  Note the TX
# launch registers live in clk_74a (io_bridge_peripheral drives spiclk
# itself during TX), so the async clock-group above cuts the internal TX
# domain crossing — the output-delay lines still document the pin-side
# budget and become live if the TX path is ever made source-synchronous.
# F4-AB-disabled: set_input_delay  -clock bridge_spiclk -max  2.0 [get_ports {bridge_spimosi bridge_spimiso bridge_spiss}]
# F4-AB-disabled: set_input_delay  -clock bridge_spiclk -min -2.0 [get_ports {bridge_spimosi bridge_spimiso bridge_spiss}]
# F4-AB-disabled: set_output_delay -clock bridge_spiclk -max  2.0 [get_ports {bridge_spimosi bridge_spimiso}]
# F4-AB-disabled: set_output_delay -clock bridge_spiclk -min -2.0 [get_ports {bridge_spimosi bridge_spimiso}]

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

# DQ input (2026-07 stripe fix — this path was previously set_false_path'd
# under a comment wrongly claiming DQ is sampled on the chip-phase clock;
# io_sdram latches phy_dq on the CONTROLLER clock, so per-bit capture
# timing was unconstrained fabric routing: the stationary diagonal display
# stripes).  Truthful constraint: the chip launches read data off
# dram_clk_pin with tAC(CL3) max 5.4 ns / tOH min 2.5 ns (AS4C32M16SA),
# plus ~0.6/-0.2 ns board flight; capture is the FALLING-edge IO register
# phy_dq_neg (FAST_INPUT_REGISTER, io_sdram.v) whose window STA now
# verifies instead of ignoring.
set_input_delay -clock dram_clk_pin -max 6.0 [get_ports {dram_dq[*]}]
set_input_delay -clock dram_clk_pin -min 2.3 [get_ports {dram_dq[*]}]
# Edge pairing: the launch clock carries ~8.8 ns of network+output-buffer
# insertion to the pin (STA includes it in arrival), so data returns after
# the first falling controller edge — the physical capture is the NEXT
# falling edge (verified: arrival ~24.7 ns vs MC2 required ~29 ns = ~+4 ns
# real margin; the default pairing reported an impossible -5.8 and let the
# fitter chase unfixable paths).  Whole-cycle consumption downstream is
# independently proven by simulation (enable_dq_read_4 arithmetic).
set_multicycle_path -setup -end -from [get_ports {dram_dq[*]}] 2
set_multicycle_path -hold  -end -from [get_ports {dram_dq[*]}] 1
# The falling-edge sample's neg->pos retime is now the HARDENED DDIO input
# path (altddio_in in io_sdram.v) — dedicated IO-cell silicon, no fabric
# route, nothing for the fitter to trade away.  History: a discrete
# negedge-FF -> posedge-FF fabric pair here was a real half-cycle path
# whose per-seed slack (+1.6 .. -1.8) was INVISIBLE in the ram_out0
# WNS ranking and shipped as SDRAM read corruption (TEXTGUARD +8-byte
# slip / I$-delivered wrong immediates, 2026-07-28); a set_max_delay 4.0
# proved physically unreachable (IO->LAB route + 0.9 ns clock skew).

# CRAM0 async-mode pin timing (F5, bridge-corruption mitigation 2026-06).
# cram0_clk is now tied LOW in core_top.v: the chip runs exclusively in
# async page mode (BCR 0x9D1F, burst_rd tied off) and the CellularRAM spec
# requires CLK held low for async operation — including during the
# CRE-controlled BCR write.  The old cram0_clk_pin generated clock
# (sourced from clk_74a when the pin free-ran) no longer exists, so the
# set_output_delay/set_input_delay block that referenced it is retired
# with it; left in place it would only raise unmatched-clock warnings and
# hide real issues.
#
# The async protocol's setup/hold is met STRUCTURALLY, not by single-cycle
# pin timing: cram0_phy.sv stretches every ADV#/WE#/OE#/data edge across
# whole 13.47 ns clk_74a cycles (cycle-count localparams derived from the
# datasheet minimums plus a +2 ns guardband), and read data is sampled
# tens of ns after OE# asserts.  Declare the pin paths false so the fitter
# does not burn effort on a protocol that is cycle-quantized by design.
# Re-evaluate if sync-burst reads return: restore the generated clock and
# real IO delays together with the clk_74a drive in core_top.v.
set_false_path -to [get_ports {cram0_a[*] cram0_dq[*] cram0_adv_n cram0_cre cram0_ce0_n cram0_ce1_n cram0_oe_n cram0_we_n cram0_ub_n cram0_lb_n cram0_clk}]
set_false_path -from [get_ports {cram0_dq[*] cram0_wait}]

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

# Quasi-static video-mode configuration -> scanout fetch address cone.
# term_fb_active / analog_keep_app / fb_stride_reg / analog_fb_stride_reg
# change only on mode switches (terminal toggle, analogizer mode, video
# mode set) — never during steady-state scanout — and a late toggle can
# at worst mis-fetch one line on the switching frame, which mid-frame
# reconfiguration produces anyway.  Cutting them keeps the fitter's
# effort on real paths.  fb_app_addr is deliberately NOT cut: it flips
# every frame and must meet timing.
set_false_path -from [get_registers {*axi_periph_slave:periph|term_fb_active \
                                     *axi_periph_slave:periph|analog_keep_app \
                                     *axi_periph_slave:periph|fb_stride_reg[*] \
                                     *axi_periph_slave:periph|analog_fb_stride_reg[*]}] \
               -to [get_registers {*video_CRT_scanout_indexed_BRAM:scanout|*}]
