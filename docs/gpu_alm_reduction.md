# GPU ALM-reduction catalogue

Status snapshot (Pocket bitstream, current checked-in
`output_files/ap_core.fit.summary` + `ap_core.sta.rpt`,
2026-04-25):

- **ALMs: 17,169 / 18,480 (93%)** — 1,311 free.
- M10K: 220 / 308 (71%).
- DSP: 33 / 66 (50%).
- Fmax on the 100 MHz mp1 PLL output domain (the GPU clock):
  **81.34 MHz at Slow 1100mV 85C** and **78.36 MHz at Slow
  1100mV 0C** per `ap_core.sta.rpt` — design fails 100 MHz
  closure by ~2.3 ns (-1.0 to -1.2 ns slack on the worst path).
  A separate seed-13 build has reported 95.38 MHz (-0.484 ns)
  on a different invocation; **before any cut from this
  catalogue is selected, freeze a single build recipe and
  STA invocation as the canonical baseline** (see Phase 0 in
  `gpu_lean_plan.md`).
- Build recipe in the snapshot above: `VARIANT_DEFS=GPU_DEBUG`
  (the Pocket Makefile default — debug observability is still
  in the shipping bitstream until A7 lands).

The fabric is ALUT-dominated: each ALM has 2 LUTs + 4 FFs, used at
~74% LUTs / ~18% FFs.  **The cost is wide combinational logic, not
register count** — cutting registers alone doesn't move ALM totals
much; cutting combinational depth does.

This document enumerates every concrete option for reducing GPU
ALM utilisation, with **estimated savings**, **risk class**, and
**verification plan**.  It also flags Quartus knobs that look
attractive but regress timing and have been ruled out by past
experience (see `feedback_quartus_fitter_knobs.md`).

---

## Review feedback

This is a useful catalogue, but I would not use the current ordering as
an implementation plan without tightening a few assumptions against the
current tree:

- **Baseline is not pinned tightly enough.**  The ALM count matches
  `output_files/ap_core.fit.summary` (17,169 / 18,480), but the checked-in
  `output_files/ap_core.sta.rpt` currently reports mp_ram Fmax of 91.65 MHz
  at Slow 1100mV 85C (slack -0.911 ns) and 89.47 MHz at Slow 1100mV 0C
  (slack -1.177 ns), not 95.38 MHz / -0.484 ns.  Before selecting cuts,
  freeze one exact build recipe: seed, `VARIANT_DEFS`, Quartus version,
  PVT corner, and whether the report is `sta.rpt`, seed log, or a custom
  timing script.
- **Release vs debug build is mixed together.**  `targets/pocket/Makefile`
  defaults `VARIANT_DEFS ?= GPU_DEBUG`, so the published Pocket build still
  includes the stray-write latch / ring-write debug counter unless the make
  line explicitly strips it.  A release baseline with `VARIANT_DEFS=""`
  should be the first measurement because it may recover ALMs without a
  feature cut.
- **The variant model in C1 is stale.**  `gpu_features.vh` now force-defines
  `GPU_FEAT_TRIANGLE`, `GPU_FEAT_PERSP_SPAN`, `GPU_FEAT_FRAG_PIPELINE`, and
  `GPU_PERSP_IMPL`, while `gpu_config.vh` says the GPU has one hardware
  implementation.  Turning triangles off is therefore no longer just a
  `gpu_config.vh` define; it is a variant-system change.
- **Several "low risk" GPU cuts are really feature removals.**  A1, A2, and
  A5 are attractive ALM targets, but they remove behavior that current tests
  now exercise: perspective triangles, Quake-shape perspective spans, and
  Gouraud light interpolation.  Treat them as product-scope decisions, not
  cleanup, unless app greps and golden-frame tests prove they are unused.
- **The catalogue mixes GPU optimisation with whole-SoC tradeoffs.**  B1-B4
  can absolutely free ALMs, but they do not optimize the GPU itself.  Keep
  those in a separate "system headroom" track so a GPU-quality decision is
  not hidden inside a CPU/audio compromise.
- **Add timing-path locality to the ranking.**  ALM headroom helps the router,
  but the next pass should record the top 10 setup paths by entity before and
  after every cut.  If the worst paths are CPU/audio/address-generation paths,
  GPU-only feature cuts may improve utilization while failing to close 100 MHz.

My suggested re-rank for a first pass is:

1. Establish release baseline: `VARIANT_DEFS=""`, same seed, same STA script.
2. Strip production-only observability first: `GPU_DEBUG`, extended
   `GPU_STATUS`, debug MMIO readbacks, and any stats registers that survive
   when `GPU_STATS` is off.
3. Do ABI-neutral GPU cleanup next: transluc-only MMIO simplification,
   status mux trimming, and small dead flag/register removals.
4. Only then choose quality cuts (A1/A2/A5/A6) with explicit app/test fallout.

---

## Game-specific vs general-purpose functionality

The GPU is currently best described as a **retro indexed-color renderer
accelerator**, not a fully general 2D/3D GPU.  It has general-purpose command,
framebuffer, texture, depth, and triangle pieces, but the high-value span path
is deliberately shaped around Quake/BUILD/Doom-style software-renderer work:
8-bit texels, shade-table colormaps, wall columns, perspective spans, color-key
sprites, and paletted translucency LUTs.

That distinction matters for optimisation.  "Game-specific" is not
automatically bad if Quake/Duke-style engines are the product target; it should
just be budgeted as product functionality, not generic GPU capability.

