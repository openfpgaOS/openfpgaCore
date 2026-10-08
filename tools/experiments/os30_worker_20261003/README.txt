OS30 INDEPENDENT GEOMETRY WORKER PROTOTYPE
2026-10-03

Result

An independent fixed-function geometry worker now fits alongside the rasterizer
without reducing the main CPU cache. It replaces the serialized matrix and
projection frontend, runs ahead of pixel rendering, and performs winding tests.
It is an opt-in prototype, not a programmable RISC-V second CPU.

The prototype is correct on the retained tests, but its measured benefit is
modest: 5.36-5.87% more GPU throughput with the unchanged SM64 command stream.
Larger vertex batches reduce modeled CPU time by 0.60%; removing CPU culling
instead makes the tested renderer slower. These results do not establish the
substantial whole-game improvement the user wants.

Keep the installed SM64 package unchanged. The next useful worker stage needs
to take over a larger, coherent piece of CPU preparation, including attributes,
lighting, clipping decisions and display-list processing. Simply duplicating a
small CPU or offloading the winding calculation does not solve that problem.

Implementation

  src/fpga/common/gpu_geometry_worker.v
  src/fpga/common/gpu_core.v
  src/fpga/targets/pocket/variants/os30.mk

The worker owns a DSP multiplier, serial divider, local matrix/viewport state,
and a mirror of projected vertex positions. A 512-word (2 KiB) output FIFO lets
it overlap vertex processing with rasterization. The original 4096-word CPU
upload ring and its free-space interface remain in place. CPU ring readback now
reports words consumed by the worker; busy includes both worker and rasterizer.
Fences still pass through the ordered stream and retire after rendering.

Output words become visible after storage. The rasterizer's payload loader can
wait for more words inside a command. This permits long pass-through commands
and 32-vertex input batches without reserving a complete expanded command in
the small FIFO. A directed 1537-word command test exceeds the FIFO capacity.

Command behavior:
  0x50: bind local object matrix and viewport; consume without forwarding.
  0x59: transform/project 1..32 packed vertices, preserving fixed-point math.
  0x56: project a pre-transformed clip-space vertex, retaining explicit depth.
  0x5B: apply the existing exact screen-space winding test; surviving triangles
        become ordinary 0x54 indexed draws.
  0x5C: private worker-to-rasterizer cache load; not a public SDK command.
  Other commands retain ordering and pass through, including fences.

GPU_STATUS bit 11 advertises the worker and extended 0x59 batch size. Bit 10
continues to advertise the SM64 geometry command set. Existing SM64 commands
work without an application update. CPU lighting, clipping and rendering
fallbacks remain in use. The worker is only qualified for this os30 profile;
it is not a general replacement for every optional legacy transform command.
As with the current renderer, reset recovery rebinds state and uploads vertices
before drawing; no coherent shared vertex cache with a second CPU is provided.

Resource experiments, saved seed 2 only

Candidate                           ALMs   LABs  M10Ks  DSPs  Fmax MHz  Setup ns
Installed geometry + prefetch      17918   1847    307    41     91.04    -0.984
64 KiB CPU D-cache                  17910   1843    241    41     89.47    -1.177
64 KiB + texture queues in M10K     17893   1847    246    41     89.33    -1.195
Above + CPU shift-RAM inference    17902   1848    249    41     89.68    -1.151
Initial worker, large FIFO, D64     17938   1846    259    44     87.74    -1.397
Final worker, small FIFO, D128      18147   1846    308    44     91.23    -0.961
Device capacity                    18480   1848    308    66

Halving the CPU data cache really frees 66 M10K blocks, but saves only eight
ALMs in this comparison. The previous sensitivity model estimated 2.71% more
CPU time from that cut. Moving the texture queues saves another 17 ALMs net
while using five RAM blocks. The earlier approximately 90-ALM queue attribution
was not an achievable whole-fit saving. Re-enabling CPU shift-RAM inference
also fails to create substantial logic room. None of these cuts is selected
for the final worker.

The final worker is attributed 905 ALMs, four M10Ks and three DSPs. Replacing
the old frontend yields a whole-system net increase of 229 ALMs, one M10K and
three DSPs. There are two unoccupied LABs and no free M10Ks. This demonstrates
that this bounded worker fits; it does not demonstrate ample space for a
programmable processor, its program RAM and broader geometry hardware.

All fits retain a configured 100 MHz main clock. The final main-clock limit is
91.23 MHz, versus 91.04 MHz for the installed trial. Final worst hold slack is
+0.117 ns, recovery +4.012 ns, removal +0.337 ns and pulse width +0.500 ns.
Setup timing at 100 MHz is still negative. The final worst setup path is the
main CPU trap-state register to the GShare predictor RAM address, not the worker.
These are single-seed placement results, not a timing guarantee or seed sweep.

Two intermediate fits (worker-batch-v3 and worker-full-cache-v5) exposed a
long modulo-5 command decoder, reaching -9.328/-8.658 ns setup slack. They are
rejected. The final worker registers the header before decoding and explicitly
recognizes the 32 valid payload lengths; it contains no modulo divider there.

Performance and application experiments

GPU replay uses eight retained SM64 frames (40, 120, 200, 360, 400, 500, 600,
720), each with two Pocket SDRAM traffic schedules. Gameplay aggregates use
the five frames from 360 onward. The preceding geometry+prefetch renderer is
the comparison baseline.

