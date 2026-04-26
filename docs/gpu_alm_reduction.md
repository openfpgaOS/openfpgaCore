# GPU ALM-reduction catalogue

Status snapshot (Pocket bitstream, post commit `f5bc8cd`, seed 13,
2026-04-25):

- **ALMs: 17,169 / 18,480 (93%)** — 1,311 free.
- M10K: 220 / 308 (71%).
- DSP: 33 / 66 (50%).
- Best Fmax (Slow 1100mV 85C, mp_ram 100 MHz domain): **95.38 MHz**
  (slack −0.484 ns; design currently fails 100 MHz closure).

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

#### A2. Skip Newton-Raphson in PSS

- **Savings:** ~250-400 ALMs + 1 DSP slot.
- **Risk:** Med.  Reduces persp-recip precision from ~16 bits to
  ~10 bits (LUT only).  The two pre-existing characterisation
  failures (`zi_step=500` / `=1000`) get worse; real-game persp
  spans on 8-pixel segments still look fine.
- **Verification:** tb_gpu — expect the two persp-extreme tests to
  fail by larger margins (already failing baseline).  Quake/Duke
  end-to-end rendering visually unchanged.  Quartus rerun.
- **Dependency:** Quake walls + Duke3D `SPAN_PERSP` paths.
  Verified-acceptable per the cmap-cache spike methodology — the
  recip-LUT alone gives sub-pixel error on 8-pixel segments.
- **How:** Remove `PSS_NR_MUL_X`, `PSS_NR_MUL_X_W`, and the second
  DSP feeder for the Newton-Raphson refinement.  PSS_FINAL captures
  the LUT-output recip directly; PSS state count drops from 19 to
  ~15.

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

#### B2. Drop `DivRadix` plugin from VexiiRiscv (use iterative `div`)

- **Savings:** ~145 ALMs.
- **Risk:** Med.  Integer division becomes ~32× slower (iterative
  vs radix-2).  Apps doing many divisions take a perf hit.
- **Verification:** Build firmware + tb_system; measure boot time.
  If "negligible" (boot is dominated by other paths), accept.
- **Dependency:** Anywhere `div` / `rem` shows up in hot loops.
  BUILD's Q-format math may use it.
- **How:** Same as B1 — `generate_vexii.sh` plugin list.

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

#### B4. Halve audio_mixer voice count (32 → 16)

- **Savings:** ~300-350 ALMs.
- **Risk:** Med.  Dense MIDI tracks may starve voices.
- **Verification:** moddemo run with worst-case track; check no
  audible voice-stealing.
- **Dependency:** Audio quality tradeoff.
- **How:** Change voice-count parameter in `audio_mixer.v`; voice
  state RAM (vtbl_*) shrinks proportionally.

### C. Structural moves (bigger savings, bigger risk)

#### C1. Remove triangle path entirely from Pocket bitstream

- **Savings:** ~1,500-2,000 ALMs + 4 DSPs.
- **Risk:** High.  Kills any app that uses `CMD_DRAW_TRIANGLES`.
  GPU demo's mode 3 (32-tri fan) breaks; Quake renderer (when it
  ships) breaks.
- **Verification:** Need to know which shipping apps use triangles.
  Currently: gpudemo only.
- **Dependency:** Future Quake port relies on this; would need a
  variant bitstream.
- **How:** `ifdef GPU_FEAT_TRIANGLE` is already in the RTL — define
  it as off in `gpu_config.vh` for a "span-only Pocket variant".
  All `S_TRI_*` states + vertex regs + gradient compute disappear.

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

---

## Recommended sequences

### Goal: free ~500 ALMs (light cleanup)

1. **A1** — drop triangle persp premul (~200 ALMs).
2. **A3** — audit MMIO read mux (~100 ALMs).
3. **B1 + B3** — drop FpuSqrt + cont2 synchronizers (~115 ALMs).
4. **A5** — drop Gouraud R-walk if confirmed unused (~100 ALMs).

**Total ~515 ALMs**, low-risk, all verifiable in tb_gpu without
hardware test.  Pocket bitstream lands at ~16,650 / 18,480 (~90%).

### Goal: free ~1,000 ALMs (moderate restructure)

The above (~515 ALMs) plus:

5. **A2** — skip Newton-Raphson in PSS (~300 ALMs + 1 DSP).
6. **B2** — drop DivRadix (~145 ALMs).
7. **A4** — fold S_CLEAR_RECT into S_FB_FLUSH (~80 ALMs).

**Total ~1,040 ALMs**, medium-risk.  Bitstream at ~16,130 / 18,480
(~87%).  More headroom for Fmax closure work + future fabric
features.

### Goal: free ~1,500 ALMs (aggressive)

The above (~1,040) plus:

8. **B4** — halve mixer voices (~300 ALMs).
9. **A6** — halve tex_cache (~100 ALMs + frees 11 M10K).

**Total ~1,440 ALMs**, audio-quality and cmap-cache hit-rate
tradeoffs documented.  Bitstream at ~15,730 / 18,480 (~85%).

### Goal: free 2,000+ ALMs (variant-bitstream territory)

Triangle removal (C1) is the only single change that hits this
class.  Requires a separate `pocket_span` variant since it loses
the triangle datapath.  Probably not worth the bitstream-variant
overhead until a span-only target (e.g. an early-Quake span-only
build) is actually shipping.

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

1. Run tb_gpu / tb_floor / tb_gpudemo / tb_system (whichever apply).
2. Run Quartus on a single seed for a fast snapshot.
3. Compare `ap_core.fit.summary` ALM line against the pre-change
   commit's snapshot.
4. If ALM delta is within ±5% of estimate, the change behaves as
   modelled.  If >10% off, dig into `ap_core.fit.rpt`'s
   "Resource Utilization by Entity" section to find what changed.

### Measuring Fmax effect

The 100 MHz mp_ram domain is the binding constraint.  Pull the
worst path with `quartus_sta -t report_timing.tcl` after each
change; if a previously-non-critical path becomes critical,
the change touched a place that wasn't expected to interact.
Any single optimisation that worsens slack by >0.1 ns gets
reviewed for unintended interaction.

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
