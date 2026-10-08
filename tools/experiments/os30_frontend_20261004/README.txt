SM64 / os30 attribute frontend, 2026-10-04

This extends the independent geometry worker beyond position transforms.
It consumes captured normals/colors and texture coordinates, performs one
directional light plus ambient and RGB565 conversion, and applies UV scaling
and tile bias. Multiple directional lights and texture generation keep their
CPU paths. CPU face culling and clipping fallbacks remain enabled.

The app keeps light/UV snapshots for surviving vertex slots. Matrix/light
changes do not force those attributes to be evaluated by the CPU. Immutable
input bounds reuse conservative frustum minima; a separate oracle compares
this shortcut with the old predicates over large, cancelling coordinates.

Private command extension, advertised by GPU_STATUS bit 12:
  0x5E, 8 words:
    Three signed Q30 coefficients (normalized direction divided by 127),
    diffuse RGB888 and ambient RGB888 (R in the low byte), packed unsigned
    scale_s | scale_t<<16, signed Q16 du, signed Q16 dv.
  0x5D, 4 words per vertex, 1..32 vertices:
    slot[4:0] | lighting<<5 | textured<<6 | signed_z16<<16
    signed_x16 | signed_y16<<16
    signed_s16 | signed_t16<<16
    raw RGB888 or signed normal bytes; alpha remains a CPU material input.
  0x5F, 17 words:
    Twelve existing position-matrix words, xc, yc, xs, ys, nearclip.

Legacy commands and the private 0x5C output are unchanged. Bit 11 still permits
32-vertex groups; older direct frontends receive batches of at most three.
The matrix/state bank and projected-point bank share one M10K. Output storage
remains 512 words. The existing multiplier performs the added arithmetic.
Separate register steps isolate color saturation and winding subtraction
from the multiplier's carry path. No new clock domain is introduced.

The optional mixer ramp pipeline prepares each channel's current-volume
plus/minus rate one state before clamping. One pair of nine-bit registers
serves both channels, using the existing target-multiply and left-ramp states.
PCM values, sample emission cycles, voice count and throughput are unchanged.

The hardware lighting is fixed point, not bit-identical to arbitrary float
lighting. The million-sample precision check includes exact axes and integer
boundaries; it bounds observed differences to one RGB8 value / one RGB565
channel step. Full first-level framebuffer and depth comparisons are separate.

The final P4 diagnostic header says ATTR on a matching core and LEGACY on an
older core. This identifies capability, not utilization. Select+L+R opens
the existing frozen performance snapshot; the profiling fields retain P3's
memory ABI. A non-diagnostic app is built separately.

Validation and artifacts
------------------------
Artifacts are in docs/measurements/os30-frontend-20261004/. The staged SM64
tree came from the existing local SM64 checkout and contains user-supplied
assets; it is not a distributable ROM bundle. before.json records source
hashes. No vendored SDK file is changed.

check_worker.py tests legacy and attribute streams against independent integer
oracles, including random upload/consume pauses, wraparound, enables, resets,
32-vertex batches, lighting, signed UVs, compact/legacy matrix state and winding.
Use --legacy for the existing protocol-only fixture.
attributes_snapshot.c checks 10,000 partial reload/state-change sequences.
check_visibility.py tests the actual staged geometry helpers and input cache.
check_bounds.py compares one million large-coordinate classifications.
check_lighting.cpp measures fixed-point precision independently of GPU RTL.
check_mixer.py compares the original, disabled and enabled audio pipelines:
14,223 sample values and emission cycles match; all 36 throughput cases match.
The independent ramp oracle covers all 16,777,216 current/target/rate inputs.
accept.py runs the complete os30 GPU acceptance suite from a frozen fit input:
129 checks each with quiet, delayed and interrupted memory transfers.

build.py creates coupled CPU-model/GPU-RTL executables. attributes-v6 is the
final worker with the color and winding register steps; worker is the legacy
capability model. Capability agreement is asserted at the model/RTL handoff.
run.py preserves every run in a new directory; analyze.py verifies inputs,
scene traces, all framebuffer/depth bytes, queue bounds, completions and the
saved-seed fit. The baseline is the retained installed worker/P3 app campaign.
The fixed route uses windows starting at frames 360, 500 and 600. Synthetic
traffic schedule 2 is an additional entrance-window sensitivity check.

Example (use a new label, preserving existing evidence):
  python3 tools/experiments/os30_frontend_20261004/run.py attributes-v6 trial-360-t1 \
    --elf docs/measurements/os30-frontend-20261004/attributes-p4.elf --start 360 --frames 8

CPU/cache timing is estimated; GPU/worker/SDRAM execution is RTL. Game time
advances one fixed step per submitted frame. External memory traffic is
synthetic; CPU memory-stall feedback and kernel mixing/interrupt costs are
not modeled. These results are not Pocket FPS predictions.

fit.py builds only os30 at saved seed 2. It never runs a seed sweep, commits,
or deploys. A successful fit does not mean the 100 MHz constraint passes;
REPORT.txt/results.json retain all setup/hold/Fmax limits.

publish.py prepares reviewable source patches and a matched diagnostic app,
normal app and os30 bitstream. It guards source and artifact hashes and keeps
rollback copies before publishing into the two local checkouts. The selected
runtime bitstream must be updated together with the packaged copy: make copy
otherwise restores the older runtime core. No SDK source is edited. The
publisher requires timing at least as good as the installed worker and does
not write to the SD card. An unfinished timing trial is not a release.

Rejected candidates are preserved for audit. Whole-face hardware culling
increased upload work and regressed frame time. Expanded cached face planes
gave too little improvement to retain. The first coupled prototype failed to
replace nested frozen/common RTL; its runs are explicitly marked INVALID.txt
and are not performance or correctness evidence.

The 256-word output buffer trials are also retained. Images were identical,
but the heavier traffic schedule lost about 0.7% against the 512-word queue,
and the narrower design exceeded the LAB limit in the saved-seed fits. It is
not selected. A simplified occupancy calculation was cycle-identical in five
paired windows but did not resolve that fit problem. The final selection
keeps the 512-word buffer. All resource trials use seed 2, not a seed sweep.
