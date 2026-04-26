# GPU lean-down plan: SDL2 + Quake target

Companion to `docs/gpu_alm_reduction.md` (catalogue of options) and
`docs/gpu_solid_blend_flip.md` (Quake-team feature wishlist).
This document picks a product profile and turns the catalogue into
a phased execution plan we will run.

## Mission

The GPU's job is to **accelerate the operations Quake's renderer
and SDL2's 2D backend actually spend time on**, and nothing else.
Any block that doesn't pull its weight against that hot path moves
back to software.

The deliverables of this plan, in order:

1. A leaner Pocket bitstream that closes 100 MHz with comfortable
   headroom (target ~80% ALM utilisation, ~100 M10K free).
2. An openfpgaOS-SDK SDL2 backend that maps the SDL2 2D
   primitives Quake uses onto the GPU's span path with no CPU
   framebuffer writes.
3. A Quake port whose render loop submits to the GPU and whose
   per-frame CPU cost drops to BSP/edge-list/lightmap-rebuild only
   — the inner pixel loops are gone.

## Target profile: "span-accelerator GPU"

This profile is intentionally **not** a 3D triangle GPU.  It is a
**span-based accelerator** for indexed-colour software-renderer
content, with the additional generic 2D primitives needed by
SDL2-style apps.  Quake's renderer is span-based natively.  SDL2's
2D primitives (`RenderCopy`, `FillRect`, `BlendMode`) decompose
to spans + clears.

The shape of the resulting GPU:

**Keep (HW-justified by the SDL2 / Quake hot path):**

| Block | Why HW |
|---|---|
| Ring buffer + command decode + fence/sync | Async submission and CPU/GPU overlap is the whole reason a GPU exists.  Cheap. |
| AXI4 framebuffer write master + 4-byte byte-strobe accumulator | Coalesces per-pixel writes into bursts.  Without this every pixel is a separate AXI burst — kills throughput. |
| `CMD_CLEAR` + `CMD_CLEAR_RECT` | Maps directly to `SDL_FillRect` / status-bar / letterbox.  Cheap (~300 ALMs total).  Last category of CPU `memset(frameplace, …)`. |
| `gpu_tex_cache` (TDP, 16 KB, 1024 sets × 16 B lines) | Texel fetch from SDRAM at 1 px/cycle is impossible without a cache.  Dual-port now serves both texture (port A) and palookup (port B).  Drop this and the inner loop becomes SDRAM-bound. |
| Affine texture span path (`CMD_DRAW_SPAN`, p0..p3 fragment pipeline, `tx_mul_q` DSP) | Quake's `D_DrawSpans` / `D_DrawTurbulent` hot loops; SDL2's textured `RenderCopy`.  The whole reason this fabric exists. |
| Perspective-correct span (`SPAN_PERSP`, PSS sub-FSM, recip LUT) | Quake's wall/floor renderer uses 8-pixel-segment perspective-affine spans.  Without HW, ~30% CPU spent on division per pixel. |
| `SPAN_COLORMAP` + `gpu_tex_cache` port B + multi-slot palookups | Quake's per-pixel light remap = `palookup[shade*256 + texel]`.  In-loop indirect fetch is what palookups exist for.  Multi-slot supports SDL2 palette-swap effects too. |
| `SPAN_SKIP_ZERO` (color-key transparency) | Sprite/HUD masking.  One compare per pixel in HW; otherwise a branch per pixel in SW. |
| `transluc[]` BLEND fabric (32 KB / 128×256 LUT) | Quake's full-screen fade, intermission, damage flashes; SDL2 `BlendMode` for paletted surfaces.  CPU equivalent is a 64 KB indirect on every blended pixel — cache-murder. |
| `tex_w_mask` / `tex_h_mask` POT wrap | Per-pixel mask in HW; saves the SW shift+mask branch in the inner loop.  Tiny RTL footprint. |

**Cut (not pulling its weight):**

