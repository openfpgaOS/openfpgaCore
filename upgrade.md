# GPU Upgrade Plan

Continuation notes for the GPU optimization work. Pick up here in the next session.

## Current State

- **Tests:** 86/86 passing (`make test` from `src/fpga/targets/pocket/`)
  - Phase 1 spans: 55 tests
  - Phase 2 triangles: 31 tests (Z, S, T gradients; depth test; indexed draw; shared edges)
- **ALMs:** 17,215 (93%) on a clean map, 18,345 (99%) on incremental fits
- **Fmax:** ~91 MHz best seed
- **Reciprocal LUT:** moved to M10K with registered read (saves ~250 ALMs)
  - `(* ramstyle = "M10K" *) reg [15:0] recip_lut [0:255];`
  - Setup step 18 sets `recip_rd_addr`, step 19 captures `recip_rd_data`
  - Steps 19–55 were renumbered to 20–56 to make room
  - LUT formula: `2^22 / (256+i)` (14-bit, fits in 16-bit word)

## Dead End: Shared DSP Multiply

Attempted to share the registered DSP between triangle setup and per-pixel
tex-address multiply (`s*width + t`). Reverted.

- **Why it fails:** registered DSP has 2-cycle latency. The fragment pipeline
  consumes the tex multiply result combinationally (0-cycle). Sharing forced a
  2-cycle pipeline stall per pixel, broke `tri_tex_0_0` (expected 0x10, got 0x00).
- **Verdict:** keep the separate combinational tex multiply (~100 ALMs cost but
  correct). Don't re-attempt without also redesigning the fragment pipeline to
  tolerate the latency.

## Pending Optimizations

### 1. Microcode ROM for Setup FSM (IN PROGRESS)

Replace the 56-step `case (setup_step)` in `gpu_core.v` with a microcode ROM.

- **Savings:** ~400 ALMs
- **Cost:** 1 M10K
- **Complexity:** high
- **Approach:** encode each step as {dsp_a_sel, dsp_b_sel, dest_sel, next_state}
  in a ROM; a tiny sequencer walks the addresses. Setup becomes a datapath
  driven by ROM fields instead of a giant decoded case statement.
- **Risk:** encoding must cover every dsp/shift/assignment combo currently in
  the case. Audit all 56 steps first.

### 2. Pipelined Fragment Processor (PENDING)

Add a deeper pixel pipeline to the rasterizer.

- **Cost:** +800 ALMs
- **Benefit:** 8× fill rate (~100 Mpix/s)
- **Dependency:** needs the ~400 ALMs from (1) plus headroom. Current 99%
  fitter packing leaves no room without (1) or (3).

### 3. Alternative: Drop T Gradient (FALLBACK)

Set `grad_t_dx/dy <- 0` the same way R was dropped.

- **Savings:** ~200 ALMs (immediate, no refactor)
- **Cost:** textures no longer interpolate along T (V). Acceptable for many
  2D-ish games and most triangle strips where T is nearly constant, but breaks
  proper affine texture mapping.
- **Use as:** quick path to ~96% if microcode ROM turns out too disruptive.

## Key Files

- `src/fpga/common/gpu_core.v` — main GPU module (~1500 lines)
  - Triangle setup FSM: steps 0–56, registered DSP multiply (2-cycle)
  - Reciprocal LUT (M10K), CLZ normalization
  - Bounding-box walker, edge function incremental stepping
  - Fragment pipeline with Z/S/T attribute interpolation
- `src/fpga/common/gpu_tex_cache.v` — 2-way 16KB texture cache (M10K)
- `src/fpga/test/tb_gpu_main.cpp` — 86-test Verilator suite
- `src/fpga/targets/pocket/Makefile` — `build` target does `rm -rf db incremental_db` for clean placement

## Build & Test Commands

From `src/fpga/targets/pocket/`:

```bash
make test     # Verilator suite, ~30s
make check    # RTL syntax, ~45s
make build    # clean Quartus compile, ~7 min
make          # full: cpu → firmware → compile → test → deploy, ~9 min
```

## User Constraints (from memory)

- **Do not** run differential/partial builds unless asked
- **Do not** revert or stash user changes
- **Do not** push without explicit permission

## Recommended Next Move

Start by auditing the 56 setup steps in `gpu_core.v` and sketching the microcode
ROM field layout. If encoding looks ugly or the savings estimate softens, fall
back to dropping T gradient to unblock the pipelined fragment processor.
