Quake2 native5 — incremental busy-scene optimization

Selected app c08bed78555f...; unchanged native4 FPGA image542029e588e2....
The app batches nine random values per allocated effect particle, removes the
original generator's invariant-zero component, and preserves the original
RV32 floating-point order and entire random sequence. It also improves exact
trig/world-position caches, compact PVS access and active texture-chain scans.
RNG batching does not consume values when the particle pool is exhausted.
No resolution, particle-count, animation or lighting reduction is used.

Validation:1920 exact application frames,8 exact color/depth GPU RTL replays,
26 sanitizer groups, geometry/texture checks, and byte-identical clean rebuild.
CPU estimates improve1.66–4.52% across fixtures. Whole-frame modeled throughput
improves3.40%,5.16%,4.80% in three stronger busy samples;0.20–0.85% elsewhere.
All sampled frames improve, but no physical Pocket FPS gain is established.
See evidence/validation-report.txt for pins, every result and limitations.

The unchanged FPGA remains the authorized100MHz experiment; restricted slow
corner timing is93.67/95.44MHz with setup−0.802ns. No new fit or seed sweep.
Audio remains disabled.320x240 RGB565.

Verify all contents and both source packages: python3 verify.py
App source/control/rebuild instructions: app/README.txt
Matching self-contained core source and bitstream: core/README.txt
Deployment package (both ELFs, core, metadata, backup/restore installer):
  docs/measurements/quake2-native-batching-20261012/deployment-native-v5
selection.json records exact hashes and deployment status.

Rejected candidates remain in the measurement directory: broader guard-band
GPU offload changed pixels and merely moved CPU savings into GPU queue waits;
several smaller copy/layout changes regressed. They are not in this release.
