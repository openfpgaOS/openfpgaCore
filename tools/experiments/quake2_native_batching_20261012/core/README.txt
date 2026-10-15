Quake2 native mesh follow-up: MAC stream + color window4

This is the isolated os30 experimental core. It uses the native-mesh ABI in
ABI.txt and keeps the selected build's silent audio configuration. Nothing in
this package installs or deploys a core, changes production files, or sweeps seeds.

Changes relative to the selected core-v4:
  - Feed six native MD2 transform terms through the existing pipelined multiplier
    without serial per-term waits: 117 ->36 cycles for three camera rows/vertex.
  - Use the existing four-word color blend destination read window, previously2.
No additional DSP or M10K is used. Exact arithmetic, winding, command order,
projection, clipping convention and color/depth output remain unchanged.

Measured full-RTL GPU replay (immediately available commands; no CPU contention):
  frame20 :3,076,766 ->2,815,736 cycles (-8.484%)
  frame80 :3,812,485 ->3,393,528 cycles (-10.989%)
  frame180:3,446,182 ->3,159,878 cycles (-8.308%)
  frame300:2,492,418 ->2,384,050 cycles (-4.348%)
Complete color+depth files match the selected baseline byte-for-byte. This is
GPU service-time evidence, not a claim of equivalent whole-game FPS improvement.
CPU/GPU overlap and subsequent hardware testing determine the actual gain.

Quartus25.1std.0 Build1129, seed2, configured100MHz:
  17,067/18,480ALMs,296/308M10Ks,35/66DSPs;1,843/1,848LABs.
  Main restricted Fmax:95.44MHz slow85C,93.67MHz slow0C.
  Worst setup:-0.802ns; minhold:+0.089ns. All recovery/removal/pulse checks positive.
  Configured100MHz timing is NOT closed. The all-corner checker exits3 for the
  negative setup slack; this remains a failed timing check, despite fitting and
  assembling successfully. It exceeds the user's >90MHz estimated target and
  improves the previous91.47MHz minimum. Current worst setup is SDRAM input capture.

Reproduce from this directory (Python3; no source outside this package needed):
  python3 package.py verify
  python3 package.py stage /tmp/q2-mac-cb4-source
  python3 package.py check-stage /tmp/q2-mac-cb4-source
  python3 build_core.py /tmp/q2-mac-cb4-source /tmp/q2-mac-cb4-fit
Optionally set --quartus-bin PATH; its version must match the pin. Both directories
must be new. Use this frozen build recipe; generic variant make generation would
replace the selected experiment's QSF. The recipe preserves seed2 and all100MHz
timing constraints and returns failure on negative setup slack. It never deploys.

source.tar.gz contains every one of the127 exact pre-Quartus source inputs.
Quartus rewrites some QSF optimization assignments while running, so the archive
restores the exact manifest-pinned pre-build QSF, not the mutated post-fit file.
The actual executed fit recipe and source-to-artifact receipt are in evidence/.
The portable builder performs the same tool steps; packaging checks validate its
input snapshot and strict failure behavior without rerunning Quartus.

Focused native/mixed/disabled worker regressions, including malformed jobs,
queue wrap/stalls, enable pauses and in-flight resets:
  python3 worker-tests/run_worker_tests.py /tmp/q2-mac-worker-tests
The included evidence records the already executed tests against the exact
fitted worker. Verilator5.052 was used. No new physical fit is needed for tests.

Full raster replay is also portable:
  python3 replay.py /path/to/pinned/captures /tmp/q2-mac-raster-tests
The four frame_00020/00080/00180/00300.vec capture files contain game assets and
are not copied into this source package. Their exact hashes are in manifest.json.
They currently exist in the follow-up measurement validation/baseline-vectors.
The package includes all replay RTL/harness sources, expected output hashes and
receipts for the selected and rejected configurations. The portable replay runner
was rebuilt and rerun after packaging; all four outputs and cycle counts match. The runner checks both
complete output and exact cycle counts. Normal source verification/build and
focused worker tests are fully self-contained.

os30.rbf_r is the assembled selected experimental artifact. Its SHA256 is:
542029e588e2a646f20fc98f1dbaf89a667ced48c75de9e89354df74da8622b6
It has not been deployed by this packaging step. Use the existing known-working
core and app together as a rollback; the native-mesh ABI is unchanged.
