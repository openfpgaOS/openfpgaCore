Private os30 whole-mesh geometry prototype
=========================================
This experiment gives the CPU immutable mesh descriptors to submit while a
small geometry processor transforms, lights, clips, projects and prepares
triangles. A two-bank DMA prefetches the next mesh while the current mesh
runs. A separate FIFO lets the existing rasterizer consume prepared triangles
at the same time. The CPU remains responsible for game logic, display-list
state and unsupported material/vertex cases.

Status: work in progress, NOT DEPLOYABLE. No production RTL, sibling SM64
package, SD card or saved fitter seed is updated by these scripts. No full
seed sweep is authorized. Fit scripts use only the existing os30 seed.

Directory map
-------------
engine: scratch-RAM processor, shared integer multiplier/divider, ISA emulator
        and randomized instruction/backpressure/cancellation regression.
kernel: exact prepared-plane/edge arithmetic, projection, gather, compact
        triangle program, original-GPU equivalence and lazy CPU fallback.
dma:    bounded-burst SDRAM reader, bank ownership, ordered command frontend,
        vertex/attribute sequencers and physical GPU/SDRAM replay tests.
client: immutable mesh/index cache, material classification, whole-mesh
        microprogram and independent integer scene oracle.
runtime: private SM64 producer, fence-owned state pages, trace capture and
         application model. The SDK is not modified.
companion: standalone RV32IMF firmware cost probes with exact host oracles;
           native CPU RTL bus harness and synthesis probe are under dma.
prepare.py: stage an isolated reduced rasterizer plus mesh frontend.
fit.py: isolated saved-seed Quartus compile; source hashes are frozen per run.

Current hardware configuration
------------------------------
100 MHz configured clock; actual timing must be measured after fitting.
The latest fit candidate uses a64 KiB CPU data cache. COMPACT_LAYOUT=1,
ENABLE_DOT=0, EXTENDED_ISA=0, CODE_PAGES=2, FIFO1024. The whole program is
410 words with projected-fallback entry404. Single shared32x32 multiplier and iterative
wide divider; dedicated short sequencers reuse those arithmetic resources.
The old vertex-triangle, transform and geometry-worker frontends are removed
only from staged hardware. The framebuffer/depth/texture/combiner pipeline
remains. The64 KiB candidate returns the six tiny raster staging RAMs to
M10Ks and removes TEX_QUEUE_MLAB. The final area-oriented saved-seed fit
plus removal of the unused dedicated texture chip uses18,310/18,480 ALMs
but1,868/1,848 LABs: it does NOT fit. This saved527 ALMs and52 LABs
compared with the prior area candidate. There is no valid timing result or deployable bitstream for the current design. Fitter
manifests, not projections from sub-block counts, determine feasibility.

Ordering and ownership
----------------------
0x63 uploads the private program after prior jobs retire. 0x62 submits a
seven-word descriptor with vertex, packed-index and state segments and entry.
Each of two256-word banks belongs to one job until computation completes;
state commit is last. No burst crosses32 bytes; outstanding SDRAM responses
are drained on GPU reset before reuse. Program/output faults fail stop.
Ordinary commands, materials and fences retain stream order.
The CPU's mutable state pages are reused only after GPU completion fences,
not command-ring consumption. Immutable mesh buffers are flushed before
publication. Texture epochs cover queued mesh reads. Partial vertex loads,
unsupported materials and mutable input use the saved-state CPU fallback.

Validation and interpretation
-----------------------------
Generated data and full logs live under docs/measurements/os30-mesh-20261004.
Independent tests cover arithmetic edge cases, cancellation, bank ownership,
ordered descriptors, complete shader state, all emitted words and cycle
counts. Real Bob-omb Battlefield captures include raw jobs, CPU-projected
fallbacks, materials, clears, texture memory and observed fences.
Complete physical raster/SDRAM replays check color and depth bytes and the
whole64MiB memory result against the independent integer contract. The
accurate hybrid path offloads only original guard-accepted LOADs. Saved-state
CPU fallback and cached projection retain the original rounding decisions.
Comparison with the original floating-point application's image is a
separate check; agreement with the integer contract alone is insufficient.
The functional CPU/cache model excludes actual raster stalls. The coupled
CPU/GPU model measures modeled overlap and frame completion, not Pocket FPS.
Only a fitting, timing-reviewed build followed by a device A/B test can
establish a user-visible improvement. See the latest result/report artifacts
for completed evidence and unresolved differences.