| Block | Why SW |
|---|---|
| Triangle rasteriser (`S_TRI_*` × 12 states, 4 DSPs, gradient walk) | Quake doesn't use triangles — its renderer is BSP+span.  SDL2's `RenderCopy` is a textured rect = 2 spans per row.  3D apps that genuinely need triangles (rare for SDL2 content) emit them as per-row spans from CPU.  ~1,800 ALMs + 4 DSPs reclaimed. |
| Per-vertex Gouraud RGB walk (`v_r[3]`, `grad_r_dx/dy`, per-pixel `sp_light_step`) | Quake uses span-level flat shade (one light value for the whole span).  Per-vertex gradient is dead silicon for the actual workload. ~150 ALMs. |
| PSS Newton-Raphson refinement | The 1024-entry recip LUT alone gives ~10-bit precision.  Quake's 8-pixel persp segments need ~6-bit; LUT covers it with margin.  ~300 ALMs + 1 DSP. |
| Z-buffer + depth-test detour (`FBSS_ZREAD/ZWAIT/ZWRWAIT`, all 7 compare modes, 256 KB SRAM Z) | Quake's BSP+edge-list renderer does its own front-to-back ordering — no Z-buffer needed.  SDL2 2D doesn't need depth.  ~300 ALMs + the entire SRAM Z-buffer interface released for other use. |
| `SPAN_TRANSLUC_REV` | Documented but no shipping caller.  ~30 ALMs. |
| GPU debug observability in release builds (`GPU_DEBUG`, extended `GPU_STATUS`) | Diagnostic code paths.  Release builds don't need them.  ~150 ALMs. |
| Triangle perspective premul (`S_TRI_PERSP_PREMUL`) | Folded into triangle removal. |

**Question marks (decide before phase 2 starts):**

- `SPAN_COLUMN`: vertical-stride flag set by BUILD-engine vline.
  Quake doesn't emit it; SDL2 doesn't need it.  Cut candidate
  (~50 ALMs).  But check the cost first — it may be free if
  `fb_stride` already covers it.
- Negative `fb_stride` (reverse-stride spans, Duke3D `hlineasm4`):
  Quake doesn't use it.  Could narrow `sp_fb_stride` from signed
  16-bit to unsigned 16-bit + a column-direction bit.  ~20 ALMs.
- `CMD_DRAW_TRIANGLES_BATCH` (the multi-tri-per-cmd batch path):
  goes with the triangle rasterizer.  Listed for completeness.

**Estimated total:** ~2,800–3,200 ALMs reclaimed before any
"keeper" block is touched.  Bitstream lands around 14,000 / 18,480
(~76%) — comfortable headroom for Fmax closure and future fabric
work.

## Phased execution plan

Each phase has: **goal**, **gate** (what proves the phase is done),
**estimated time**, **rollback** (what to do if the gate fails).

### Phase 0 — Pin the baseline (BLOCKING for everything else)

**Goal:** Establish the exact ALM/Fmax/test-pass numbers a
release build produces today, so phase-1 cuts have a defensible
"before" snapshot.

**Tasks:**

1. Build with `VARIANT_DEFS=""` (no `GPU_DEBUG`, no `GPU_STATS`).
2. Quartus run on seed 13 (current best).  Capture
   `ap_core.fit.summary`, top-10 setup paths by entity from STA,
   M10K and DSP usage.
3. Run full Verilator suite (`make gpu`, `make gpu-floor`,
   `make gpu-gpudemo`, `make system`).  Capture pass/fail counts.
4. Document the recipe in `CHANGES.md`: Quartus version, seed,
   `VARIANT_DEFS`, PVT corner used for STA, the exact STA TCL.
5. Quote the **release-build** numbers as the new "before" — the
   debug build numbers are a separate baseline.

**Gate:**
- A single text artefact at `/tmp/baseline_release.md` (or in
  `docs/`) with: ALMs / M10K / DSP / Fmax / TNS / top-10 paths /
  `tb_gpu` pass count.
- The number in `gpu_alm_reduction.md`'s "status snapshot" is
  updated to the release-build figure.

**Estimated time:** 1 build + 1 STA + 1 Verilator full run = ~30 min.

**Rollback:** N/A — this is measurement only.

### Phase 1 — ABI-neutral cleanup (no feature change)

**Goal:** Take the cuts that are pure dead-code or
build-config flips.  No app-visible feature change.  Validate
the savings model before any product-scope decision.

**Tasks (one commit per item, in order):**

1. **A7 — Strip release-build observability.**  Make the Pocket
   release default `VARIANT_DEFS=""`.  Gate `GPU_STATUS` extended
   bits behind `GPU_DEBUG`.  Verify SDK `of_gpu_wait` and friends
   only need `{busy, ring_empty}` from `GPU_STATUS[1:0]`.
