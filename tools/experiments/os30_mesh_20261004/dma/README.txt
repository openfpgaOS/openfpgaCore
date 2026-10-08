PRIVATE OS30 MESH DMA AND ORDERED FRONTEND
2026-10-04

Sources
-------
gpu_mesh_dma.v is a segmented, read-only AXI fetcher with two-bank ownership.
gpu_mesh_frontend.v combines it with the microengine, two 256x32 mesh banks,
and a 256x32 output command FIFO. Engine code/scratch RAM remain inside the
engine. No production source is changed by either module or these tests.

DMA contract
------------
desc_valid/ready accepts desc_bank, byte-address desc_addr, word destination
desc_dst, word count desc_words (1..256), and desc_commit. The first accepted
segment claims a bank; subsequent segments may assemble that same uncommitted
bank. A successful final segment with commit=1 marks the whole bank ready.
bank_ready remains asserted throughout consumer processing. Only an explicit
bank_release of a ready bank frees ownership. Releasing a filling bank is
ignored. Descriptor submission order and segment content are the scheduler's
responsibility; the generic DMA permits interleaved segments in both banks.

The descriptor must be 4-byte aligned, fit destination words 0..255, and not
wrap the external 32-bit address range. An end exactly at 2^32 is legal.
There is one outstanding burst, with at most eight words, never crossing a
32-byte boundary (therefore also never crossing a 4 KiB boundary). A bank RAM
write port must be available every response cycle: wr_en/bank/index/data is
a direct, unstalled write stream. There is no response FIFO or internal RAM.

Malformed descriptors produce bank_ready+bank_error without issuing a read.
Any RRESP error, early RLAST, or absent expected RLAST invalidates the bank.
The current response is drained through actual RLAST before reporting ready
and accepting another descriptor. A slave that never supplies RLAST requires
fabric reset; there is deliberately no timeout that could misattribute stale
responses to a new job. Partial bank contents are never published as valid.

Abort immediately discards bank ownership and suppresses writes, but maintains
any already-present ARVALID/address/length until accepted and drains the
outstanding R response before admitting new descriptors. reset_n is a shared
bus reset and may discard a transaction outright only when the arbiter/slave
also reset. These two mechanisms are intentionally distinct.

Frontend ABI
------------
The input/output ring interface matches gpu_geometry_worker with 12-bit
logical pointers; output storage is 256 words. Synchronous read ports use the
same lookahead input address contract as the original worker. Counters wrap
at 4096 independently of the physical 256-word output RAM. Output packets may
be streamed before their full payload has been produced, just as previously.

Private command 0x63, payload length 2..769:
    start PC, then one or more 32-bit microinstructions
The upload waits for all preceding jobs to retire into the output FIFO.
start+instruction_count must be <=768. Program memory is not initialized by
reset. A completed upload permits jobs; the caller must initialize every code
address it executes. Uploads may replace code only after earlier jobs finish.
A partial cancelled upload invalidates code_loaded. Soft/ring reset preserves
a previously completed program; GPU hard reset requires uploading it again.

Private command 0x62, exactly seven payload words:
    vertex address, vertex word count (1..128),
    index address, packed count metadata / index word count (1..64),
    state address, state word count (1..64), entry PC (0..767)
The index count low16 is the DMA word count. Upper16 zero retains legacy
one-triangle-per-word counts; upper16 nonzero is the actual triangle count
(1..64), with exactly ceil(count/2) DMA words. Compact indices pack three
5-bit vertex indices into each16-bit half. Compact layout requires vertex
word count divisible by4. Before each engine start, two idle cycles preload
scratch62 = vertex_words/4 and scratch63 = actual_triangle_count. Counts and
entry PC remain attached to their owning bank while the next descriptor loads.

The three segments go to bank offsets 0, 192, 128 respectively; state is the
final commit segment. Addresses/counts are snapshotted before fetching. Banks
alternate in submission order. Only the next bank in that order may execute.
The parser can submit bank N+1 while the engine processes bank N, but cannot
reuse a processing bank. The single descriptor register set is reused after
its final segment is accepted, with per-bank entry PC and ownership retained.

