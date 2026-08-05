# os20 — THE 2D/dual-issue pocket variant (90 MHz since 2026-08-02; the
# retired 100 MHz and 96 MHz configurations' numbers live in the project
# memory and git history).  90 MHz CPU/RAM clock: INCLUDE_CLK90 =
# mf_pllram_90 PLL + CLK_FREQ_HZ sysreg (0xD4) so the shared os.bin
# re-derives timers at boot.  CPU adds the 4/32 store buffer
# (configs/os20.cfg).  The dual-issue wall needs ~11.2 ns, so 96 MHz
# could never truly close; 90 MHz targets real closure margin.
DEFS := INCLUDE_ANALOGIZER INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
        INCLUDE_PALETTE INCLUDE_4PLAYER INCLUDE_CLK90
