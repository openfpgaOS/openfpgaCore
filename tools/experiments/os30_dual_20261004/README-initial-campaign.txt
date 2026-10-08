OS30 LEAN DUAL-ISSUE FEASIBILITY EXPERIMENT
2026-10-04

Scope and result
----------------
This is a private experiment, not a production configuration or release.
The restricted dual-issue CPU can fit with the complete ordinary os30 GPU,
but the measured designs lose substantial timing headroom. No full-core
candidate has passed 100 MHz timing. No whole-game FPS improvement is established.

All full-core builds retain triangle setup, clip-fed vertices, combiner,
render cache, texture-memory interface and audio mixer. The independent
geometry worker is disabled. This is a comparison against ordinary os30,
not a game-level comparison against the currently deployed worker build.
The current Quake II edge-overflow corrections remain in every frozen input.
Only saved seed 2 is used: there is no seed sweep or deployment.

The fresh single-issue I32/D128 reference uses 17,248 ALMs, 1,846 LABs and
306 M10Ks. Its minimum main-clock Fmax is 92.53 MHz; worst setup is -0.842 ns
at the configured 100 MHz. The reference itself is not timing closed.

The first ALU-only dual uses 18,838 ALMs and 1,913 LABs and fails capacity.
Area optimization plus block-RAM texture queues reduces it to 18,146 ALMs
and 1,849 LABs: still one LAB too many. Moving the synchronous SDRAM burst
buffer to M10K finally fits at 18,112 ALMs / 1,848 LABs / 249 M10Ks, but
main-clock Fmax is only 85.32 MHz, setup -1.720 ns, hold +0.049 ns.

The smaller immediate-only lane fits at 18,075 ALMs / 1,847 LABs / 246 M10Ks,
but its Fmax is 84.85 MHz, setup -1.786 ns, hold +0.061 ns. Simply fitting
the logic therefore does not deliver a qualified faster core.

The final full-cache/smaller-predictor trial also fits: 18,141 ALMs, 1,847 LABs,
307 M10Ks. It keeps I32/D128, but its main-clock Fmax is only 82.26 MHz,
setup -2.156 ns, hold +0.022 ns. The worst path is fetch control through
instruction alignment/decompression into the second decoded instruction.
REPORT.txt/results.json contain the authoritative completed six-build table.

Decision: fitting dual issue is feasible without removing the GPU or cutting
either cache. It is not yet a qualified performance improvement. The clock
headroom loss can consume the component-level issue-width gains, and the
current frontend timing rewrites do not solve it. Retain these prototypes
for architectural work; do not promote any of them as a faster release.

CPU architecture
----------------
Both dual profiles retain the os30 ISA, full bypass, FPU behavior, radix-4
divider, pipelined integer-to-float conversion, and two-slot/sixteen-operation
store buffer. Multiplication, division, FP, memory and branch/shift execution
remain on the primary lane. The added lane shares the caches and implements
base integer ALU operations, or only immediate ALU operations in the smaller
profile. Upstream instruction eligibility, dependency checking, exception
ordering and shared CSR writeback behavior remain intact.

See generator/README.txt for private source patches, exact generated flags,
interface checks, source manifests and optional frontend timing experiments.
The additional decode/dispatch/forwarding paths are the principal cost, not
a duplicate FPU. Standalone CPU evidence and limits are in CPU_FITS.txt.

Correctness and performance
---------------------------
Eight CPU configurations pass 64 RTL program/traffic executions. Tests cover
integer/FP/atomic instructions, traps and timer interrupts, cache management,
30,000 int-to-float conversions, dependencies, subword writes, real Pocket
SDRAM and scanout traffic, and instruction/data capacity boundaries.
All checked outputs match and all memory/AXI/scanout scoreboards are clean.

At identical simulated clock rates, the ALU-only I32/D64 design improves
the measured SM64 vector function by about 10% and private captured-scene
triangle setup by 12-13%. The immediate-only lane retains the vector gain
but gives only 5-6% on triangle setup. These are component measurements:
the vector inputs are synthetic, and the private software triangle setup
is not the production renderer's CPU workload. There is no game FPS claim.

The I32/D64 design regresses the memory RAW test under heavy scanout and
severely regresses a deliberately larger 96 KiB working set. I16/D128 avoids
that data cliff but more than doubles cycles on the constructed 24 KiB
executable workload. Neither cache reduction is a general free optimization.

I32/D128 with BTB256/GShare1KiB preserves both original caches. Its measured
vector throughput is about +9.9%, triangle setup +11.1% quiet / +11.7% under
scanout, and memory RAW +6.1%. It still loses about 3.6% on the dependency-heavy
24 KiB indirect-call fixture. Predictor savings are five M10Ks, not seven;
the measured full-core memory budget leaves only one M10K available.

Validation reproduction, workload provenance and exact per-test results are
in validation/README.txt and the artifact validation/summary-all.json.

RAM and timing experiments
--------------------------
Texture queue migration uses existing synchronous RTL and forwarding.
WBUF_REVIEW.txt documents the SDRAM burst buffer: 491 passing checks,
32 poisoned read/write collisions and 335 checked continuation reads.
The final fitted buffer is one physical M10K, with one-cycle read latency
and OLD_DATA behavior; no no_rw_check assumption was added.

The worst CPU timing paths are instruction alignment/compressed decoding
and dispatch backpressure. Six private frontend rewrites pass extracted-cone
and exhaustive dispatch equivalence. Two standalone fits reject them as
solutions: grouped decompression adds area and worsens Fmax; control factoring
alone gives only 0.113 ns improvement. Neither is selected for a full-core fit.

Files and reproduction
----------------------
Artifacts (ignored, including private source trees and bitstreams):
  docs/measurements/os30-dual-20261004/
    REPORT.txt, results.json        full-core resources and all-corner timing
    generator-v*/                   generated RTL, original sources and hashes
    fit-*/                          frozen full-core inputs and Quartus reports
    cpu-fit-*/                      isolated CPU resource/timing probes
    validation/summary-all.json     all eight CPU validation/performance results
    production-input-check.json     unchanged production-source audit

Generate profiles with generator/prepare.py; labels and profile choices are
documented in generator/README.txt. fit.py accepts a private CPU netlist:
  python3 tools/experiments/os30_dual_20261004/fit.py NEW_LABEL --cpu CPU.v \
    --area --texture-queues-bram --write-buffer-bram
The RAM migrations must fit the selected CPU's memory budget; do not add them
blindly to the full-cache/smaller-predictor candidate. Existing output labels
are rejected. Regenerate the comparison with summarize.py after completed fits.

No production RTL/configuration, sibling game folder or deployed file was
changed. No commit or full sweep was performed. A future promotion requires
timing improvement plus representative full-game testing against the installed
build; the present evidence does not justify a release or a 30% speed claim.