2. **A3 — Audit MMIO read mux for dead sources.**  Cross-reference
   `reg_rdata <=` cases in `gpu_core.v` against
   `firmware/api/of_gpu.h` MMIO macros.  Delete unreferenced
   sources (most likely the `GPU_DBG_*` branches that survive
   even with `GPU_DEBUG` off).
3. **A8 — Transluc-only MMIO.**  `GPU_CMAP_DATA` writes with
   bit 31 = 0 are no-ops since cmap_bram retired.  Drop the
   target-select gate, make `GPU_CMAP_DATA` write transluc
   unconditionally.  Update SDK headers.
4. **Verify gate.**  Re-run Verilator full suite.  Re-run Quartus.
   Compare delta vs phase-0 baseline.

**Gate:**
- All Verilator tests pass at same count as phase-0 baseline
  (modulo the 5 known regressions tracked in #59 #60 #61).
- Quartus ALM delta is **non-positive** (we expected savings, got
  savings or stayed flat).
- Top-10 setup paths report has no new path appearing in the
  worst-10 that wasn't worst-50 before.

**Expected savings:** ~150–300 ALMs.

**Estimated time:** 1 day if everything goes smoothly.

**Rollback:** Each item is a single commit; revert individually
if it breaks something downstream (SDK app reading a removed
debug register).

### Phase 2 — Profile-aligned feature pruning

**Goal:** Cut the blocks that are not in the SDL2 + Quake hot
path.  This is the big ALM-recovery phase.  Each cut is
**product-visible** (Verilator tests for the cut feature flip
to XFAIL or get deleted; downstream apps that used the feature
need a SW path).

**Pre-phase requirement (BLOCKING):**

Before starting, confirm via grep that no shipping app uses:
- Triangles (`of_gpu_draw_triangles*` calls) — except gpudemo
  mode 3, which is acceptable to lose / move to SW.
- Per-vertex `R` (vertex-color interpolation).
- Z-buffer (`of_gpu_set_zbuffer`, `of_gpu_depth_test` with anything
  but `NONE`).
- `SPAN_TRANSLUC_REV`.

If any unexpected caller is found, decide per-feature: is the SW
path acceptable, or is the feature genuinely required?  This is
the product-scope decision the catalogue's review feedback called
out — make it once, document it, then proceed.

**Tasks (each item is its own commit + Verilator run + STA):**

5. **C1 + A1 — Triangle rasterizer removal** (paired; A1 is
   subsumed).  This is the variant-system change the catalogue's
   review feedback flagged.  Concretely:
   - Re-introduce `GPU_FEAT_TRIANGLE` as a real on/off knob in
     `gpu_config.vh` (currently force-defined in
     `gpu_features.vh`).
   - Default Pocket bitstream sets it off.
   - Audit every `ifdef GPU_FEAT_TRIANGLE` wrapper to confirm the
     code inside actually disappears when the macro is undefined.
   - Delete: 12 `S_TRI_*` states, 3 vertex sets × 7 attributes,
     edge-equation regs, gradient compute, BBOX clamp,
     attribute init, persp premul, batched-triangle re-entry.
   - tb_gpu loses ~30 triangle tests; XFAIL or delete them.
   - gpudemo mode 3 (32-tri fan) goes; either XFAIL or rewrite
     as a per-row span emit from the CPU side.
   - **Expected savings:** ~1,800 ALMs + 4 DSPs.
6. **A2 — PSS Newton-Raphson removal.**  Remove `PSS_NR_MUL_X` /
   `_X_W` states and the second DSP feeder.  PSS_FINAL captures
   the LUT-output recip directly.  **Expected savings:** ~300
   ALMs + 1 DSP.  **Validation:** golden-frame comparison on a
   Quake-style oblique wall test scene; `zi_step=500/1000`
   characterisation tests degrade further (already failing).
7. **Z-buffer removal.**  This is a new entry not in the
   catalogue:
   - Remove `FBSS_ZREAD` / `FBSS_ZWAIT` / `FBSS_ZWRWAIT` states.
   - Remove `st_zb_addr` / `st_zb_stride` / `clear_depth` /
     `CMD_SET_ZB`.
   - Remove `SPAN_DEPTH_TEST` / `SPAN_DEPTH_WRITE` flag handling
     in the fragment pipeline (p2/p3 use unconditional
     pass-through).
   - Remove the SRAM word-port interface from `gpu_core.v` ports
     (`sram_*` outputs/inputs).  The Pocket SRAM chip then
     becomes unused — leave it physically connected but tied off
     in `core_top.v` so no PCB rework.
   - tb_gpu loses depth-related tests; XFAIL or delete.
   - SDK: deprecate `of_gpu_set_zbuffer` and
     `of_gpu_depth_test`.  Apps that tried to use them get a
     compile-time warning.
   - **Expected savings:** ~300 ALMs + the entire SRAM controller
     (free PCB-level resource).
8. **A5 — Drop Gouraud R-walk.**  Remove `v_r[3]`, the R-gradient
   compute, per-vertex `R` payload word, and per-pixel
   `sp_light_q` step in the persp-active branch.  Span path
   keeps `sp_light_q` as a flat-light register (per-span
   constant).  **Expected savings:** ~150 ALMs.
9. **A11 — Drop `SPAN_TRANSLUC_REV` if no caller.**  Remove bit-7
   handling in BLEND key composition.  Reserve the API flag.
   **Expected savings:** ~30 ALMs.
10. **Verify gate.**  After all five cuts: Verilator pass count
    matches the new (smaller) suite expected counts.  Quartus
    delta is positive (ALMs freed) by ~2,500–2,800.  Top-10 setup
    paths re-measured; flag any path that became worse than the
    pre-phase worst.

**Gate:**
- Verilator regressions (apart from intentionally-removed
  features) are zero.
- Quartus reports ≥ 2,500 ALMs freed cumulatively from phase-0
  baseline.
- Fmax improves OR stays within 0.1 ns of phase-0 (we don't
  expect Fmax to regress; if a cut accidentally lengthens a path
  via increased mux fan-in, fix it).

**Expected savings:** ~2,800 ALMs cumulative (+300 from phase 1
≈ 3,100 total).

**Estimated time:** 3–5 days.  Triangle-removal is the bulk —
plan a full day on it including the variant-system rework and
test-suite cleanup.

**Rollback:** Each item is its own commit.  If the suite of
Quake/SDL2 ports (phase 3) reveals a feature is needed after
all, revert the specific commit.  The variant-system change
(C1) makes triangle re-introduction harder than before — keep a
"triangle-on" CI build alive on a branch in case it becomes
necessary.

### Phase 3 — SDL2 backend on the lean GPU

**Goal:** Implement an openfpgaOS SDL2 backend that uses the
post-phase-2 GPU exclusively.  No CPU framebuffer writes.

**Tasks** (in `openfpgaOS-SDK/src/sdk/SDL2/`):

11. **Surface model.**  `SDL_PIXELFORMAT_INDEX8` only.  The SDL2
    `SDL_Surface` `pixels` pointer maps to GPU framebuffer SDRAM
    address; the SDL2 palette maps to `of_video_palette`.  Apps
    that try to create RGB surfaces get a clear error.
12. **`SDL_FillRect` → `of_gpu_clear_rect`.**  Trivial.  Caller
    pre-computes start byte addr.
13. **`SDL_BlitSurface` (textured rect copy) → span sequence.**
    For each row of the destination rect, emit one
    `CMD_DRAW_SPAN` with affine s/t stepping to walk the source
    texture.  Stride handles aligned and unaligned cases.
14. **`SDL_BlitScaled` (scaled rect copy) → span sequence with
    sstep/tstep != 1.0.**  Same shape as `BlitSurface` but
    sstep = src_w/dst_w in Q16.16, tstep similarly.
15. **`SDL_BlendMode` (`BLEND` / `MOD` / `ADD`).**  Map to the
    `transluc[]` LUT.  At app load time, build a 32 KB LUT from
    SDL's blend math against the active palette and call
    `of_gpu_translucency_upload`.  In-frame blends emit spans
    with `SPAN_TRANSLUC` set.  `MOD` and `ADD` are different
    pre-built LUTs swapped at blend-mode change time.
16. **`SDL_RenderDrawLine` / `SDL_RenderDrawPoint`.**  CPU-side
    Bresenham emitting a series of single-pixel `CMD_DRAW_SPAN`s
    with `count = 1`.  Acceptable cost — these aren't hot paths.
17. **`SDL_LockSurface` / `UnlockSurface`.**  Returns the SDRAM
    pointer; CPU writes pixel data; on `Unlock`, issue
    `of_cache_clean_range` so the GPU's tex_cache fill sees
    committed bytes.  This is the one path that retains a CPU
    write — but it's app-driven and rare (palette changes,
    procedural surfaces).
