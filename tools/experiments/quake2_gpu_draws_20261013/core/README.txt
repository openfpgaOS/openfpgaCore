Native particle geometry experiment, os30 only

This is a Quake2-only image: the additive texel*C+D color combiner is
compiled out and its hardware capability is cleared. Normal RGB
texture modulation, alpha blending, lighting colors, and depth remain.
It is NOT a replacement for the separate SM64 image.



This package contains the selected exact prebuild sources, bitstream, recorded
worker gates, ABI, and physical reports. It is not a Pocket performance result
or a deployment receipt. Full-application image/throughput evidence belongs to
the accompanying experiment release. Historical source preparation receipts
inside the source archive are provenance only, not evidence for this candidate.

GPU_STATUS bit 13 advertises native camera-space particle billboards. The GPU
reuses the geometry worker multiplier/divider; the particle path preserves draw
order, alpha, clip rectangle, material, and depth state. Unsupported particles
must use the app's existing fallback. See particle-abi.txt for fixed-point limits
and the explicitly bounded difference from the former floating-point frontend.
General near-plane geometry clipping is not included. This os30 experiment keeps
the existing silent-audio configuration without the mixer.

Verification and fresh staging (run from this package directory):
  python3 package.py verify
  python3 package.py stage /tmp/quake2-particle-source
  python3 package.py check-stage /tmp/quake2-particle-source
  python3 worker-tests/run.py /tmp/quake2-particle-worker-tests

Build once with Quartus 25.1std.0 Build 1129, seed 2, configured 100 MHz:
  python3 build_core.py /tmp/quake2-particle-source /tmp/quake2-particle-fit
Use --prepare-only to validate copying without invoking Quartus. A negative
timing check returns failure even when the bitstream assembled successfully.
The portable build recipe is adapted for this self-contained archive; the
actually executed original recipe is retained under evidence/. No commands
here deploy files or sweep seeds. Source staging must remain immutable; build
outputs go to a separate new directory.

Recorded physical result (read evidence/physical/result.json for all corners):
{
  "fit_successful": true,
  "ALMs": {
    "used": 17948,
    "available": 18480
  },
  "M10Ks": {
    "used": 296,
    "available": 308
  },
  "DSPs": {
    "used": 32,
    "available": 66
  },
  "LABs": {
    "used": 1847,
    "available": 1848
  },
  "worker": {
    "ALMs": 2172.4,
    "registers": 1925,
    "M10Ks": 5,
    "DSPs": 3
  },
  "configured_clock_MHz": 100,
  "main_clock_restricted_Fmax_MHz": {
    "slow_85": 90.74,
    "slow_0": 90.13
  },
  "constrained_timing_passed": false,
  "worst_setup_ns": -1.095,
  "worst_hold_ns": 0.043
}
