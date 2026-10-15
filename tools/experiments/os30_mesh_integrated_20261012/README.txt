OS30 integrated mesh source review — native-v7 / F18 MESH3 + F19 MESH4 addition

UNQUALIFIED. NOT DEPLOYED. NO BITSTREAM. NO PHYSICAL BUILD SELECTED.

This is an isolated review bundle, not a production enablement or portable
release. It retains the existing raw LOAD/shared FMA, performs exact whole-span
preflight and packet validation, and stores generated packets in a 512-word
variable-length queue before their ordered GPU markers. F18 stages small markers
with the existing 512-word SDK threshold and publishes once before a raw-owner
wait when queue credit could depend on older staged markers.

Read results.txt for the measured cross-variant outcome, strict-check scope and
deployment gate. No practical 512-word variant has demonstrated a net benefit.

Review starting points:
  results.txt              Final measured outcome and evidence references.
  sm64-v2-to-v3.patch       Small publication/liveness change and F18 label.
  sm64-v1-to-v3.patch       MTF1 to MTF2 ARM plus batching.
  sm64-integration.patch   Complete app delta from the pinned queued-front app.
  native-v7.patch          Complete native model delta, including testbench.
  physical-top.patch       Pocket wiring only, against qualified frozen top.
  rtl/                     Exact changed/new common RTL from native-v7.
  abi.json                 Frozen MTF2 register/marker contract.
  reviews/                 Scope-qualified reviews; older physical-v5 review
                           is connectivity evidence, not a v7 fit certificate.
  additions/app-v4/        Separate F19 adaptive-feed patch, build/test receipts
                           and independent review. No additional RTL change.

The v4 addition forces publication below 128 words of already-published CPU-ring
occupancy, retains the 512-word/sync override, and forces publication on the DMA
backend. It preserves v3's ownership-wait helper. The measured two-interval total
is 4.34% slower than the same-ELF capability-off control. Focused test success
is not a speed or deployment qualification; see results.txt for exact limits.

Final strict checker sources are archived under recipes/checker-dependencies/
with their original measurement-relative paths; the strict receipts identify
the selected versions. Earlier checker recipes remain for provenance.

Earlier reviews/receipts retain their original point-in-time status, including
"pending" fields. They are preserved evidence; results.txt and the final strict
receipts state the completed experiment outcome. verification.json is the
unchanged historical v3 bundle check; verification-v4.json verifies this edition.

All exact bases, preimage/postimage hashes and historical receipt relocations
are in manifest.json. Native base is
  docs/measurements/os30-psram32-20261009/cached-cpu-v1/native-sdram-v2
App integration base is
  docs/measurements/os30-psram32-20261009/queued-front-v1/work/sm64-v2
Physical top base is the frozen source tree under
  docs/measurements/os30-psram32-20261009/work/core/docs/measurements/
  psram32-build-upload-reset-placeeffort-v1
The two smaller app patches instead require mesh-integrated-v1/work-app or
work-app-v2 respectively. They are alternatives, not a sequence to apply after
sm64-integration.patch. Native and physical paths are different layouts.
To reconstruct v4, first reconstruct the exact v3 app, then apply the separate
additions/app-v4/sm64-v3-to-v4.patch. Its manifest pins both source versions.

Verification:
  python3 tools/experiments/os30_mesh_integrated_20261012/verify.py
  python3 tools/experiments/os30_mesh_integrated_20261012/verify.py --patches --evidence
The latter requires the exact local ignored measurement inputs named above;
it applies/reverses patches with zero fuzz/offset only in temporary directories.

The two focused tests need GCC and Verilator but no ROM assets:
  python3 tools/experiments/os30_mesh_integrated_20261012/run_tests.py --output /tmp/mesh-review-test
Use a new output directory. ASan/UBSan are enabled; LeakSanitizer is disabled
because the local execution harness uses ptrace. These tests do not replace
full-frame native replay or physical timing qualification.

Recipes are archived byte-for-byte for review. They retain original __file__
relative paths and are NOT directly runnable after relocation into this bundle.
For full reproduction, reconstruct the exact original isolated directory
layout from the bases/patches, provide independently obtained SM64 assets and
the existing native SDRAM model/toolchain, then use the archived app build,
fixture, replay and checker recipes at their original locations. Review their
recorded commands and source hashes before running. build_native.py snapshots
work-core/frozen: restore it from selected native-v7, not later mutable staging.
build_pocket.py is a prepare/optional-compile recipe, not a qualified output.
No physical project has been compiled for this selected combination.

The bundle omits game/ROM/texture data, ELF/MIF/OS/loader/bitstream binaries,
full native models, large snapshots, traces and generated Verilator output.
Receipts can reference these external inputs by exact hash without embedding
them. No deployment, production source change, physical timing or sustained Pocket FPS
claim is made. Recorded native cycle comparisons have the limits in results.txt.