| Functionality | Classification | Why it exists / current shape | Optimisation implication |
|---|---|---|---|
| Ring buffer, command decode, `NOP`, `FENCE`, `kick`, wait/fence registers | General platform plumbing | Any asynchronous accelerator needs submit/completion and backpressure. | Keep.  Only strip debug-only observability around it. |
| `GPU_DEBUG`, `GPU_DBG_*`, extended `GPU_STATUS`, stats counters | Debug/test-specific | Bring-up and hang diagnosis, not rendering output. | First release-build cut candidate.  Preserve minimal busy/empty/fence ABI. |
| AXI framebuffer write path, byte-lane accumulator, partial-word flushing | General GPU plumbing | Needed by spans, triangles, clear, translucent overdraw, and unaligned byte writes. | Keep unless replacing the whole I8 framebuffer model. |
| `CMD_SET_FB`, `CMD_CLEAR`, `CMD_CLEAR_RECT` | General 2D framebuffer utility, with game-motivated details | Full clear is generic; rect clear is useful for letterbox/status/menu panes and retiring CPU `memset(frameplace, ...)`.  It only writes an 8-bit replicated color. | Keep for I8 targets.  If a future `SOLID` fill/span lands, re-evaluate overlap with `CLEAR_RECT`. |
| `CMD_SET_ZB`, depth buffer, depth test/write detour | General 3D feature | Z is a standard raster feature, but this implementation stores 16-bit Z in local SRAM and exposes all GL-like compare modes. | Keep basic depth.  A9 can narrow compare modes if real apps only use `LESS`/`LEQUAL`/`ALWAYS`. |
| `CMD_SET_TEXTURE`, texture cache, affine texture address `t * width + s` | General textured-raster feature within an I8 pipeline | Texture fetch/cache and non-power-of-two `tex_width` are broadly useful.  Hardware is I8-only; SDK `RGB565`, wrap_s, and wrap_t fields are parsed but ignored. | Keep cache and multiply-mode addressing.  Do not retain dead API fields as a reason to keep hardware; they currently cost little/no RTL because they are ignored. |
| `CMD_DRAW_SPAN` affine spans with arbitrary `fb_stride` | Broad software-renderer primitive | Accelerates horizontal/vertical/strided runs without forcing triangle setup. | Core feature for both general 2D and retro FPS profiles. |
| `SPAN_COLUMN` | **Dead flag — not consumed by RTL** | Bit 1 of `of_gpu_span_t.flags`; declared as `localparam SPAN_COLUMN = 1` in `gpu_core.v` but no consumer in the datapath.  Vertical column stepping is actually driven by `sp_fb_stride` (set to e.g. 320 by the SDK for column walks).  The flag is reserved API surface only. | Not an ALM-cut candidate.  Either delete the flag bit from the SDK + the localparam (small API cleanup) or document it as reserved.  No HW logic to remove. |
| `SPAN_COLORMAP`, palookup SDRAM layout, `CMD_SET_COLORMAP_ID` | Game-specific indexed-color shading | Implements shade-row x texel palookups; comments explicitly call out Quake/BUILD shape and 16 slots. | Core for Quake/Duke.  Good removal candidate only for RGB/direct-color or unshaded I8 profiles. |
| `sp_light_q`, `sp_light_step`, per-vertex `r` gradient | Mixed: general interpolation, game-specific use | Attribute interpolation is general, but the current consumer is an 8-bit shade-row index for palookup. | A5 is a quality/product cut.  If retained, document it as indexed-light interpolation rather than generic RGB Gouraud. |
| `tex_w_mask` / `tex_h_mask` POT wrapping | Game/engine-shaped | Recreates BUILD/Quake shift-mode wrap for power-of-two world textures; triangles explicitly reset masks to no-wrap. | Cut only if world-span renderers do not rely on it.  It is not a general wrap/clamp implementation. |
| `SPAN_PERSP` + PSS reciprocal/NR machinery | Game-specific in current product, general conceptually | Perspective-correct spans are a classic software-renderer feature; the 8-pixel segment model is tuned for Quake/Duke wall/floor spans. | Treat A2 as core retro-FPS quality/performance functionality, not cleanup.  A general 2D profile could drop it entirely. |
| Triangle rasterizer, edge setup, bbox, depth, texture gradients | General 3D primitive | Broadly useful for demos, sprites-as-triangles, and future 3D APIs. | C1 is a variant/profile decision.  A span-only Quake profile might drop it; a general SDK GPU should keep it. |
| Perspective triangles (`v_w`, premul, triangle-emitted `SPAN_PERSP`) | General 3D feature | Needed for real perspective-correct textured triangles, less central to Quake/Duke span renderers. | A1 is a general-3D quality cut.  Safe only if target profile is span-first and app grep proves no non-affine triangles. |
| Batched triangles | General performance feature | Reduces command overhead/ring bandwidth for many same-state triangles; gpudemo uses it. | A10 is not game-specific, just optional.  Removing it trades ALMs for CPU/ring overhead. |
| `SPAN_SKIP_ZERO` and global triangle `SET_SKIP_ZERO` | Game-specific but broadly useful for 2D games | Palette color-key transparency for sprites/HUD/masked textures; triangles use sticky state so sprites can be two triangles. | Keep for retro content unless profiling proves no masked sprites/overlays. |
| `transluc[]`, `SPAN_TRANSLUC`, `SPAN_TRANSLUC_REV` | Strongly game-specific | Paletted 2D blend LUT, explicitly matching BUILD translucency and covering Quake fade/remap as a subset.  It is not general alpha blending. | Keep for Duke/Quake fade/translucency.  In a generic RGB or no-blend profile, this is a clean feature-family cut; A11 is the smallest sub-cut. |
| `GPU_TEX_FLUSH` | General coherency operation | Needed when CPU uploads textures/palookups and the GPU cache may hold stale lines.  Mid-flight behavior was tested because real content hit it. | Keep if CPU and GPU share texture memory. |

Profile-level read:

- **Generic accelerator core:** ring/fence, framebuffer write/clear, texture
  cache, affine spans, basic depth, and maybe basic triangles.  This is the
  reusable substrate.
- **Retro FPS / indexed-color profile:** keep `SPAN_COLORMAP`,
  `SPAN_PERSP`, POT masks, `SPAN_SKIP_ZERO`, `transluc[]`, and clear-rect.
  These are game-specific, but they are exactly the features that make
  Quake/Duke-style renderers fast and GPU-owned.  `SPAN_COLUMN` is a
  reserved API flag without HW behind it; column stepping is driven by
  `sp_fb_stride`.
- **General 3D SDK profile:** keep triangles, depth, affine/perspective
  triangle correctness, and texture cache.  Consider dropping palookup,
  column spans, POT mask emulation, and paletted translucency if the target is
  not indexed-color engines.
- **Minimal 2D/I8 profile:** keep clears, raw affine spans, skip-zero if
  sprites matter, and texture cache.  Drop perspective spans, triangles,
  depth, colormap slots, and translucency.

> **Cross-cutting note (Quake/triangle scope):** "Quake renderer" in
> the rows above means **the world/wall/floor span renderer** — BSP +
> edge list + `D_DrawSpans`/`D_DrawTurbulent`/`D_DrawSky`.  That path
> is span-only and does not need triangles.  Quake's **alias model
> renderer** (player, monsters) is also span-based per scanline,
> emitting `D_PolysetDraw`-shaped spans rather than triangles.  The
> only Quake path that *might* benefit from triangle hardware is the
> sprite renderer for billboards, and even that emits 2 spans per row
> in the existing C code.  C1 (triangle removal) is therefore safe
> for the **Quake span renderer profile**.  C1 is unsafe only for a
> hypothetical "general 3D SDK" profile that wants to expose
> `CMD_DRAW_TRIANGLES` to non-Quake apps.  Pick the profile, then
> rank C1 accordingly.