Ordinary GPU packets pass through unchanged only after preceding jobs finish
emitting into the FIFO. In particular, material updates and completion fences
cannot overtake a job. Releasing a mesh bank means its processor is finished;
it does NOT mean rasterization or external color/depth writeback is complete.
GPU completion fences retain responsibility for those downstream lifetimes.

fault is sticky and fail-stop until reset:
    1 malformed job/upload or job before a completed code upload
    2 DMA error
    3 microengine trap
No faulted job is silently retired as successful rendering. Output may already
contain a prefix of a faulted packet; recovery must reset the GPU/FIFO together.

reset_n resets the GPU parser, engine, and command pointers. bus_reset_n resets
the DMA fabric interface. GPU-only reset/soft_reset/ring_reset cancel and drain
DMA through its abort input. Both clocks are clk_cpu. This separation matters:
Pocket reset_n_cpu_media differs from sdram_cpu_reset_n.

Pocket integration
------------------
core_top.v currently ties arbiter M2 reads/writes idle around line 2907. Route
only M2 AR/R to the frontend's m_ar*/m_r*; retain idle AW/W. Connect bus_reset_n
to sdram_cpu_reset_n, while frontend reset_n follows the GPU/media reset.
Preserve the existing arbiter CPU/audio/M2 fairness policy. M2 uses the same
physical SDRAM bandwidth, not a separate bus or a coherent CPU cache port.
CPU cache clean/descriptor ownership remains necessary before submitting jobs.

Validation
----------
Run with a fresh output directory (scripts freeze source hashes and binaries):
  python3 tools/experiments/os30_mesh_20261004/dma/check_dma.py --out <new-dir>
  python3 tools/experiments/os30_mesh_20261004/dma/check_frontend.py --out <new-dir>

DMA final result: docs/measurements/os30-mesh-20261004/dma-test-v5/result.json
  3,025 successful committed jobs, 306 invalid/bus-error jobs, 601 soft aborts,
  102 shared resets, 94,410 read bursts / 583,270 response beats.
  Both standalone and production axi_sdram_arbiter paths run with randomized
  AR/R delays. CPU saw at most 8 intervening grants; M2 at most 13. Integrated
  grants GPU/CPU/M2/audio were 21122/5760/3840/21119. This proves transaction
  fairness for that workload, not physical SDRAM throughput/audio deadlines.

Frontend 768-word ROM baseline:
  docs/measurements/os30-mesh-20261004/frontend-test-v4-768/result.json
  59,479 checked output words, 9,694 read bursts, 47,012 DMA+engine overlap
  cycles, 16,801 FIFO-full cycles, 90 GPU/soft/ring cancellations, 7 fault cases.
  Boundary jobs execute through255/256,511/512 and entry767. Invalid uploads
  crossing768 and invalid entry768 are rejected. Updated packed metadata
  regression is recorded separately in frontend-test-v6-packed/result.json.
  Tests cover two-bank data ownership, engine STORE/LOAD, replacing programs
  between jobs, input/output ring wraps, ordinary commands larger than FIFO,
  stale-response draining when GPU resets without memory-fabric reset, and
  fail-stop malformed/bus-error handling. The test kernel copies/increments
  words; this is an actual overlap test, not a geometry/game speed result.

Earlier failed builds are retained as evidence, not counted as test passes.
DMA v1 was a C++ reserved-port-name build failure. v2/v3 exposed an invalid
background-master model holding ARVALID after handshake; its masters now
wait for their response before issuing a new read. Frontend v1/v2 missed the
new engine plane-module dependency; no RTL behavior was evaluated in them.

No full seed sweep, deployment, commit, or live/sibling source edit occurred.

Integrated raster/SDRAM replay
-----------------------------
check_gpu_replay.py stages the private frontend into the validated prepared
rasterizer and connects its M2 read port to tb_sdram_cont's actual production
arbiter/slave/io_sdram path and SDRAM chip/pin-timing model. It replaces each
0x61 packet with one projected-triangle 0x62 job. Each job reads 24 captured
input words plus two unused dummy index/state words. It uploads the real fused
triangle microprogram through 0x63 before replaying the ordered game packets.

