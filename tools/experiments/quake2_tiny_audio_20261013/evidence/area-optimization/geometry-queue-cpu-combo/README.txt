Ring-lookahead worker, scoped CPU combinational physical synthesis

Seed 2 is legally fitted: 18,133 / 18,480 ALMs; 1,847 / 1,848 LABs;
305 / 308 M10Ks; 42 / 66 DSPs. The configuration remains 100 MHz.
Minimum restricted main Fmax is 87.31 MHz (slow 0 C); slow 85 C is
88.06 MHz. This seed is NOT eligible for the strict >90 MHz selection.

All four-corner hold/recovery/removal/pulse and nonmain setup checks pass.
Used cross-clock and input setup checks pass. Raw SDRAM rising-capture
setup is -0.804 ns; the exact 48-node DDIO topology proof establishes
that its 16 high endpoints are disconnected. Only those endpoints are
excluded from the additional used-path checks. Raw violations remain
recorded, and 100 MHz closure is false. No clock/SDC change was made.

Global AREA mapping, global COMBO OFF, effort NORMAL, exact CPU instance
COMBO ON at ic|cpu|cpu. The retained CPU SPEED assignment is not proof of
independent SPEED mapping in Lite. Source is identical to frozen sweep
f63613efe348448c45af303972d6804a41d15724ac3a716f17eff4b40e6eb2d3.
The local spaced-JSON inventory has a different fingerprint; individual
128 source hashes are exact (canonical-source-fingerprint.json).

The queue change raises estimated mapping by 126 ALMs versus divisor-only
and fitted use by 59 ALMs. It improves the worst restricted estimate from
83.74 to 87.31 MHz; behavior and cycles remain exact in the worker and
complete GPU replay gates. This is a physical result, not an FPS claim.

No assembler, sweep, source deployment or SD-card write was run here.
The parent launched the separate complete 30-seed sweep afterward.
The portable evidence includes summary tables, Fmax and critical setup
reports plus resource reports; all raw reports remain in this directory.