Recommended optimisation framing: choose the target profile first, then rank
cuts inside that profile.  Without that decision, the catalogue risks calling
Quake/Duke-critical features "game-specific waste" while preserving
general-purpose features that the actual shipped content may not use.

---

## Where the ALMs are (per-area breakdown)

Quartus's hierarchical fitter report stops at module boundaries;
`gpu_core.v` is monolithic.  Numbers below combine measured FF
counts, FSM-state counts, and reasoned scaling for combinational
logic — treat as ±20% per area, with rank ordering reliable.

| Area | ALMs | % of `gpu_core` | What it is |
|---|---|---|---|
| Triangle setup + scan + emit (`S_TRI_*`) | ~1,800 | 26% | 12 states (LOAD → SETUP → BBOX → BBOX_CLAMP → MUL_WAIT/2 → INIT_ATTRIB → PERSP_PREMUL → ROW → PIX → ROW_NEXT → GRAD); 3 vertex sets × 7 attributes; edge-equation A/B/C regs (3 × 32-bit); gradient-walk `grad_*`; bbox-origin attribute init; persp-triangle premul (`v_sw / v_tw`). |
| Fragment pipeline (p0…p3, p2b) | ~1,200 | 17% | 5-stage pipeline × ~10 32-bit fields/stage; tex_req issue logic; `cmap_req_addr_reg` latch; `tx_mul_q` DSP output reg; wide muxes for cmap/transluc-vs-raw color selection. |
| Perspective (PSS sub-FSM + Newton-Raphson) | ~650 | 9% | 19 PSS states (ADV/CLZ/TOP8/RECIP_W/RECIP_SHIFT/MUL/MUL_W/FINAL/RECIP_NA + NR_MUL_X variants); recip-LUT addressing; persp anchor/pend regs; `sp_zinv` accumulator; persp_active / seg_*_ready interlocks. |
| FBSS + fb_acc + BLEND | ~450 | 6% | 11 states (IDLE / FLUSH_W_RSP / Z* / BLEND_REQ / AR_WAIT / R_WAIT / LUT_WAIT / APPLY); 4-byte FB accumulator; BLEND scratch (src/word/lane/p3_flags + AR/araddr + transluc_rd_addr); SRAM Z-buffer interface. |
| Span params (`sp_*`) | ~350 | 5% | sp_fb_addr, sp_tex_addr, sp_s/t (Q16.16), sp_sstep/tstep, sp_count, sp_light_q/step, sp_zi/zistep, sp_z_addr, sp_fb_stride, sp_tex_width, sp_tex_w/h_mask + persp slot-A regs. |
| CMD decode + ring drain + main FSM | ~350 | 5% | 40-state main FSM in `state[5:0]`; `cmd_is_*` flag set; `pay_idx` / `pay_remaining`; ring r/w pointers; `ring_rd_data` register; the entire S_DECODE → S_PAY_DATA → S_EXECUTE dispatch. |
| CMD_CLEAR + CMD_CLEAR_RECT | ~300 | 4% | 8 states (CLEAR_INIT/FB/FB_WAIT/ZB/ZB_WAIT, CLEAR_RECT/WORD/WAIT); `clear_*` regs; `cr_*` regs; byte-strobe lane mux for partial-word edges. |
| MMIO decode + read mux | ~300 | 4% | 4-bit `reg_addr` decoder; 16-source `reg_rdata` mux; CPU-write paths for `cmap_wr_addr/_target` (transluc-only now); ring-write counter (gated under `GPU_DEBUG`). |
| Sticky GPU state (`st_*`) | ~250 | 4% | st_tex_addr, st_tex_width, st_depth_func, st_fb_addr, st_zb_addr, st_fb_stride, st_zb_stride, st_skip_zero, st_colormap_id; global `clear_*` carriers. |
| AXI write master (`m_wr_*`) | ~150 | 2% | AW + W + B handshake + completion FSM. |
| DSP-related combinational glue | ~150 | 2% | Triangle gradient + perspective recip-multiply DSP feeders, sign/zero-extend, pre-DSP register staging. |
| GPU_STATUS bit-pack + slack | ~150 | 2% | The `GPU_STATUS` packed-status reg (state + fp_pipe_stall + tex_state + fbss + p3_valid + dbg_setup_step + dbg_tri_det); residual logic. |
| `transluc[]` read port logic | ~70 | 1% | 15-bit `transluc_rd_addr` + lane mux + read-driver glue (M10K bits not counted). |
| `gpu_tex_cache` (separate module) | 169 | 2% | Dual-port M10K interface, 5-state FSM, AXI fill arbiter. |
| **Total `gpu_core` block** | **~7,050** | 100% | matches fitter's 7,048 ± rounding |

---

## Optimisation catalogue

Each option lists: **savings** (best estimate), **risk** (low/med/high),
**verification cost** (tb_gpu re-run / Quartus rerun / hardware test),
and **dependency** (does this break any current downstream caller?).

### A. Cuts within the GPU

#### A1. Drop triangle perspective premul (`S_TRI_PERSP_PREMUL`)

- **Savings:** ~200 ALMs + 1 small DSP slot.
- **Risk:** Low.  Pocket triangle test (4c.4 — `Triangle perspective vs
  affine`) currently passes; removing premul would XFAIL it but no
  shipping app emits `v_w != 0x10000`.
- **Verification:** tb_gpu run; expect 1 test to flip to XFAIL,
  others unchanged.  Quartus rerun.
- **Dependency:** None on Pocket today.  Quake / SDL2 3D apps
  would regress — but those don't ship yet.
- **How:** Remove `tri_persp_active`, the `S_TRI_PERSP_PREMUL` state,
  and the `v_sw` / `v_tw` register set.  Triangle path falls through
  directly from S_TRI_LOAD to S_TRI_SETUP.  Keep the `v_w` payload
  word so the on-ring layout is stable across re-add.

> **Review feedback:** This is not just one XFAIL in the current test
> suite.  The tree now has visible perspective-triangle coverage
> (`test_triangle_persp_premul_dormant`,
> `test_triangle_persp_vs_affine`,
> `test_triangle_persp_reference_match`,
> `test_triangle_persp_v0_offset`) plus Quake-bug probes.  The implementation
> note also needs to be sharper: removing only `S_TRI_PERSP_PREMUL` and
> `v_sw/v_tw` leaves the `tri_persp_active` gradient muxes, `grad_w_*`,
> row init, and span-emitted `SPAN_PERSP` behavior inconsistent.  This cut is
> really "affine-only triangles while preserving the payload ABI"; rank it
> medium/high risk unless the app grep proves no non-unit `w` triangle ships.

