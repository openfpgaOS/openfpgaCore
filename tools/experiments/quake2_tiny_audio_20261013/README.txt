Quake2 OS30 tiny audio experiment

Status: source-only, UNQUALIFIED. All 30 seeds were attempted; none passed
the unchanged release gate. Core source, application and test/sweep evidence
are included. There is no selected core, FPGA bitstream or deployment.

The completed OS30 sweep attempted every seed from 1 through 30. None met
the unchanged strict >90 MHz release gate. Seed 20 had the best worst-corner
restricted main Fmax estimate, 87.97 MHz, with the other used-interface and
nonsetup checks clean. The configured clock remains 100 MHz; this result is
not closure at that clock. Seed 20 is diagnostic evidence, not a selected
release. Pocket audio playback has not yet been tested on the device.

This experiment restores the 32-voice hardware mixer and adds an opt-in cache
for immutable sound effects. It preserves the native6 GPU architecture. The
prior GPU feature, image and performance qualification is referenced through
its manifest at tools/experiments/quake2_gpu_draws_20261013. Those large game
captures are not duplicated here, and no new gameplay FPS result is claimed.
The geometry worker now preloads its divisor and simplifies queue readiness
and address lookahead. Paired RTL and eight complete GPU frames preserve
outputs and cycles exactly; these changes target physical timing only.

Release assembly requires completed attempts for all 30 OS30 seeds and an
eligible selected artifact. If none qualifies, a separate source-only export
contains the application, core source and all test/sweep evidence. Its
selected_seed remains null, its state is source-only-unqualified, and it
contains no FPGA bitstream or release core directory. The prepared core has eight changed files relative
to native6, including the new sample cache and geometry worker changes. The verifier checks every recorded seed, reparses
its raw timing reports and repeats the selection rule. These tools do not
deploy, commit, mount an SD card or automatically start a seed sweep.

Verify, stage and rebuild the application:
  python3 package.py verify app
  python3 package.py stage app /tmp/q2-audio-app
  python3 build.py app /tmp/q2-audio-app /tmp/q2-audio-app-build
  python3 run_tests.py app /tmp/q2-audio-app /tmp/q2-audio-app-tests

For a source-only, unqualified export, use:
  python3 package.py verify
  python3 package.py stage core-source /tmp/q2-audio-core-source
  python3 build.py core-source /tmp/q2-audio-core-source /tmp/q2-audio-source-check --prepare-only
  python3 run_tests.py integration /tmp/q2-audio-core-source /tmp/q2-audio-mmio-tests --core-source
Use --core-source similarly with the other hardware logical test suites.
The source-only build command only prepares sources; it cannot select or
promote an unqualified seed. The strict release qualification is unchanged.

For a qualified release bundle, verify and stage the core:
  python3 package.py verify
  python3 package.py stage core /tmp/q2-audio-core
  python3 build.py core /tmp/q2-audio-core /tmp/q2-audio-core-build --prepare-only

To perform a single physical rebuild, use a different new output directory:
  python3 build.py core /tmp/q2-audio-core /tmp/q2-audio-core-build2 \
    --quartus-bin /path/to/Quartus25.1std/quartus/bin

Run the owned logical checks against staged sources:
  python3 run_tests.py mixer /tmp/q2-audio-core /tmp/q2-audio-mixer-tests
  python3 run_tests.py cache /tmp/q2-audio-core /tmp/q2-audio-cache-tests
  python3 run_tests.py integration /tmp/q2-audio-core /tmp/q2-audio-mmio-tests
  python3 run_tests.py contention /tmp/q2-audio-core /tmp/q2-audio-bus-tests
  python3 run_tests.py worker /tmp/q2-audio-core /tmp/q2-audio-worker-tests
  python3 run_tests.py write-path /tmp/q2-audio-core /tmp/q2-audio-write-tests
  python3 test_package.py