Final worker, unchanged app commands:
  Schedule 1: GPU time -5.55%, throughput +5.87%.
  Schedule 2: GPU time -5.09%, throughput +5.36%.

Final worker, private 32-vertex batching client with CPU culling retained:
  Schedule 1: GPU time -5.64%, throughput +5.97%.
  Schedule 2: GPU time -5.13%, throughput +5.41%.
  CPU model: time -0.60% over frames 360-719; six equal windows improve by
             0.53-0.66%. All 721 frames follow the baseline scene trace.

CPU-culling offload was also tested in a private application copy:
  64 KiB D-cache, old batch size:    +3.95% CPU time (regression).
  64 KiB D-cache, larger batches:    +3.19% CPU time (regression).
  128 KiB D-cache, larger batches:   +1.39% CPU time (regression).

In the small-batch comparison, gfx_geometry_run self time falls from about
5.12 to 3.85 ms, but gfx_gpu_geometry_batch grows from 1.66 to 2.78 ms and
attribute preparation from 2.21 to 2.79 ms. Letting rejected triangles reach
the worker causes more CPU attribute work and command uploads. Moving only
the winding test is therefore counterproductive in this renderer. Restoring
CPU culling and retaining only larger batches avoids that extra work.

The worker-disabled control of the first modified binary itself costs about
1.07% CPU time versus the original binary, so compiler layout and new dispatch
overhead are part of these results. Percentages above compare complete tested
binaries to the original baseline; they are not isolated instruction costs.

CPU estimates omit real GPU backpressure, LCD scheduling and full audio
interrupt timing. GPU replays use real Pocket SDRAM RTL and synthetic traffic,
but textures are in SDRAM; the device normally uses CRAM1. CPU and GPU models
are not coupled. Their percentage improvements must not be added together or
reported as real-device FPS. No new Pocket performance-panel readings arrived.

Validation

  - Worker unit: 1920 mixed vertex batches, 7680 winding tests and 159071 exact
    output words; randomized input/output stalls, pointer wrap, enable holds,
    denominator edge cases and reset checks. The unit uses a 16-word output
    FIFO to stress bounded-buffer behavior.
  - Final worker GPU acceptance: 129 checks x 3 traffic schedules = 387 passed.
    Includes long streamed payload, an incomplete fence and fence resumption.
  - Worker-disabled current SM64 profile: 126 checks x 3 = 378 passed.
  - Final worker: 16/16 original-stream color and depth matches.
  - Final private batching client: 16/16 color and depth matches.
  - Five CPU application cases complete the same 721-frame route with audio
    work enabled and zero modeled ring overflow.
  - Final RTL matches the frozen fitted source except two documentation
    comments in gpu_core.v. Default profile flags remain unchanged, and the
    opt-in build uses the same full-cache CPU netlist as the final fit.

No full seed sweep, commit, SD-card copy or SM64 package update was performed.
The existing diagnostic application, runtime and rollback hashes are verified
by tools/experiments/sm64_device_perf_20261003/verify_publication.py.

Reproduction and retained artifacts

Tools in this directory refuse to overwrite existing experiment directories.
Use a new label for another fit/replay/capture or archive an old campaign first.

  python3 tools/check_geometry_worker.py
  python3 tools/experiments/os30_worker_20261003/accept.py NEW --mlab-queues
  python3 tools/experiments/os30_worker_20261003/replay.py NEW --mlab-queues
  python3 tools/experiments/os30_worker_20261003/fit.py worker --dc128 --mlab-queues --label NEW
  python3 tools/experiments/os30_worker_20261003/analyze.py

Optional resource trial: generate the private os30_worker CPU configuration
using tools/vexii-container.sh os30_worker, then run fit.py dc64. This does not
change the os30 CPU selected by the build profile.

prepare_app.py creates a private SM64 source/build tree under measurements.
Its default keeps CPU culling and enables batches up to 32 only on worker
hardware. --offload-cull reproduces the rejected broader submission experiment.
After building its normal PERF_DIAG=0 app.elf, capture.py LABEL --dc128 runs the
CPU model; replay.py LABEL --capture LABEL --mlab-queues validates its images.
The external SM64 source and package are not modified by these tools.

Opt-in core build, for further development (not a qualified release):
  make -C src/fpga/targets/pocket build VARIANT=os30 OS30_GEOMETRY_WORKER=1 OS30_DEPTH_PREFETCH=1 JOB=os30-worker

Primary evidence is under docs/measurements/os30-worker-20261003/:
  summary.json                             aggregate results and limitations
  fit-worker-v6/                           final frozen RTL, reports, bitstream
  accept-worker-v6/                        final worker regression logs
  accept-legacy/                           worker-disabled regression logs
  unit/                                   arithmetic/queue regression evidence
  replay-worker-v6/                        unchanged-app exact image comparisons
  capture-cpu-batch-only/                  private client ELF and CPU profile
  replay-batch-only-v6/                    private client image comparisons
  sm64-worker.patch                       retained-culling private client patch
  sm64-worker-initial.patch               initial experimental client patch

The prior feature inventory and architectural review remain in
tools/experiments/os30_feature_audit_20261003/REVIEW.txt. This report replaces
its resource estimates with actual fits where tested; it does not change the
earlier conclusion that a substantial gain requires broader CPU offload.