#### A2. Skip Newton-Raphson in PSS

- **Savings:** ~250-400 ALMs + 1 DSP slot.  Note: `persp_pss` is
  already a 4-bit state register with localparams 0..15, so
  deleting NR states does NOT shrink the state register itself.
  Savings come entirely from removing the second DSP feeder, the
  NR temporary registers, and the control muxes around them.
- **Risk:** Med.  Reduces persp-recip precision from ~16 bits to
  ~10 bits (LUT only).  Real-game persp spans on 8-pixel segments
  fit within the LUT's precision envelope, but the regression
  surface is wider than just the two characterisation failures —
  see Verification.
- **Verification:** Current tb_gpu has substantially broader
  perspective coverage than the original "2 zi_step characterisation
  failures":
    - `Persp span — constant 1/z (affine equivalent)`
    - `Persp span — segment swap, constant 1/z (32 pixels)`
    - `Persp span — varying 1/z (anchor sanity check)`
    - `Persp span — mid-segment curvature accuracy`
    - `Persp span — small |zinv| LUT scale (z ≈ 16384)`
    - `Persp span — slope-divide rounding bias`
    - `Persp span — negative zinv (defensive/no-hang)`
    - `SPAN_PERSP — exact Quake d_scan.c repro (oblique wall)`
    - `Perspective triangle vs CPU barycentric reference`
  Plus Quake-bug probes in the persp-triangle area.  A2's
  verification must include:
    1. tb_gpu run with NEW per-test slack budgets — some tests
       currently pass with `max_diff ≤ 1`; expect those to flip
       to `max_diff ≤ 2-3` after NR removal.  Update the per-test
       tolerances explicitly.
    2. **Golden-frame comparison** on a Quake-style oblique wall
       scene + a Duke3D water/slime sector frame — pixel-diff
       against a pre-A2 reference frame; require zero visible
       artefacts at normal viewing distance.
  Quartus rerun for ALM/Fmax delta.
- **Dependency:** Quake walls + Duke3D `SPAN_PERSP` paths.
  Acceptability per the cmap-cache spike methodology — the recip-
  LUT alone gives sub-pixel error on 8-pixel segments — but
  validate via golden frame, not just tb tolerance.
- **How:** Remove `PSS_NR_MUL_X`, `PSS_NR_MUL_X_W`, and the second
  DSP feeder for the Newton-Raphson refinement.  PSS_FINAL captures
  the LUT-output recip directly.  Number of *active* PSS states
  drops from 19 to ~15; the 4-bit state register remains.

#### A3. Audit MMIO read mux for dead sources

- **Savings:** ~80-150 ALMs.
- **Risk:** Low.  Pure dead-code removal.
- **Verification:** Read every `reg_rdata` case branch; confirm each
  is documented + actively used.  Drop the rest.  tb_gpu unaffected.
- **Dependency:** Apps that read those specific MMIO registers
  (debug-only counters most likely).
- **How:** Grep `reg_rdata <=` in `gpu_core.v`, list each branch,
  cross-reference against `firmware/api/of_gpu.h` MMIO macros.
  Anything not referenced from firmware/api becomes a deletion
  candidate.  GPU_DBG_BADWR / BADCNT already gated by `GPU_DEBUG`
  but the mux source still exists; gate the entire branch.

> **Review feedback:** The best first cut here is probably not the read mux
> itself.  With `GPU_DEBUG` off, BADWR/BADCNT/RINGWR are constants and should
> mostly collapse; with the current Pocket Makefile, `GPU_DEBUG` is on by
> default, so the real cost is the debug latch/counter/compare plus the
> extended status observability.  Measure `VARIANT_DEFS=""` first.  Also add
> `GPU_STATUS` extended fields to this item, because the 25-bit debug pack is
> likely a larger cone than the sparse register-address case statement.

#### A4. Fold `S_CLEAR_RECT_WORD/WAIT` into `S_FB_FLUSH/_WAIT`

- **Savings:** ~80-100 ALMs (fewer states, shared AW/W handshake
  logic).
- **Risk:** Med.  Both paths emit byte-strobed AXI writes; the
  shared logic is non-trivial because S_FB_FLUSH carries `fb_acc_*`
  state and S_CLEAR_RECT_* carries `cr_*` state.
- **Verification:** tb_gpu's `test_clear_rect` + all existing fb-acc
  paths must continue to pass.
- **Dependency:** None — internal refactor.
- **How:** Introduce a unified `clear_fsm` that distinguishes
  source by a flag; share the AXI handshake state.  Tedious but
  saves moderate ALMs.

> **Review feedback:** Be careful that this refactor may trade two states for
> a wider source mux (`fb_acc_*` vs `cr_*`) on the write-address/data/strobe
> path.  If attempted, stage a single `wr_addr/wr_data/wr_strb/wr_return`
> payload before entering a shared two-state M_WR issuer; do not leave the
> shared state continuously selecting between rect and fb-acc sources.  The
> expected savings should be validated because the byte-strobe arithmetic in
> `S_CLEAR_RECT_WORD/WAIT` remains either way.

#### A5. Drop Gouraud R-walk if no caller uses per-vertex `R`

- **Savings:** ~100-200 ALMs (`v_r[3]`, `grad_r_dx/dy`,
  per-pixel light-step in S_TRI_PIX, `sp_light_step`).
- **Risk:** Low if confirmed.  Med if a future Quake mod uses
  vertex-light triangles.
- **Verification:** tb_gpu's `test_vertex_color_interpolation`
  flips to XFAIL.  Spans still walk flat light via
  `sp_light_q + sp_light_step` (where `sp_light_step` is now
  always 0 from CMD_DRAW_SPAN); the triangle gradient path is
  what disappears.
- **Dependency:** Apps that emit per-vertex `R` ≠ a constant.
  Pocket gpudemo does; Quake doesn't (it uses span-level light
  via the colormap row).
- **How:** Remove `v_r[3]`, the R-gradient compute in S_TRI_GRAD,
  and the per-pixel `sp_light_q <= sp_light_q + sp_light_step` in
  the persp-active branch's load_p0 step.  Keep `sp_light_q` as a
  flat-light register loaded from `light` byte at S_PAY_DATA.

