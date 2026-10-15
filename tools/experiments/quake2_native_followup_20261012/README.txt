Quake2 busy-area optimization follow-up — 2026-10-12

Selected candidate is deployed for a Pocket comparison. Relative to
the user-tested native-v3 app/core-v4 pair, estimated CPU work falls14–15% in the
main fixtures. Producer-paced GPU/PSRAM simulation yields6.9–25.9% throughput
gains across8samples, including22.5% and25.9% in two busy views and18.1% in another.
The20% goal is not universal and no physical Pocket FPS gain is claimed.

Selected software avoids repeated exact trig, redundant BSP transforms,
particle staging arrays and lightmap texels this vertex-lit backend never reads.
It preserves the original lighting samples, clipping, particles and command order.
The core streams the existing native MAC datapath and uses its four-word color
read window. No extra DSP/M10K or new GPU ABI is needed. 320x240RGB565 and the
existing temporary silent-audio policy are preserved.

Physicalfit:17,067/18,480ALMs;296/308M10Ks;35/66DSPs. Single seed2 only.
Restricted main Fmax95.44MHz(slow85)/93.67MHz(slow0), improved from92.27/91.47.
Configured100MHz still fails setup(-0.802ns); holdmin+0.089ns and all remaining
corner checks are positive. This remains the authorized overclock experiment.

App source and exact native-v3 control:app/README.txt.
Core source, selected bitstream and fixed-seed build:core/README.txt.
Whole-frame model results/limits:evidence/validation-report.txt.
Selected hashes, timing, deployment location:selection.json.
Verify all packaged bytes and both source packages:python3 verify.py.

The prepared installer package is:
  docs/measurements/quake2-native-followup-20261012/deployment-native-v4
It contains both ELFs, os30.rbf_r, metadata and the verified backup/restore installer.
The card was mounted after preparation; the normal app/core pair was installed
and read back successfully. The old three files were backed up. No Pocket
gameplay/FPS result is claimed. Deployment receipts are in evidence/.

Validation:1920exact appframes across native/legacy/CPU-only/demo2;8exact fullRTL
color/depth samples;22ASan/UBSan renderer groups; geometry/texture allocator tests;
clean source-stage rebuild identical to the selected ELF; malformed/stalled/reset
worker checks;120frame profile smoke;9private installer/rollback fixtures.

Residual costs: CPU fallback triangles/visibility/effects, ring/GPU waits in some
views and first-use native model uploads. A440,100byte upload caused the isolated
cold-frame hitch; prewarming is separate from sustained open-world throughput.
CPU estimates are scalar, not actual Vexii timing; paced runs omit scanout/CPU
SDRAM contention, OS interrupts, vblank and previous-frame carryover. Hardware
comparison in the user's scene remains necessary before claiming the target.