These are already-projected vertices, not full raw meshes or a game CPU run.
Transform, lighting, clipping, CPU mesh caching and whole-mesh batching costs
are absent. Actual DMA, instruction execution, triangle setup, output FIFO,
rasterization, physical SDRAM transactions and contention are present. This
test is deliberately useful for exposing setup/overlap costs, not claiming FPS.

Backing inputs use unique 128-byte slots at SDRAM offset 0x03e00000. The script
checks masked physical addresses against every original MEM/RAND/DUMP range.
Identical backing bytes are inserted in both baseline and mesh vectors, so
the full-memory hash comparison remains meaningful. After excluding only HASH
output bytes, baseline output is also checked against the original capture;
framebuffer/depth/fence data must remain unchanged by the added input region.
Each converted packet is independently matched to its kernel-capture oracle.

Result: docs/measurements/os30-mesh-20261004/gpu-replay-all-v1/result.json
All 12 scenes / 10,341 jobs pass exact complete 64 MB hash, fence, color and
depth comparison. The entrance result is retained from gpu-replay-v2, with
matching program and executable hashes checked before reusing it. Frozen RTL
and executable are in gpu-replay-v1/build. That first script attempt stopped
before simulation because its address-overlap check had not masked the CPU
virtual alias in a DUMP record; the updated check uses the real 26-bit address.

The entrance has 1,130 jobs:
    Prepared baseline       2,015,302 cycles
    Actual mesh setup       3,332,315 cycles
    Engine active           2,399,832 cycles (includes 298,259 output-blocked)
    DMA active                369,721 cycles
    Fragment phase          1,522,074 cycles
    Engine + fragment         923,117 cycles
    DMA + engine + fragment   249,042 cycles
    M2 bursts / words           5,650 / 29,380

Thus concurrency works, but setup cost is substantial. The mesh replay is
39.52% lower throughput than precomputed packets, and about 50.1% more cycles
than the old complete GPU replay (2,219,716 cycles). Across all scenes the
throughput difference versus prepared input ranges from -4.76% to -39.52%.
Do not present this as a performance improvement. Whole-mesh batching could
amortize the small-job fetch overhead, but the arithmetic execution cost also
needs improvement and must be remeasured before making any speed claim.

Dedicated vertex shader and shared-resource validation
-----------------------------------------------------
gpu_mesh_vertex.v executes packed SM64 raw vertices using context scratch64..87.
It reads4 bank words, then writes7 expanded xyzw/ST/RGBA words. Context and all
architectural scratch words are read-only. Raw input is captured before output
writes, so overlapping input/output addresses are supported. VERTEX opcode48
uses scratch[A] as input bank pointer and scratch[B] as output bank pointer.

No multiplier, divider, or RAM is instantiated in the shader. It schedules the
engine's registered32x32 product, synchronous scratch read ports, bank RAM, and
shared iterative unsigned divider. Matrix transform, UV fixed-point wrapping,
full40-bit positive lighting intensity, RGB saturation and signed/zero/negative
W fog behavior follow client/mesh_transform.py. Fog uses32 division beats when
the positive numerator fits32bits and64 otherwise; nonpositive fog bypasses it.

Standalone result: vertex-unit-v1/result.json
  32,496 real captured vertices plus32,768 random/extreme vertices; all7 words
  exactly match the independent reference. 514 mid-operation aborts suppress
  writes and divider requests. Captured average70.165 module cycles/vertex.

Actual engine result: vertex-engine-v2/result.json
  65,264 programs,3,002,144 exact output words and exact instruction cycles.
  Each program COPYs context from bank to scratch, runs VERTEX, checks7 outputs
  and untouched context/driver registers, then executes MUL and DIVU to verify
  shared-resource handoff. Output backpressure is randomized. Sources were
  frozen before the later general instruction writeback timing changes.
  These tests prove shader arithmetic and integration, not whole-game speed.