> **Review feedback:** Current RTL increments `sp_light_q` in the common
> `load_p0` path, not only in a perspective-active branch.  Direct spans are
> safe only because `sp_light_step` is zero.  Current tests also explicitly
> cover vertex-color interpolation, and gpudemo/batch-fan paths submit nonzero
> vertex `r` values.  This can still be a good ALM cut, but it should be a
> deliberate "flat triangle lighting only" product decision and the API docs
> should say that only `v0.r` is honored.

#### A6. Reduce `tex_cache` to 8 KB

- **Savings:** ~100 ALMs + 9 M10K (data_mem 16 → 8) + 1 M10K
  (tag_mem) + 1 M10K (valid_mem) = ~11 M10K total.
- **Risk:** Med.  Hit rate on Duke3D walls drops from ~94% to
  ~88% per the spike data.  Per-frame cycle cost +~15-20% in
  cmap-heavy scenes.
- **Verification:** Re-run cmap-cache spike with 512-set geometry.
  tb_gpu floor + persp tests should still pass within their slack.
- **Dependency:** Cmap-via-port-B path now shares a smaller
  cache; both texture and palookup pages compete more tightly.
- **How:** Change `SETS = 512` and `SET_BITS = 9` in
  `gpu_tex_cache.v`.  Address layout shifts (set index now 9 bits,
  tag becomes 13 bits).  Walk-clear S_INIT now 512 cycles.

> **Review feedback:** The implementation needs more than localparam edits.
> Current address slices are hard-coded around the 16 KB geometry
> (`req_addr[13:4]`, `pipe_addr[25:14]`, `lat_addr[13:4]`).  Make set/tag
> extraction helper wires/functions so the 8 KB version uses set `[12:4]`
> and tag `[25:13]` intentionally.  Verification should measure combined
> texture + palookup traffic through both cache ports, because port B now
> serves colormap lookups from the same reduced cache.

#### A7. Strip production debug/status observability

- **Savings:** likely ~50-150 ALMs; measure before trusting the number.
- **Risk:** Medium until the SDK blocker below is fixed.  After that,
  low for release builds (app-visible `GPU_STATUS[1:0]`,
  `GPU_RING_RDPTR`, and `GPU_FENCE_REACHED` are preserved).
- **SDK blocker (BLOCKING):** `of_gpu_submit()` calls
  `of_gpu_kick_verified()` (`firmware/api/of_gpu.h:367`), which calls
  `of_gpu_verify_ringwr()` (line 268).  That helper reads
  `GPU_DBG_RINGWR` (MMIO 0x38) and `__builtin_trap()`s if the hardware
  counter disagrees with the SDK's `_gpu_ringwr_count`.  With
  `GPU_DEBUG` off, `gpu_core.v:144` exposes the counter as constant 0,
  so any submission with ≥ 1 ring word traps the app immediately.
  This is a **release-build blocker** — A7 cannot land until either:
  (a) the SDK gates `of_gpu_kick_verified()` behind a runtime
  capability bit ("GPU_DEBUG present"), falling back to a plain
  `of_gpu_kick()` when the bit is clear, or
  (b) the kernel advertises `caps->hw_features & OF_HW_GPU_DEBUG` so
  apps can probe and skip the verify, or
  (c) `of_gpu_kick_verified` is removed in favour of `of_gpu_kick`
  unconditionally (the verify was added as a transient diagnostic
  during the gpudemo-freeze investigation; that bug is closed).
  Pick (c) for the simplest path; commit before A7.
- **Verification:** Build once with `VARIANT_DEFS=GPU_DEBUG`, once with
  `VARIANT_DEFS=""`, same seed.  Confirm SDK wait/fence paths still work
  end-to-end on hardware; run gpudemo + at least one app that submits
  ring traffic.
- **Dependency:** Bring-up diagnostics and crash dumps.  After A7 these
  are only available in debug-build bitstreams.
- **How:** First, land the SDK fix above.  Then make the Pocket
  release build strip `GPU_DEBUG`; gate extended `GPU_STATUS` fields
  behind `GPU_DEBUG`; keep only `{ring_empty,busy}` in production.  If
  `GPU_STATS` is off, ensure `stat_pixels/stat_spans` do not survive
  as reset-only output regs in synthesis.

#### A8. Simplify cmap/transluc MMIO to transluc-only

- **Savings:** small, probably ~20-60 ALMs.
- **Risk:** Low only after confirming no app still uses legacy MMIO colormap
  upload.
- **Verification:** Grep for `GPU_CMAP_DATA`, `of_gpu_colormap_upload`, and
  raw offset `0x24` writes outside the SDK.  Re-run transluc upload tests.
- **Dependency:** Old binaries that upload colormap through GPU MMIO instead
  of the current SDRAM palookup path.
- **How:** The source comments already say colormap MMIO writes are a no-op.
  If legacy compatibility is no longer required, drop the target-select
  register and make `GPU_CMAP_DATA` write the transluc table unconditionally
  (or rename the public register in the next ABI window).

#### A9. Narrow depth functions if apps only use LESS/LEQUAL/ALWAYS

- **Savings:** unknown, likely ~50-120 ALMs if Quartus is currently building
  all compare variants in `FBSS_ZWAIT`.
- **Risk:** Medium.  The API exposes all seven depth functions and tb_gpu
  currently tests GEQUAL/GREATER/NOTEQUAL paths.
- **Verification:** App grep plus tb update.  Add one golden scene for the
  actual depth mode used by triangle rendering.
- **Dependency:** Any app relying on EQUAL/GEQUAL/GREATER/NOTEQUAL.
- **How:** Restrict `st_depth_func` to NONE/ALWAYS/LESS/LEQUAL and collapse
  the depth compare case.  Keep enum values reserved in the public API so
  unsupported modes fail predictably.

#### A10. Drop batched triangle command support

- **Savings:** unknown, probably modest (~50-100 ALMs) because much of
  `pay_remaining` is shared command infrastructure.
- **Risk:** Medium.  Gpudemo mode 3 and current batch tests rely on it; CPU
  overhead and ring bandwidth increase if callers emit one command per tri.
- **Verification:** XFAIL or rewrite batch tests; run gpudemo mode 3 through
  the non-batched helper and measure frame cost.
- **Dependency:** Any renderer using `of_gpu_draw_triangles_batch`.
- **How:** Keep `CMD_DRAW_TRIANGLES` for exactly 3 vertices and remove the
  mid-payload triangle-done re-entry path back into `S_PAY_DATA`.

