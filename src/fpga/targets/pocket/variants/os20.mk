# os20 — 2D / dual-issue Pocket variant, 100 MHz CPU/GPU/SDRAM.
# The default PLL and CLK_FREQ_HZ sysreg select 100 MHz; firmware derives
# timers from that register. CPU geometry lives in configs/os20.cfg.
# GPU cuts plus radix4/physical optimizations, /4 mixer mix-down, SDRAM
# open-row tracking and scanout sub-bursts: seed 25 estimates 94.66 MHz,
# setup -0.564 ns at 100 MHz, worst hold +0.074 ns across four corners.
# 16,005 / 18,480 ALMs; the 94 MHz trial goal is met, 100 MHz is not closed.
# Retained GPU cycles are unchanged; the CPU divider uses fewer iterations.
# The mixed CPU/IRQ test takes 5.516% fewer cycles (not a game FPS result).
# Both ports render on the CPU. Diablo's optional GPU=1 probe also uses
# affine colormapped spans and uploads its blend table, so retain those.
# Neither client submits GPU perspective or depth-tested spans.
DEFS := INCLUDE_ANALOGIZER INCLUDE_HW_MIXER INCLUDE_TRANSLUC \
        INCLUDE_PALETTE INCLUDE_4PLAYER INCLUDE_BANK_ROW_TRACK \
        EXCLUDE_GPU_PERSPECTIVE EXCLUDE_GPU_DEPTH INCLUDE_GPU_CLAMP_PIPE