All staging and output directories must be new and have no symlink ancestors.
Requirements are Python 3, make and a C++ compiler, Verilator, riscv64-elf-gcc
15.2.0 for the app, and Quartus Prime Lite 25.1std Build 1129 for the FPGA.

The source archives include existing project vendor RTL and the pinned SDK
startup objects and static libraries required to build. They contain no game
PAKs or proprietary Intel simulation-model excerpts. The selected fabric
mixer uses no substituted DSP arithmetic helper.

The app archive contains the full 731-file native6 source/build inventory.
Only src/quake2/openfpga/snd_of.c changes production behavior. With
SOURCE_DATE_EPOCH=1791806400, its expected ELF SHA256 is:
  10581bdb05e98c87dcd08abb2798549267793920f6c91e1c6be964b0dd9fb636
The original clean build and a fresh archive-staged build match exactly.
The host-test runner makes a separate test copy and overlays an expanded
sound-backend fixture that maps the fixed mixer MMIO page. Production sources
and their archive remain unchanged by testing.

Audio extension ABI:
- MIX_CTRL at 0x48000820, bit 1, enables caching; reset/default is 0.
- Capability 0x48000834 returns SCF1 (0x53434631) when the cache is present.
- IRQ pending/W1C at 0x48000824 is unchanged.
- Every voice-register write invalidates that voice's cached line.
- Stream voices bypass caching automatically in hardware.
- Nonstream samples must remain immutable while playing. Disable or invalidate
  caching before modifying or reusing storage. This is not a coherent cache
  for arbitrary CPU writes.

The synthesis policy is effective global AREA. The QSF retains its CPU SPEED
assignment, but Quartus Lite removed the partitions; there is no evidence
that the CPU was independently synthesized for SPEED. Earlier global
assignments remain in the QSF, and the final one determines the setting.
Receipts record this history and the CPU assignment without claiming that
CPU mapping is unchanged or that extra timing margin is guaranteed.

Receipts also record global combinational physical synthesis, its effort
level, and any exact CPU-instance COMBO ON assignment. That scoped option
has been verified to modify CPU nodes in the fitter. The portable guard
allows only target ic|cpu|cpu; wildcard and other targets are rejected.
The prepared candidate uses global COMBO OFF, effort NORMAL, and the exact
CPU-instance COMBO ON assignment; final evidence records those settings. These options do not
change RTL behavior, and clock/SDC constraints remain unchanged.

The core is configured for 100 MHz. A restricted Fmax estimate above 90 MHz
is not timing closure at 100 MHz. Read the final selection and all setup,
hold, recovery, removal, pulse-width and nonmain-clock checks. Failed seed
attempts remain in the evidence. Only the selected bit-reversed RBF is copied
to core/os30.rbf_r; omitted binaries retain their original hashes in receipts.

Every fitted seed also checks cross-clock and input setup paths. An additional
routed-netlist check must prove that exactly 16 SDRAM rising-capture endpoints
are disconnected before excluding those endpoints from the used-path query.
Falling-capture paths and all consumers remain checked. Raw violations and the
per-seed topology proof are retained; the 100 MHz closure flag uses raw checks.

Synthetic audio tests compare exact PCM and sustained 48 kHz FIFO occupancy
at 90/100 MHz under bounded memory stalls and saturated synthetic CPU/GPU
traffic. The Quake2 workloads include mixed 11/22 kHz mono effects and an
additional all-22 kHz case, each with a 44.1 kHz stereo stream. Extreme
32-voice stereo workloads with short loops and high rates can still underrun.
These tests do not qualify physical SDRAM pins, audio CDC, device playback,
complete firmware scheduling or gameplay. Restoring audio also restores
CPU decoding and scheduling work; no guaranteed FPS benefit is claimed.

The final-source GPU/SDRAM write-path gate preserves the complete control
transcript, including one inherited SDRAM-model refresh warning at 1701
versus 1700 cycles. Color, depth, writes and total cycles remain exact.
This gate validates the directed workload, not physical DRAM retention.