#### A11. Drop `SPAN_TRANSLUC_REV` if unused by real content

- **Savings:** small, probably ~20-40 ALMs.
- **Risk:** Low/medium.  It is tested and documented, but repo grep shows no
  current firmware caller beyond tests/docs.
- **Verification:** App grep and Duke3D transluc content check.  XFAIL
  `test_transluc_reverse_key` if removed.
- **Dependency:** Fade-table variants that need destination/source axes
  swapped.
- **How:** Remove bit 7 handling in BLEND key composition and reserve the API
  flag.

### B. Cuts within the CPU subsystem (`cpu_system`)

#### B1. Drop `FpuSqrt` plugin from VexiiRiscv

- **Savings:** ~66 ALMs.
- **Risk:** Low if no app uses `fsqrt`.  Most embedded code paths
  don't; Duke3D / Quake floor sqrt is integer.
- **Verification:** Build firmware + apps; confirm linker doesn't
  reference `fsqrt` opcode anywhere.  Soft-float fallback handles
  it via libgcc.
- **Dependency:** Math-heavy float apps (none currently).
- **How:** Remove the FpuSqrt plugin from
  `src/fpga/vendor/vexriscv/generate_vexii.sh`; regenerate.

> **Review feedback:** Current `generate_vexii.sh` exposes `--with-rvf`, not
> an obvious per-plugin list, although the generated Verilog does contain
> `FpuSqrtPlugin`.  Confirm the generator has a supported "RVF without
> FSQRT" option before treating this as a one-line change.  Also, `fsqrt.s`
> is part of the RISC-V F extension; removing hardware while still compiling
> apps for `rv32imafc` is only safe if the compiler never emits `fsqrt.s` or
> if the OS traps/emulates it.  A libgcc fallback only helps when the code is
> compiled to call a helper instead of emitting the instruction.

#### B2. Drop `DivRadix` plugin from VexiiRiscv (use iterative `div`)

- **Savings:** ~145 ALMs.
- **Risk:** Med.  Integer division becomes ~32× slower (iterative
  vs radix-2).  Apps doing many divisions take a perf hit.
- **Verification:** Build firmware + tb_system; measure boot time.
  If "negligible" (boot is dominated by other paths), accept.
- **Dependency:** Anywhere `div` / `rem` shows up in hot loops.
  BUILD's Q-format math may use it.
- **How:** Same as B1 — `generate_vexii.sh` plugin list.

> **Review feedback:** This item looks stale.  The generated CPU already
> instantiates a module named `DivRadix`, and that module's counter runs to
> `5'h1f`, which suggests a 32-step implementation is already in play.  If
> the intended change is "remove M-extension division entirely," that breaks
> `rv32imafc` binaries that emit `div/rem`.  If the intended change is a
> slower divider variant, identify the exact Vexii generator switch and
> measure it before assigning a 145-ALM saving.

#### B3. Drop second-controller synchronizers (`s_cont2_*`)

- **Savings:** ~50 ALMs (3 × `synch_3`).
- **Risk:** Low.  Only games using both Pocket controllers
  affected.
- **Verification:** Build + test single-controller game (Duke3D).
  Multi-controller test would XFAIL.
- **Dependency:** Multiplayer; SDL2 multi-pad apps.
- **How:** Remove the three `synch_3:s_cont2_*` instances from
  `axi_periph_slave.v`.  Plumb the bits to constant-zero in the
  read mux.

> **Review feedback:** This is straightforward, but it is a platform input
> feature cut rather than a GPU optimization.  If taken, also update capability
> docs/tests so the second controller is advertised as unsupported instead of
> silently reading zero forever.

#### B4. Halve audio_mixer voice count (32 → 16)

- **Savings:** ~300-350 ALMs.
- **Risk:** Med.  Dense MIDI tracks may starve voices.
- **Verification:** moddemo run with worst-case track; check no
  audible voice-stealing.
- **Dependency:** Audio quality tradeoff.
- **How:** Change voice-count parameter in `audio_mixer.v`; voice
  state RAM (vtbl_*) shrinks proportionally.

> **Review feedback:** This is larger than a parameter edit in the current
> tree.  `audio_mixer.v` uses 5-bit voice indices and 32-bit active/end masks;
> `axi_periph_slave.v`, `caps_table.c`, `hal.c`, `mixer.c`, `regs.h`, and the
> public API/caps all encode 32 voices.  It may still be an excellent whole-SoC
> headroom lever, but list it as an audio ABI/capability change with firmware
> edits, not a local RTL tweak.

### C. Structural moves (bigger savings, bigger risk)

#### C1. Remove triangle path entirely from Pocket bitstream

