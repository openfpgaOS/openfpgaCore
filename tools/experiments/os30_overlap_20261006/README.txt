Current F9 / retained-A CPU and GPU overlap experiment
====================================================

Artifacts: docs/measurements/os30-overlap-20261006/coupled/
Short results: REPORT.txt, results.json (18 eight-frame runs).
Follow-up: LONG_REPORT.txt, long-results.json (four 32-frame runs).
Final input/tool/evidence binding: final-manifest.json.

The tested hardware is the retained single-issue os30 A bitstream d3d90e20,
with no raw geometry worker or command DMA. Normal F9 is compared with private
kick128 and kick128-tail software candidates. No FPGA compile, deployment,
SDK edit or sibling application change is performed by these tools.

build.py verifies the existing phase6 CPU model and the retained-A GPU/SDRAM/
CRAM1 replay snapshot, copies both into this campaign, and adds host-only
coupling/probes. Synthesizable GPU and memory-controller files remain exact.
The earlier overlap builder supplies the existing CPU hook adaptation; its
script and all input/output sources are hashed. Compiler output is retained.

The functional CPU model warms the same intro/menu/Bob-omb Battlefield route.
At a completed frame boundary the harness creates/reset-initializes real GPU,
render-cache, SDRAM and CRAM1-controller RTL. It copies SDRAM and resident fast
textures, restores texture route/upload pointer, and translates the ring pointer
origin. All later GPU MMIO reaches RTL, including fast-texture uploads. CPU
uncached stores synchronize before mutation. The software GPU keeps separate
original memory and serves only command/frame bookkeeping, never raster output.

The RTL receives commands only when the executed guest publishes them. Read
pointers, fence polling and presentation come from RTL. Synthetic scanout/audio
and optional extra CPU reads are retained sensitivity traffic. Actual CPU cache
misses do not contend through the RTL arbiter, and cached CPU stores are host
memory effects. Thus this is a coupled CPU timing model/GPU RTL experiment,
not a cycle-accurate whole SoC or a Pocket FPS prediction.

Metrics distinguish GPU busy, fragment-stage occupancy, stalls, empty pipeline
cycles, and pipeline advances containing valid fragments. The latter is not a
pixel-retirement counter. CPU renderer scope is gfx_run entry through final
flip publication and includes guest waits. Empty command input is split by
CPU phase; fully idle additionally requires GPU busy clear. Publications and
ring/fence polls are counted; poll counts are not converted into exact wait ms.
The publishes.csv words column is cumulative; differences give batch sizes.

Primary timing is last submission minus first submission divided by N-1.
Handoff through first submission is excluded, not the first submit interval.
Final GPU tail is compared with next-frame renderer entry: all baseline tails
in the measured windows finish during the next game/audio work. Queue-pressure
improvements are therefore midframe effects, not recovery of the final tail.

Every run validates scene, geometry and culling records. Every paired frame
matches color AND depth. Each first frame also matches independent retained
F9 RTL replay. Paired command words and pixel counts match; raw command hashes
match whenever the actual selected framebuffer is the same. Modeled display
timing can change framebuffer selection, so address-bearing hashes across
different buffers are not falsely declared identical.

Result: earlier publication alone is slightly slower near frames360/500 but
helps near600. The tail variant gives ~0.35–0.43% modeled throughput improvement
near360/500; its instruction layout/I-cache changes prevent attributing that
tiny gain solely to scheduling. In the 32-frame600–631 follow-up the variant
improves modeled throughput2.61% and2.21% across the two schedules while reducing
ring polls by3290 and2712 per measured interval. No substantial hardware FPS
gain is established. A game/device trial remains separate from this evidence.

Reproduction (requires fresh destinations; existing evidence is preserved):
  python3 tools/experiments/os30_overlap_20261006/build.py
  python3 tools/experiments/os30_overlap_20261006/campaign.py
  python3 tools/experiments/os30_overlap_20261006/analyze.py

run.py accepts LABEL --elf PATH --start FRAME --frames COUNT --traffic 1|2.
The extended runs use baseline600-t1-long, baseline600-t2-long and
kick128-tail-600-t1-long / kick128-tail-600-t2-long with --start600 --frames32.
Then analyze_long.py performs the independent extended-run checks.
seal.py freezes final evidence after all runs/reports are complete.