Run with fresh output directories:
  python3 tools/experiments/os30_mesh_20261004/dma/check_vertex.py --out <dir>
  python3 tools/experiments/os30_mesh_20261004/dma/check_vertex_engine.py --out <dir>

Whole raw-mesh validation and measured limits
--------------------------------------------
whole-frontend-v2/result.json validates2,608raw jobs (32,496vertices and19,784
triangles) through randomized segmented DMA, metadata bootstrapping, compact
microcode and stalled FIFO:294,538 exact output/order-marker words,40,400read
bursts,484,669DMA+engine overlap cycles and939,486FIFO-full cycles. Material
values in this ownership test are explicit synthetic fixture inputs. The frozen
610word driver includes VERTEX,PROJ,GATHER but predatesPROJA/ATTR.

check_whole_gpu.py consumes the real material-textures-360-8 capture. For every
status0 supported job it compares independently prepared raster packets against
DMA of raw vertices, packed indices and the actual64word material snapshot.
ActualRGB565 texture bytes initialize both simulations. Identical job backing
memory is inserted in both, with checked nonoverlap versus texture/color/depth.
Both traverse the same prepared rasterizer, production arbiter/controller and
SDRAM pin/chip model. All color/depth dumps, GPU fence tokens and the complete
64MB memory hash must match. Unsupported jobs are skipped on both sides, so
this validates supported-job fixtures, not the original complete game frame.
The fixed-point reference is not asserted bit-identical to the old CPU float
clipper. Baseline packets are precomputed; its cycles omit CPU geometry work.

whole-gpu-all-v1/result.json: all24cases PASS,8frames times3traffic schedules.
Each frame has315supported and11skipped jobs. Across8unique frames:2,520jobs,
31,392vertices,19,112inputtriangles and7,888prepared draw packets. Traffic0 is
idle,1 is audio/scan scheduling,2 adds syntheticCPU read pressure. Mean cycles:
    Traffic      Prepared baseline      Whole mesh pipeline
       0              1,469,480               4,384,980
       1              1,549,690               4,434,153
       2              1,686,409               4,527,687
These numbers include programupload and fences after16jobs. They show the
then-current arithmetic pipeline still dominates; they are not anFPS gain.
Frozen615word code predatesPROJA/ATTR, so later speed changes need new evidence.

whole-gpu-continuous-v1/frame-360-m1/result.json queues all315supported jobs
before one final fence and also passes exactly:4,357,071cycles versus4,451,697
for16jobgroups. Thus the grouping overhead is~94,626cycles, not the principal
bottleneck. Continuous engine4,344,121cycles include415,953OUT-blocked cycles;
DMA268,384; raster1,294,418; engine+raster overlap1,289,439; allthree139,423.

Projected attribute expansion
-----------------------------
gpu_mesh_attributes.v implements ATTR52. scratch[A] holds the projected vertex
scratch pointer; scratch[B] holds bank destination; scratch[D] is combine mode.
It reads projectedS,T,zi,depth,RGB565C/D and writesS*zi>>16,T*zi>>16,zi,
G/R/B ofC inQ16,depth,thenoptionalR/G/B ofD inQ16. The threeD words retain their
previous bank contents if combine iszero. All scratch/flags/accumulator remain
architecturally unchanged; only the shared registeredDSP and read ports are
used. No local RAM/multiply/divide instance is added.

attributes-unit-v1/result.json:96,559vertices (31,023captured projected vertices
plus65,536random/allRGB565cases),780,355exact writes and375cancellation phases.
Module latency13cycles withoutD and16withD. Actual instruction dispatch adds
threecycles, giving16/19, confirmed by standalone engine/control comparison.
The fused_attributes=True kernel option is restricted to the compact scratch
input layout; existing unfused/default projected replay paths remain unchanged.

Final attribute fusion and FIFO measurements
-------------------------------------------
attributes-engine-v2/result.json:96,559actual engine programs,3,246,667exact
output words and exact16/19ATTR cycles, plus input/driver scratch guards and
following ordinaryMUL. Earlier attributes-engine-v1 data passed but exposed a
onecycle provisional latency underestimate; its failed cycle result is kept.

