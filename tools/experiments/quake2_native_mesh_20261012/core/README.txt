Quake2 native MD2 mesh core, experiment v1

STATUS: Historical v1 prototype, NOT SELECTED. It fits with the mixer enabled
(18196 ALMs, 304 M10Ks, 45 DSPs), but the initial physical result reports a
-3.626 ns setup path from native RAM through validation to the address register.
Timing has therefore failed; a separate pipelined v2 is being developed. This
package keeps the mixer enabled, as in the initial mixer-on-v1 fit. It contains
no bitstream and does not deploy anything or change production core sources.
The separately archived application and integrated game validation are required
before using the new command. Worker tests alone do not establish game parity,
physical timing, real CPU/GPU overlap under load, or an FPS improvement.

The source archive contains every file from the prepared Quartus source tree,
including CPU Verilog, boot MIF, PLL files, pin/clock constraints and existing
parent provenance. The files match the initial physical build's recorded input
hashes exactly, accounting only for build_core.py renaming build-receipt.json
to parent-preparation-receipt.json when making a build snapshot. Historical
timing text already present in that prepared source is parent evidence, not
timing evidence for this prototype. build-receipt.json in the staged source
is also parent preparation evidence; use the NEW fit output receipt for results.

The readable native-mesh.patch contains the four live changes from the selected
worker-q2-performance-seed2 baseline: shared geometry worker, os30 GPU, os30
Pocket top, and QSF macro. The QSF uses common/os30/gpu_core.v. An unused
gpu_core.v.orig present in the prepared tree is archived for exact provenance;
it is not a Quartus input. Preimages and postimages pin the readable delta.

From this directory:

  python3 package.py verify
  python3 package.py stage /tmp/q2-native-core-source-NEW
  python3 package.py check-stage /tmp/q2-native-core-source-NEW
  python3 worker-tests/run_worker_tests.py /tmp/q2-native-worker-tests-NEW
  python3 build_core.py /tmp/q2-native-core-source-NEW /tmp/q2-native-fit-NEW

The build recipe is the unchanged, hashed recipe used by mixer-on-v1. It needs
Quartus Lite 25.1std.0 Build 1129 at its recorded installation path. Both stage
and fit destinations must be new directories. The recipe makes its own copy,
runs map/fit/asm/STA and strict all-corner setup/hold/recovery/removal/pulse-width
checks. Timing failures remain failures even when assembly produced a file.
--without-mixer makes a separate fit snapshot with the mixer macro removed;
it is not enabled in the archived source and requires matching software/audio
validation. No seed sweep is run.

The manifest pins all package files except the manifest itself. Verification
rejects unsafe member names, links, duplicate archive entries, missing/extra
files and content changes, then tests the readable delta in both directions.
Existing directories are never overwritten. Test/build outputs belong outside
this immutable package. Final physical evidence should be stored alongside the
package rather than inserted into its pinned inventory.