18. **Validation app.**  Port `sdl2-blit-bench`, `sdl2-pong`, or
    similar small SDL2 reference app and confirm pixel-correct
    output.

**Gate:**
- An SDL2 reference app builds, runs on Pocket hardware, and
  produces pixel-correct output against an SDL2 reference build
  on a workstation.
- Per-frame CPU FB writes: zero (verified via a guard sentinel
  in the FB that should never change).

**Estimated time:** 1–2 weeks.

### Phase 4 — Quake renderer port

**Goal:** Port Quake's renderer to emit GPU spans, reaching
playable framerates on Pocket hardware.

**Tasks** (in a separate Quake repo, e.g.
`PocketQuake/`):

19. **Identify the per-frame CPU work that goes away.**  The
    targets are:
    - `D_DrawSpans` / `D_DrawTurbulent` / `D_DrawSky` / sky-blit
      → `CMD_DRAW_SPAN` with `SPAN_PERSP` + `SPAN_COLORMAP`.
    - `D_PolysetDraw` (alias model) → either span emit (Quake's
      model rasterizer is span-based per scanline) or fall back
      to CPU if non-trivial.
    - `D_DrawSpriteSpans` → `CMD_DRAW_SPAN` + `SPAN_SKIP_ZERO`.
    - Particle drawing → CPU; few enough particles per frame
      that it doesn't matter.
    - Status bar / HUD / console / fades → `of_gpu_clear_rect` +
      `CMD_DRAW_SPAN` with `SPAN_TRANSLUC`.