Reproduction
------------
Run scripts from the repository root. Most accept --label to create a new
result directory and refuse to overwrite a previous capture. Do not run fit.py
in parallel with source edits before its source freeze. Firmware builds use
the existing container toolchain in the isolated SM64 directory; no system
SDK or sibling application changes are required. Scene fixtures are derived
from the user's local assets and are kept in ignored measurement directories.

Measured decision gates (2026-10-04)
----------------------------------
Bob-omb Battlefield frames360..367, same fixed game step and external traffic:
  Original installed-app/control model   36.0737 ms/frame
  Accurate hybrid v3                     38.6456 ms/frame
  + cached shader conversion v4          37.7940 ms/frame
  + cached material templates v5         35.9211 ms/frame (selected)
  + exact reciprocal/noinline v6         36.2542 ms/frame
  + retained vertex contexts v7          36.5452 ms/frame
  + contexts + reciprocal/noinline v8    36.3695 ms/frame
  + contexts + scalar-copy correction v9 36.2047 ms/frame
All these accurate hybrids match all eight original color AND depth buffers.
The normal source was restored and rebuilt bit-identically to selected v5;
slower retained-context variants are kept only under kernel/retained_contexts.
The v5 improvement over control is only0.425% throughput, not substantial.
These are coupled-model results, not measured Pocket FPS.

The custom arithmetic program is29.15% faster than its earlier version on
frame360, but that component gain did not produce a similar frame-rate gain.
The initial general whole-mesh path reduced main-CPU estimated work21.85%,
but frame intervals grew12.6% and rendering precision changed. It is rejected
as a deployable optimization. Cache-size sensitivity alone was only0.05%.

An isolated cacheless RV32IMF companion synthesizes to3,096 ALMs,7 M10Ks and
4 DSPs, with98.14 MHz standalone timing. These figures EXCLUDE local program/
job RAM, memory adapters and GPU integration. Actual CPU RTL executing the
exact prepared-triangle C routine takes3,715,290 cycles for782 scene jobs,
versus631,004 cycles for the custom engine on the same jobs. Both emit exactly
31,100 words. Moving edge/plane setup to that CPU is therefore rejected;
geometry-only CPU offload still needs its own cycle and integration evidence.
Retaining the measured fixed edge/plane/setup/attribute/normalization units
adds at least1,285 ALMs, before shared arithmetic/control. Combined with the
CPU this already exceeds the custom engine, so it has no proven fit advantage.

Primary evidence under docs/measurements/os30-mesh-20261004:
  experiment-status.json and performance-checkpoint-v1.json
  selected-v5-source-manifest.json
  coupled-comparison-hybrid-v3.json / -v4.json / -v5.json
  fit-mesh-v8-area-notex/results/pocket-os30-s2/result.json
  tiny-imf-v1/scene-result.json and scene-custom-engine.json
  companion-triangle-host-v1/result.json
  engine-unit-final-v1/result.json

Coverage diagnosis
------------------
A separate diagnostic build counts reasons without using its timing as a
performance result. Across eight frames,15,810 pre-cull triangles are submitted
as whole meshes. All608 accepted-LOAD fallback triangles are decals(272) or
texture-alpha blending(336); there are no parser/mixed-load/capacity failures.
The additional4,006 triangles use nonaccepted LOADs and no accepted slots.
Decal float depth bias, two-pass alpha blending and continuous float zi scaling
are not exactly represented by the current one-pass fixed ABI. Do not enable
those selectors merely because the combiner recognizes their texture inputs.

The original lazy implementation computes2,848 retained but never-used vertices
on partial reloads in this window. A bounded32-context pool removes that work
and passes the full original-math oracle, but its copy/cache/control costs make
the complete modeled frame slower. It was not retained in the selected build.
See runtime-coverage-v1/coverage-summary.json and kernel/cpu_optimizations.txt.

Final RAM-placement audit
-------------------------
The latest fit contains only14 physical memory LABs in total. Moving every
one to M10K would still not close the20-LAB deficit, and two FIFOs require
asynchronous first-word fall-through reads. Merely changing their RAM hint
would change protocol timing. No further memory-hint fit was launched.
Further progress needs a different area/performance architecture; the current
prototype is preserved for review and is not presented as a deployable gain.