- **Savings:** ~1,500-2,000 ALMs + 4 DSPs.
- **Risk:** Med-High depending on profile.  In the **Quake-span /
  SDL2-2D profile** (`gpu_lean_plan.md`'s target), Quake's renderer
  emits spans not triangles for world, alias models, and sprites —
  so dropping triangles loses zero Quake functionality.  GPU demo's
  mode 3 (32-tri fan) breaks; an SDL2 app that wanted
  `SDL_RenderCopyEx` rotation would have to do CPU-side rotation +
  per-row span emit (cheap; rotated blits are rare).  In a
  hypothetical **general 3D SDK profile** (not currently scoped),
  triangles would be required and C1 is unavailable.
- **Verification:** Audit `of_gpu_draw_triangles*` callers.  Today:
  gpudemo only.  Quake will not be a caller per the lean-plan
  decision.  Any caller found is either a known-doomed test (delete
  it) or a profile-decision that must escalate.
- **Dependency:** Apps emitting `CMD_DRAW_TRIANGLES`.  Profile
  decision in `gpu_lean_plan.md` already commits to span-only.
- **How:** `gpu_features.vh` currently force-defines
  `GPU_FEAT_TRIANGLE`, and `gpu_config.vh` says the old variant
  matrix was collapsed into one always-on implementation.  A
  span-only bitstream therefore requires reintroducing a supported
  variant knob — promote `GPU_FEAT_TRIANGLE` from a forced define
  back to a `gpu_config.vh` knob (default off in the lean profile),
  then audit every `ifdef GPU_FEAT_TRIANGLE` wrapper to confirm the
  enclosed code actually disappears when the macro is undefined.
  Expect many triangle tests to need XFAIL or deletion, not just
  gpudemo mode 3.  Budget ~1 day for the variant-system rework
  separately from the deletion itself.

#### C2. Single-master CPU topology (revert 3-master split)

- **Savings:** ~300-400 ALMs.
- **Risk:** High — reverts the change that got Fmax to 92.65 MHz.
  Project memory `project_cpu_three_master_topology.md` documents
  this as a deliberate Fmax improvement.
- **Verification:** Quartus rerun with full STA.
- **Dependency:** Timing closure.
- **How:** Revert `cpu_target_port` split; route everything via
  the previous HubFiber.
- **Probably not worth it.**  Don't pull this lever unless the
  Fmax cost is acceptable.

> **Review feedback:** Keep this out of the GPU optimization sequence.  If
> timing closure is the goal, reverting a known Fmax improvement is
> counter-directional even if it frees ALMs.

#### C3. Remove `transluc[]` BLEND fabric

- **Savings:** ~250-300 ALMs + 32 M10K.
- **Risk:** High — Duke3D translucent walls / sprites lose GPU
  acceleration; revert to CPU fallback (and that requires
  `of_cache_flush`, partly defeating GPU-owns-FB).
- **Verification:** tb_gpu transluc tests XFAIL; Duke3D translucent
  rendering reverts to CPU.
- **Dependency:** Duke3D translucent path; long-term GPU-owns-FB
  closure.
- **How:** Remove `transluc_bram`, the BLEND state machine,
  `SPAN_TRANSLUC` flag handling, and the M0 read arbiter
  (port A would resume sole ownership of M_RD).
- **Don't do this without an explicit decision** — the BLEND
  fabric is what unblocks Duke3D translucent walls in hardware.

> **Review feedback:** Agree with the "do not do this" conclusion.  If BLEND
> has to give back ALMs, look for smaller cuts first: remove REV mode if
> unused, specialize the MMIO upload path, or consider a smaller/quantized LUT
> only with Duke3D visual comparison.

### D. Quartus knobs (NOT recommended at >90% utilisation)

These are listed for completeness; per
`feedback_quartus_fitter_knobs.md`, they regress timing once
ALM > 95%:

- **Resource sharing** — Quartus collapses shared adders /
  comparators across always-blocks.  Saves ~100-200 ALMs but
  worsens critical-path slack on the cmap+tex shared paths.
- **Register retiming** — moves FFs across combinational logic.
  Saves nothing on ALMs; may help Fmax.
- **MLAB inference for small RAMs** — currently 0 MLABs used.
  Could push some small register arrays to MLABs (~200 ALMs
  saved) but our small arrays are mostly state machine state
  vectors that don't infer cleanly to MLAB.
- **Seed sweeping** — already done (we have seeds 1-30+).  Best
  is seed 13 with -0.484 ns slack.  No knob recovers the missing
  0.484 ns purely through placement.

**Conclusion: Quartus knobs alone won't close 100 MHz at 93%
utilisation.  Structural RTL changes are the only path.**

> **Review feedback:** This conclusion is directionally right for a debug
> build, but measure the stripped release build before committing to feature
> removals.  Since `GPU_DEBUG` is currently on by default for Pocket, the
> first "structural" change may be deciding what observability belongs in the
> shipping bitstream.

---

## Recommended sequences

The catalogue above lists every option independently.  The
**canonical execution sequence** is in `gpu_lean_plan.md` — that
plan picks the **span-accelerator profile (Quake span renderer +
SDL2 2D)** and orders the cuts accordingly.  Do not pick items
off this catalogue à la carte without that profile decision; the
"is this a feature cut or cleanup?" answer changes per profile.

For reference, the lean plan's sequence is:

1. **Phase 0 — Pin baseline.**  Build with `VARIANT_DEFS=""`,
   capture release ALM/Fmax/STA top-10 paths.  Blocks everything.
2. **Phase 1 — ABI-neutral cleanup** (no app-visible feature
   change):
   - **A7** strip release-build observability (after the SDK
     `of_gpu_kick_verified` blocker is resolved — see A7).
   - **A3** audit MMIO read mux for dead sources.
   - **A8** transluc-only MMIO simplification.
   Re-measure; expected savings ~150-300 ALMs.
3. **Fix open regressions** (`#59 #60 #61`) before touching
   profile-aligned cuts — chasing end-of-span timing bugs in a
   half-restructured tree silently corrupts.
4. **Phase 2 — Profile-aligned feature pruning** (each one
   product-visible; one commit per item; golden-frame validation
   where rendering output is affected):
   - **C1** triangle rasterizer removal (~1,800 ALMs + 4 DSPs).
   - **A2** PSS Newton-Raphson removal (~300 ALMs + 1 DSP).
   - **(new)** Z-buffer machinery removal (~300 ALMs + frees
     SRAM Z chip).  Not in the catalogue's A1-A11 list because
     it's a profile-level cut, not a piecewise option.  See
     `gpu_lean_plan.md` Phase 2 step 7.
   - **A5** drop Gouraud R-walk (~150 ALMs).
   - **A11** drop `SPAN_TRANSLUC_REV` (~30 ALMs).
   Re-measure; cumulative savings target ~2,800-3,200 ALMs.

**A1** (persp triangle premul) is subsumed into C1.

**B1/B2** (VexiiRiscv FpuSqrt / DivRadix) are not part of the
lean plan — they're whole-SoC headroom levers, listed in the
catalogue for completeness but not on the GPU path.  They need
separate CPU-generator audits before being attempted.

**B3** (cont2 synchronizers) is a platform-input feature cut, not
a GPU optimisation.  Same separate-track note.

**B4** (audio voice halving) is a firmware-visible audio
capability change.  Same separate-track note.

**A4** (CLEAR_RECT fold) and **A6** (tex_cache halving) are
optional polish — pick up only if the post-Phase-2 ALM/Fmax
delta isn't sufficient.

**A9** (depth funcs narrow) is moot under the lean plan — Phase
2's Z-buffer removal subsumes it.

**A10** (drop batched triangles) is subsumed into C1.

After Phase 2, expected position: ~14,000-14,400 ALMs
(~76-78%), ~15-25 M10K freed (the SRAM Z is external — frees an
off-chip resource), 5+ DSPs freed.  That's the position from
which Fmax closure work + SDL2 backend + Quake port begin.

---

## Verification matrix

| Optimisation | tb_gpu | tb_floor | tb_gpudemo | tb_system | Quartus | Hardware |
|---|---|---|---|---|---|---|
| A1 persp premul drop | re-run | unchanged | re-run mode 3 | unchanged | rerun for ALM/FMax | unchanged |
| A2 NR drop | 2 persp tests fail more | unchanged | unchanged | unchanged | rerun | quake walls visual |
| A3 MMIO mux audit | unchanged | unchanged | unchanged | unchanged | rerun | unchanged |
| A4 CLEAR_RECT fold | re-run | unchanged | unchanged | unchanged | rerun | unchanged |
| A5 Gouraud drop | vcol test fails | unchanged | re-run mode | unchanged | rerun | gpudemo visual |
| A6 tex_cache halving | spike + tb | re-run | re-run | unchanged | rerun | full play-test |
| A7 debug/status strip | wait/fence + debug helpers | unchanged | unchanged | unchanged | rerun `GPU_DEBUG` on/off | release smoke |
| A8 transluc-only MMIO | transluc upload + cmap upload path | unchanged | unchanged | unchanged | rerun | legacy app check |
| A9 depth funcs narrow | depth mode tests adjusted | unchanged | re-run depth scenes | unchanged | rerun | z-buffer visual |
| A10 triangle batch drop | batch tests fail/rewrite | unchanged | mode 3 perf/regress | unchanged | rerun | gpudemo visual/perf |
| A11 TRANSLUC_REV drop | reverse-key test fails | unchanged | unchanged | unchanged | rerun | Duke transluc check |
| B1 FpuSqrt drop | unchanged | unchanged | unchanged | re-run | rerun | unchanged |
| B2 DivRadix drop | unchanged | unchanged | unchanged | re-run | rerun | full play-test |
| B3 cont2 synch drop | unchanged | unchanged | unchanged | unchanged | rerun | single-pad only |
| B4 mixer voices/2 | unchanged | unchanged | unchanged | re-run audio | rerun | audio play-test |
| C1 triangle removal | many fails | unchanged | mode 3 fails | unchanged | rerun | gpudemo regress |
| C2 single-master CPU | unchanged | unchanged | unchanged | re-run | full STA | timing-driven |
| C3 transluc removal | 4 transluc fails | unchanged | unchanged | unchanged | rerun | duke3d translucent regress |

---

## Methodology notes

### Measuring savings

For any RTL-side change:

1. Record the exact build recipe: seed, Quartus version, `VARIANT_DEFS`,
   and whether the target is debug or release.
2. Run tb_gpu / tb_floor / tb_gpudemo / tb_system (whichever apply).
3. Run Quartus on a single seed for a fast snapshot.
4. Compare `ap_core.fit.summary` ALM line against the pre-change
   commit's snapshot.
5. If ALM delta is within ±5% of estimate, the change behaves as
   modelled.  If >10% off, dig into `ap_core.fit.rpt`'s
   "Resource Utilization by Entity" section to find what changed.

### Measuring Fmax effect

The 100 MHz mp_ram domain is the binding constraint.  Pull the
worst path with `quartus_sta -t report_timing.tcl` after each
change; if a previously-non-critical path becomes critical,
the change touched a place that wasn't expected to interact.
Any single optimisation that worsens slack by >0.1 ns gets
reviewed for unintended interaction.

Also save the top 10 setup paths by entity before and after each change.
The pass/fail question is not only "did ALMs drop?", but "did the paths that
actually block 100 MHz move?"  A change that frees GPU ALMs but leaves the
worst paths in CPU/audio unchanged may still be useful for headroom, but it
should not be counted as timing closure.

### When to commit incrementally vs bundle

Each optimisation lands as a separate commit so the bisect path
is clean.  If two changes touch the same RTL file (e.g., A1 + A2
both edit triangle/persp), commit one at a time with `git diff`
checked between them.  Commit messages name the optimisation by
ID (`A1`, `A2`, etc.) and reference this doc.

---

## Open questions

1. **Does any shipping app emit non-affine triangles** (`v_w !=
   0x10000`)?  If no, A1 is free.  Need to grep apps + Quake
   port code.
2. **Does any shipping app set per-vertex `R`** ≠ a constant?  If
   no, A5 is free.  Same grep methodology.
3. **What's the actual division frequency** in firmware + apps?
   Determines if B2 (DivRadix drop) is acceptable.
4. **Does the timing-critical path live in any of these areas**?
   If A2 (NR drop) accidentally lives on the critical path, it
   helps both ALM and Fmax simultaneously — the ideal outcome.
   Need `report_timing -setup -npaths 5` post-change.
5. **What is the release-build ALM/Fmax baseline with `VARIANT_DEFS=""`?**
   This should be answered before any feature removal.
6. **Can VexiiRiscv legally generate RVF without FSQRT or a slower divider
   variant without breaking `rv32imafc` binaries?**  If not, B1/B2 need to
   move out of the practical sequence.
7. **Which depth modes and transluc reverse mode are used by real apps?**
   A9/A11 are small cuts, but only safe if the answer is "not used."
8. **Which product profile is being optimised first: retro FPS,
   general 3D SDK, or minimal 2D/I8?**  The right answer changes the ranking:
   `SPAN_PERSP`/colormap/transluc are core in the retro profile but optional
   in a general 3D or 2D profile.
9. **Are palookup, POT-mask wrapping, and `SPAN_COLUMN` contractual GPU
   capabilities or current-engine conveniences?**  If contractual, keep them
   out of the ALM-cut pool; if not, group them into a named profile.
10. **Should the public SDK keep advertising ignored general-purpose fields**
    (`RGB565`, wrap_s, wrap_t) while the hardware is I8-only?  This is mostly
    API clarity rather than ALM savings, but it affects what "general purpose"
    means to app authors.

---

## Where the timing-vs-ALM tradeoff sits

Important: at 93% ALM and failing 100 MHz by 0.484 ns, the
binding constraint is **timing closure**, not raw ALM count.
The Cyclone V router gets harder to satisfy as utilisation rises;
~5-10% headroom typically helps placement freedom enough to
recover ~0.2-0.4 ns of slack.  So freeing ~500-1,000 ALMs has a
**dual benefit**: enables Fmax closure work AND opens M10K /
ALM headroom for future fabric features (fast-clear, multi-row
burst, second tex cache, RGB blend Tier 2, etc.).

The recommendation is therefore **moderate restructure (~1,000
ALMs freed)** as the next discrete fabric pass — see "Goal: free
~1,000 ALMs" above.  After landing, re-run STA and decide whether
the critical path moved enough to close 100 MHz, or whether
further structural work on a specific signal is needed.

> **Review feedback:** Update this section after the baseline is reconciled.
> The current checked-in STA summary shows a larger miss than 0.484 ns, so the
> amount of ALM headroom needed to recover placement freedom may also be
> larger.  If the worst paths are not in `gpu_core`, the recommended "moderate
> GPU restructure" should be paired with the relevant CPU/audio timing fixes.