20. **Surface cache and lightmap rebuild.**  Stays CPU-side.
    The lightmap rebuild emits cache lines that the GPU then
    pulls via `tex_cache` port A on the next span.  The cache
    flush after lightmap rebuild is the one place
    `of_cache_clean_range` is called per frame.
21. **BSP traversal, edge list, surface caching.**  Pure CPU.
    These were never on the GPU.
22. **Per-frame GPU command stream.**  Build a single ring
    submission per frame containing:
    `CMD_CLEAR` (or `CMD_CLEAR_RECT` for sky/floor color) →
    series of `CMD_DRAW_SPAN`s for the world →
    series of `CMD_DRAW_SPAN`s for entities/particles/HUD →
    `CMD_FENCE`.  No mid-frame `of_gpu_finish` calls.
23. **Performance gate.**  Target 30 fps on E1M1 at 320×240
    indexed-color.  60 fps would be a stretch goal.

**Gate:**
- Quake runs and produces pixel-correct output on E1M1 within
  ±1 byte vs a reference build.
- Per-frame CPU cycles spent in pixel-loop code: zero (verified
  via profile counter).
- Frame rate ≥ 30 fps in normal play.

**Estimated time:** 3–6 weeks.  This is the largest phase by
time and it's almost entirely software — the GPU-side work in
phases 1+2 is preparatory.

## Interleaving with the open regressions

