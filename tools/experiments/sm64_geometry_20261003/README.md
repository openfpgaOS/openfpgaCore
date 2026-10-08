# SM64 geometry accelerator trial

This is a functional os30 experiment. The approximate CPU model shows a
regression, not a speedup. It is intended for the user's local hardware trial;
no hardware deployment or full seed sweep is performed by these helpers.

The generated campaign is `docs/measurements/sm64-geometry-20261003/`.
`results.json` records final rendering checks, CPU estimates, GPU workload
cycles, resource use, timing, and the exact tested ELF hash. Intermediate
captures are retained to explain development decisions; `gameplay` is the
selected application revision and `fit-v1` is its unchanged hardware.

## Hardware ABI

`make build VARIANT=os30 OS30_GEOMETRY=1 JOB=os30-geometry` selects the opt-in
profile. Normal os30 builds retain their prior configuration. The experiment
keeps 100/100/100 MHz CPU/GPU/SDRAM and uses saved fitter seed 2.

- GPU_STATUS bit 10 advertises this geometry ABI. Read it after `of_gpu_init`.
- `0x50` installs three rows for clip x/y/w. Coefficients use Q8.24 and
  translations Q24.8; raw positions are signed 16-bit integers. The projected
  coordinates use the existing 1/256 prescale and subpixel viewport convention.
- `0x59` carries 5, 10, or 15 payload words for one to three cached vertices.
  Each vertex is `slot`, `x | y<<16`, `z | RGB565<<16`, `s`, `t`; s/t use Q16.16.
  A prior `0x4A` surface state is required. Unsupported lengths drain as no-ops.
- Projection uses `floor(2^32 / w_q8)`; independent high-precision depth uses
  `floor(2^38 / w_q8)`. Shared vertices retain consistent depth across software
  material fallbacks.
- `0x5B` draws cached indices in bits 0–4, 5–9, 10–14; bit 15 culls front
  faces and bit 16 culls back faces. Existing `0x54` semantics are unchanged.
- Old MAC command forms `0x51`–`0x53` are excluded in this specialized profile,
  and their peripheral capability bit is cleared. Existing `0x56`, `0x54`,
  and whole-triangle paths remain available. Hardware lighting stays excluded.

## Application behavior

The staged SM64 renderer defers transforms for eligible perspective batches
of at least eight vertices in gameplay scenes, conservatively inside all six
clip planes. Intro and file-select scenes retain the existing path. It
retains software clipping, fog, texture generation, lighting, translucency,
unsupported-material handling, and constant-depth menu rendering. Matrix
snapshots survive partial vertex-slot reloads and later matrix changes.

Matrix states are reused across batches. A conservative raw-space backface
test avoids unnecessary submissions, while hardware performs the final
subpixel cull. Consecutive triangle commands share prepared material state;
every other display-list command invalidates it before and after execution.

`GEOMETRY=0` selects a separate object directory for software-path comparison.
`GEOMETRY_REFERENCE=1` computes the same quantized transforms on the CPU and
uses existing GPU commands as a functional reference. Neither mode is a
hardware FPS oracle.

## Reproduction

Run from the core repository root. Each output label must be new:

```sh
python3 tools/experiments/sm64_geometry_20261003/trial.py accept LABEL
python3 tools/experiments/sm64_geometry_20261003/trial.py fit LABEL
python3 tools/experiments/sm64_geometry_20261003/capture.py LABEL --elf PATH
```

`build_replay.py` builds the actual GPU/cache/arbiter/Pocket-SDRAM model from a
frozen common-source directory and an acceptance manifest. `replay.py` adds
framebuffer and depth dumps, and exercises three memory-traffic schedules.
`analyze.py` gates the selected captures on their reference comparison and
checks the disabled-accelerator path against the original renderer.

The CPU harness captures commands; it does not implement the new geometry
opcodes as a raster oracle. All visual comparisons use GPU RTL. CPU and GPU
are measured independently, without hardware CPU execution, audio mixing,
submission overlap, firmware interrupts, or display pacing.

`publish.py` defaults to a read-only file review. Its explicit `--apply` mode
backs up the original source/runtime under SM64's `.obj/`, verifies no user
files changed since the starting snapshot, and copies only the reviewed
renderer, runtime, and trial notes. It never invokes `make copy` or commits.