whole-gpu-attr-all-v1/result.json: final509wordPROJA+ATTR program passes all24
supported-job raster/physicalSDRAM cases (8frames times3traffic schedules), now
queueing each entire315job supported frame before its completion fence. Same
textures, color/depth and full64MBhash as independent prepared packets. Mean
meshcycles mode0/1/2:3,751,488 /3,799,343 /3,884,690. Actualengine arithmetic
and fullpipeline outputs agree with the separate complete-program tests.

Continuous frame360/audio+scan, fixed509word program:
  Output FIFO      Totalcycles     OUTblocked       AddedM10Ks vs256
      256           3,815,938        460,302                0
      512           3,693,327        334,406                1
     1024           3,533,721        166,235                3
All three exactly preserve complete memory and fence outputs. More buffering
lets geometry run during raster bursts; engine arithmetic remains3,341,946
cycles for this frame. The512wordFIFO gains3.32%throughput,1024gains7.99%, but
physical area/timing must be fitted separately. These are private frozen build
parameter overrides; the source wrapper default stays256words.

Parent'sCODE_PAGES parameter defaults3(768instructions), with final509word
program permitting2(512instructions). frontend-test-v7-pages2 validates full
512upload, crossing255/256,entry511HALT,entry512/overflowreject,59,977output
words,40per-bankmetadatajobs,90cancellationcases and10faults. Removing one
programRAM can fund a512wordFIFO without consuming another overallM10K.

Complete application replay and original-renderer fidelity
----------------------------------------------------------
check_runtime_gpu.py resolves raw/state/index segments from each MEM snapshot
at submission, retains ordinary commands and CPU completion fences, and checks
both the uploaded ISA and physical GPU/SDRAM against independent integer
transform/clip/project/packet references. runtime-gpu-v3 passes all 8 complete
frames 360..367, including projected fallbacks, material changes and 2D draws.
Color, explicit depth dumps, fence tokens and the full 64 MB memory hash match.
FIFO 1024, compact layout, two code pages, DOT and extended ISA disabled. Mean
GPU replay time 4,126,818 cycles (41.27 ms at 100 MHz). This replay excludes CPU
execution stalls; the parent's separate coupled simulator measures overlap.

Matching that integer reference does not imply matching the original SM64
rendering policy. runtime-original-gpu-v1/frame-360/FIDELITY.txt documents an
8,668-pixel difference against the original GPU capture. A reference-only
LOAD AABB guardband experiment reduces it to 3,176 pixels, while eliminating
all six depth-coverage differences. The remaining pixels arise from original
CPU-clipped high-precision reciprocal normalization versus the experimental
coarse reciprocal before normalization. Exact original guardband eligibility
and conservative CPU fallback for unsupported loads are being integrated.
Neither the diagnostic guardband reference nor the old full-six-plane output
is a validated hardware deployment recommendation.

Shortened projection division
------------------------------
check_project_skip.py validates the engine's optimized fixed-numerator divide.
For denominator d >= 256, k=floor(log2(d)): initialize quotient zero, remainder
2^(k-1), and execute 39-k restoring steps to obtain exact floor(2^38/d). The
skipped leading quotient bits are all zero because 2^(k-1) < d. PROJ and PROJA
costs become 72-k and 78-k, respectively, with unchanged output values.
project-skip-engine-v1 passes 85,744 actual engine programs: 84,992 prior
fixtures plus 752 signed/power-of-two boundaries, 2,143,600 exact words and
cycle counts, source/context preservation, following MUL/DIV ownership and
150 cancellation phases. A separate recurrence proof covers 130k denominator
values including the full unsigned 32-bit domain. Actual PROJ clamps signed
negative W to 256. Source freezing preserves the earlier timing tests.

VERTEX/ATTR arithmetic reset removal passed independent vector/cancel checks,
but increased fitted area in the parent's comparison. Those register resets
were restored exactly from the previously validated unit-v1 frozen modules;
no unvalidated reset-removal source remains in the candidate.