The catalogue calls out three open regressions (#59 #60 #61) that
landed during the cmap-via-cache work.  They should be fixed
**before phase 2** — phase 2 is the big restructure, and chasing
end-of-span timing bugs in a half-restructured tree is a recipe
for silent corruption.

Concretely: phase 0 (baseline) → fix #59 + #60 + #61 → phase 1 →
phase 2 → phase 3 → phase 4.

If the regressions turn out to be hard (e.g., #59 needs deep PSS
work), pause phase 2's PSS-NR removal (A2) until #59 is closed —
the two changes touch the same code area.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Quake's perspective span needs >10-bit recip precision in some scenes | Med | Med — visible warping at oblique walls | Validate A2 with golden frames before committing.  If precision insufficient, keep one round of NR but cut something else for ALMs. |
| SDL2 app in the wild uses `SDL_PIXELFORMAT_RGB888` and the indexed-only backend rejects it | High | Low — apps see a clean error and adapt | Documented limitation.  If a target SDL2 app needs RGB, that's a separate variant-bitstream conversation, not this profile. |
| Removing the Z-buffer breaks a future SDL2 game that uses depth for layering | Low | Med — game can't run without rework | Layering can be done via paint-order in span emit; document the limitation in the SDL2 backend. |
| Variant-system reintroduction (C1) is heavier than expected | Med | Med — phase 2 timeline slips | Budget the variant rework as its own pre-phase-2 task; ~1 day of work on `gpu_features.vh` / `gpu_config.vh` / Makefile. |
| Triangle removal removes Gouraud and Z-test paths that some Verilator test depends on, and we lose coverage on a still-shipping feature | Low | Low — features are intentionally going | Audit tb_gpu before phase 2; explicitly XFAIL or delete the doomed tests in the same commit as the removal. |
| 100 MHz still doesn't close after the cuts | Med | Med — phase 4 Quake target degrades to <30 fps | Pull worst paths from STA after phase 2; if the binding path is in `cpu_system` not `gpu_core`, the right next step is a CPU-fabric fix, not more GPU cuts. |

## Acceptance criteria (whole plan)

When this plan is "done":

1. **Pocket bitstream** is at ~76% ALM utilisation, ~70% M10K,
   closes 100 MHz with ≥ 0.5 ns slack on the worst seed.
2. **`tb_gpu` passes** at the new (smaller) test-count baseline,
   with all phase-2 feature-removal tests cleanly XFAIL'd or
   deleted.
3. **An SDL2 reference app** runs pixel-correct on hardware and
   makes zero per-frame CPU FB writes.
4. **Quake** runs E1M1 at ≥ 30 fps with zero per-frame CPU pixel
   work (BSP/lightmap rebuild excepted, those happen at
   sector-change cadence not per-frame).
5. **`docs/gpu_architecture.md`** reflects the lean profile —
   triangle/Z-buffer/Gouraud sections deleted, SDL2 + Quake
   integration sections added.
6. **`CHANGES.md`** has per-phase entries with the measured ALM
   / Fmax / DSP deltas.

## Out of scope (deliberately)

- **Triangle 3D apps that need a real triangle rasterizer.**  If
  a future port of Doom 3 or a Wolfenstein-style raycaster
  needs HW triangles, that's a variant-bitstream conversation,
  not this profile.
- **RGB framebuffer / direct-colour rendering.**  This profile is
  indexed-8 only.  RGB requires a different fragment pipeline
  and a 4× bigger framebuffer; punt to a future SoC variant.
- **Bilinear texture filtering.**  Quake/SDL2 both work fine with
  point-sampled filtering at 320×240.  Listed as a Tier-2
  expansion in `gpu_architecture.md` §2.1; not on this plan's
  path.
- **Per-channel alpha blending.**  `transluc[]` paletted blend is
  enough for SDL2 paletted surfaces and Quake fades.  Real
  per-channel alpha needs RGB framebuffers (above).
- **Multi-target bitstreams (MiSTer, Analogue Pocket variants
  with different memory maps).**  The lean profile is
  Pocket-first; portability gets re-validated via
  `make check-api` after each phase.

## Open questions to answer before starting

These match the catalogue's review-feedback Q's, narrowed to
this plan's scope:

1. **Phase 0 numbers.**  What's the release-build (`VARIANT_DEFS=""`)
   ALM/Fmax baseline?  This blocks phase 1.
2. **Triangle-app audit.**  Apart from gpudemo mode 3, does any
   shipping app emit `CMD_DRAW_TRIANGLES`?  Blocks phase 2 step 5.
3. **Per-vertex `R` audit.**  Does anything except
   gpudemo's vertex-colour test emit non-constant `r`?  Blocks
   step 8.
4. **`SPAN_TRANSLUC_REV` audit.**  Any caller?  Blocks step 9.
5. **Variant-system rework cost estimate.**  How much of
   `gpu_features.vh` / `gpu_config.vh` actually has to change to
   support a `GPU_FEAT_TRIANGLE=0` build?  Block-budget for step 5.

The audits in (2)-(4) are quick greps; (1) is one Quartus run;
(5) is ~1 day of code reading.  Total time to clear all open
questions: under a day.

## Summary

The lean profile is "**span-accelerator GPU for indexed-colour
software-renderer content**".  It accelerates exactly what
Quake's renderer and SDL2's 2D primitives spend time on, and
nothing else.  Triangles, Z-buffer, Gouraud, NR refinement,
debug observability, REV translucency mode, and stale MMIO
muxes all leave.  The result is a smaller, faster, simpler GPU
that runs Quake at playable framerates on a hardware target
(Pocket) that today fails 100 MHz closure.

Phases 0+1+2 are the fabric work (~5–7 days).  Phases 3+4 are
the software work (~5–8 weeks).  Total wall-clock is dominated
by the software port; the fabric leaning is a fraction of the
investment.
