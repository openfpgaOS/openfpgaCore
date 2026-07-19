# os20 — THE 2D/dual-issue pocket variant (96 MHz since 2026-07-18; the
# retired 100 MHz configuration's numbers live in the project memory and
# git history).  96 MHz CPU/RAM clock: INCLUDE_CLK96 =
# mf_pllram_96 PLL + CLK_FREQ_HZ sysreg (0xD4) so the shared os.bin
# re-derives timers at boot.  CPU adds the 4/32 store buffer
# (configs/os20.cfg).  Wide-swept: seed 19, WNS -0.856 at 96.06 MHz —
# more margin than the retired os20@100 (-1.007) for ~1-3% net perf.
DEFS := INCLUDE_ANALOGIZER INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
        INCLUDE_PALETTE INCLUDE_4PLAYER INCLUDE_CLK96
