SM64 / os30 geometry overlap experiment — 2026-10-04

Results: docs/measurements/sm64-overlap-20261004/REPORT.txt and results.json

This experiment executes the installed P3 SM64 ELF in the retained CPU timing
model while advancing the installed worker-v6 GPU and Pocket SDRAM RTL in the
same modeled time. GPU read pointers and fences come from RTL. Commands are
published when the application actually publishes them, with no artificial
wait for GPU idle after a batch. Uncached texture stores synchronize the RTL
before updating memory. The software GPU retains separate original memory
only for command/frame accounting; its pixel results are never the oracle.

The control uses the same source snapshot with the independent worker disabled
and the former GPU matrix frontend restored. Color/depth cache, prefetch,
clock and application are unchanged. This is an architectural performance
comparison, not a new FPGA fit or a trial bitstream.

The first 360, 500 or 600 frames run functionally to restore the established
Bob-omb Battlefield route. Each coupled window then runs eight frames. The
reported mean excludes the first handoff and uses seven submit intervals.
Schedule 1 injects retained synthetic scanout/audio SDRAM traffic. Schedule 2
adds synthetic CPU reads. Neither schedule couples actual CPU cache misses
back through the RTL arbiter, nor models interrupt/kernel/PCM mixing cost.

The game clock deliberately retains one fixed game update per submitted frame
so positions, animation and inputs match the existing scene trace. Therefore
modeled milliseconds are workload timings, not device FPS or actual 30 Hz
pacing/catch-up behavior. CPU caches and execution timing are estimates.

Counters:
  input_starved: worker HEADER/LOAD/COPY needs unavailable published input.
  output_blocked: worker cannot reserve/append its next output words.
  vertex_math: worker matrix/divider/project states, including arithmetic latency.
  cull_math: worker screen winding arithmetic states.
  worker_other: remaining command decode, transfer and emission cycles.
  math_raster_overlap: arithmetic concurrent with GPU command/draw pipeline
    activity (includes setup, payload consumption, clears and memory stalls).
  math_fragment_overlap: the subset concurrent with the fragment pipeline.
  cpu_render: cycles between gfx_run entry and final flip publication; this
    includes any ring/fence waits in that interval, not just useful CPU work.

The five worker categories partition every sampled cycle exactly. Occupancy
statistics cover the existing 4096-word input and 512-word output queues.
The final-batch tail measures completion after flip publication; it omits
ring stalls during earlier uploads. GPU busy time must not be added to CPU
time since both components run concurrently.

Reproduction (from a clean, new experiment output directory):

  python3 tools/experiments/sm64_overlap_20261004/build.py worker
  python3 tools/experiments/sm64_overlap_20261004/build.py direct
  python3 tools/experiments/sm64_overlap_20261004/run.py worker w360-t1 --start 360 --frames 8 --traffic 1
  python3 tools/experiments/sm64_overlap_20261004/run.py direct d360-t1 --start 360 --frames 8 --traffic 1

Repeat those runs for starts 500 and 600 and traffic 2, preserving the naming
pattern. Build and run scripts refuse to overwrite existing result folders.
Then:

  python3 tools/experiments/sm64_overlap_20261004/analyze.py

Validation covers 48 paired color/depth comparisons (96 RTL-rendered frames),
12 comparisons against retained independent replay output, identical scene
traces, exact counter partitions, completed fences/presentations, no ring
overflow and no SDRAM protocol/pin-timing failures. Sources and binaries are
hashed. The separate edge-overflow repair is intentionally outside the frozen
installed-core comparison. No production files, deployment or seed sweep are
part of this experiment.
