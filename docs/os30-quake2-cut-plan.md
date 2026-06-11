# OS30 Quake2 GPU cut plan (rev 2 — post-adversarial-review)

Status: IMPLEMENTED 2026-06-10 (C1+C2+C3+C5 approved; C4 inverted — 0x4D KEPT and
wired into Quake2's world pass as 0x4A+0x4D).  Fit: 15,487/18,480 ALMs (84%),
299/308 M10K; worst path moved off the GPU (DRV_DA_SEL split) onto the known
VexiiRiscv chain; 30-seed sweep pending.  Acceptance: full 243/243,
lean `gpu-acceptance-os30` 134/134, scanout + scanout-lean PASS.
Produced 2026-06-10 by 5-mapper architecture analysis +
synthesis + 2 adversarial verifiers (full transcripts: workflow wf_ba2970c5-fc1).

Decisions already taken (Alberto, 2026-06-10):
- OS30 = Quake2-only. Quake1 stays an OS25 title (its 0x49/0x48 paths live there).
- All freed ALMs bank as 100 MHz timing margin. No translucency/mixer/tex-cache reinvestment.
- Deliverable: plan -> approval -> implement + gpu-acceptance + Quartus fit evidence.

## 0. Premise: what Quake2 actually emits (verified in ~/Repos/Quake2)

A literal "0x4A/0x4B-only" GPU **breaks Quake2 as-is**. The client
(`of_emit_q2.c:112-165`) hard-gates `OFQ2_CAP_VERT_TRI` on bit 20 AND bits 19/13/15.
Real emission paths:

| Q2 subsystem | Command | Notes |
|---|---|---|
| World (sky/opaque/water passes) | **0x49** param-tri, ATTR_PERSP_Q29 + dynamic shift | `R_DrawWorldTris` sw_edge.c:1636; world CANNOT move to 0x4B — it samples the pre-lit surface cache, 0x4B's per-vertex light can't express it |
| Alias models | **0x4A + 0x4B** | sw_polyset.c:213-272 |
| 2D HUD/console/turb rows + span fallbacks | **0x48 long-form record-style** (>=33 words) | OFQ2_StretchBlit/Blit, of_emit_q2.c:636+; NEVER the compact lane form |
| z clear, FB ops, present | 0x11 / 0x23 / 0x42 / 0x02 | |
| Never emits | compact 0x48 (11-32w), 0x4C, 0x4D, 0x20, ATTR_SOLID, GPU transluc | water/glass = CPU spanlets via vid_alphamap behind PrepareForCPU drains |

So "pure triangles" holds for all 3D (world+alias are triangles), but through
**0x49 + 0x4B**, not 0x4B alone. Tier 2 (cut 0x49/0x48/Q29, ~400-700 extra ALM) needs a
world+2D client rewrite and loses the surface-cache lighting model — **parked**.

## 1. Approved-pending cut list (Tier 1 — Q2 binary unchanged)

All cuts are **param-gated constant-folds** (signals stay declared; setters/decode gated;
Quartus sweeps the reader-less cone). Literal deletion breaks kept paths — see hazards.

| # | Cut | Est. ALM | Mechanism |
|---|---|---|---|
| C1 | 0x48 compact-direct lane machinery + 0x4C column list | 450-650 | New `GPU_HAS_COMPACT_SPAN` + `GPU_HAS_COLUMN_LIST` params (default 1), AND into `spanprod_compact_direct` (gpu_core.v:3842-3844) and 0x4C decode (:3851-3853); 11-32-word 0x48 payloads must DRAIN as no-ops |
| C2 | Analog raster prune (scanout) + periph analog-FB regs + ANALOGIZER_SETTINGS CDC fabric | 150-300 + 140-250 | New scanout `HAS_ANALOG_RASTER(0)` under `EXCLUDE_ANALOGIZER`; gate periph analog regs (axi_periph_slave.v:608-611,753-758,1262,1325-1335) and core_top CDC cone (:200-268); MMIO writes keep completing as discards. May free scanout line-buffer M10Ks (device is 308/308) |
| C3 | `GPU_EW_PARALLEL_DIVS` 1->0 (serial walker divider) | 120-250 | Flip constant in variant param block. Bit-identical pixels. Zero cost on 0x4B (hidden under ~123cy derive); ~62 cy/tri exposure on 0x49 world ONLY when GPU-bound — verify with `ofq2_stat_*` heartbeat, revertible alone |
| C4 | `GPU_HAS_PARAM_TRI_RECS` 1->0 (drop 0x4D) | 30-80 | Prune gates already exist; set param 0 + periph `HAS_PARAM_TRI_RECS(0)`. **OPEN DECISION — see §3** |
| C5 | Drop `sram_controller` under `EXCLUDE_TRANSLUC` | 45 | Instance-vs-tie under ifdef. **Pin parking is mandatory**: drive `sram_we_n/oe_n/ub_n/lb_n = 1'b1`, `sram_dq_oe = 0` explicitly — undriven pins get grounded and hold the chip in continuous write (hazard B4) |

Gross total: **~935-1,575 ALM** (+ possible M10K from C2 line buffers + 4 from mixer),
all to timing margin. Only failing domain today: 100 MHz `mp_ram general[0]`,
OS25 best-of-30 WNS -0.526 at 93% ALM. No OS30 fit artifact exists yet — baseline first.

**Dropped: C6** (cont2 tie-off deepening, ~10-30 ALM) — gates second-controller input,
violates the no-gating-without-permission rule for noise-level savings.

### Hazard fixes baked into C1 (from adversarial review)

- **B1**: `spanprod_select_current_record` (:1577-1592) and parametric-arm writers of cut
  regs (:1469-1472, :1413) are SHARED sites — gate their compact writers, never delete.
  Also remove the `sp_fastpath` tail-safe bounce (:4814-4821) manually (won't fold).
- **B2**: `GPU_HAS_COLUMN_LIST=1 && GPU_HAS_COMPACT_SPAN=0` is a broken combo (0x4C
  delegates to compact arms via `column_compact_idx_remap`). Derive: column list requires
  compact. Add elaboration check or derive one param from the other.
- **B5 + sticky-state contract**: a drained compact 0x48 must STILL clear
  `tri_state_valid` — move the clear to fire on raw opcode match (0x48/0x49),
  independent of payload-size decode. Negative test: compact-0x48 -> 0x4B sequence.
- **Caps bit 23 `OF_HW_GPU_SPAN_GROUP` is mandatory** (set OS25/MiSTer, clear OS30):
  `of_gpu_draw_affine_span_group` has NO self-gate today (of_gpu.h:991+) — add
  `of_has_feature` guard mirroring `of_gpu_set_tri_state` (:1402). Fix the 0x4C doc
  fallback chain (of_gpu.h:1053-1055) to long-form. NOTE: `of_gpu_draw_persp_span_group`
  is LONG-FORM (header writer of_gpu.h:1188-1225) — it survives, needs no gating, and
  must stay byte-exact.
- Update stale comments: axi_periph_slave.v:684-688 ("UNCONDITIONAL"), of_caps.h bit 21/22
  text, HW_FEATURES block.

## 2. Variant mechanics

Fold into **OS30 itself** (it is Quake2-only by decision; no third variant):
extend `VARIANT=os30` defs (pocket/Makefile:161-165) with `OS30_LEAN` selecting the new
param block in core_top.v (split the `OS30_VERT_TRI` ifdef three-way) + C5/C2 instance drops.
MiSTer keeps the full configuration; full-config acceptance stays in CI.

## 3. OPEN DECISION — 0x4D: cut it, or finally use it?

The completeness critic raised the strongest counter-proposal of the review (anti-C4):

- Q2's world re-sends a **37-word 0x49 per triangle** (header mostly constant per surface).
- 0x4D was built for exactly this: 0x4A once per surface + **16-word** 0x4D per triangle
  = ~55% world command-bandwidth cut + fewer CPU stores per tri. The world cannot use
  0x4B (surface-cache lighting), so 0x4D is NOT redundant on this variant.
- Cost of keeping: forgo 30-80 ALM; write the missing SDK emitter (trivial — 0x49 emitter
  minus 21 words); wire it in of_emit_q2's world pass.

Options: (a) cut 0x4D per C4; (b) keep 0x4D, write emitter, world goes 0x4A+0x4D.
Recommendation: **(b)** — on a Quake2-optimized variant, a measured world-pass win beats
30-80 ALM of margin; revert to (a) if heartbeat counters show no gain.

## 4. Verification

A. Per-cut map deltas: `make check VARIANT=os30` baseline FIRST (none exists), then apply
   cuts one at a time (C4/C3/C5/C1/C2), `make check` after each — a ~0 delta means a
   failed prune (look for live `spanprod_direct_*` regs, not dstate FSM extraction).
B. Acceptance: new `gpu-acceptance-os30q2` config
   (`-GGPU_HAS_COMPACT_SPAN=0 -GGPU_HAS_COLUMN_LIST=0 -GGPU_HAS_PARAM_TRI_RECS=0
   -GGPU_EW_PARALLEL_DIVS=0 +define+EXCLUDE_TRANSLUC`). **tb_gpu.v must declare+forward
   the two new params first** (today it forwards only 4). C++ gates per group
   (`GPU_TEST_ENABLE_SPAN_GROUPS` etc.). NEW negative tests: 11-32w 0x48, 0x4C, 0x4D all
   drain (sentinel borders intact) + the compact-0x48->0x4B sticky-state test.
   Byte-exact required in cut config: all 0x49 (7 fns), Q29 (14), z-modes, vert-tri,
   0x48 long-form incl. persp span groups (11 fns), palookup, fence/flip/clear, combos.
   Full-config `gpu-acceptance` (236) stays green in the same run.
   KNOWN HOLE: base `gpu`/`gpu-persp`/`gpu-floor`/`gpu-gpudemo` suites are built on the
   compact 11-word payload — they run in full config only (accepted, documented), or port
   `begin_test_span_payload` to long-form later.
C. Scanout: add a `-GHAS_ANALOG_RASTER=0` tb_scanout build with analog assertions gated
   to LCD-only, incl. the 1-line-deadline test. Default `make scanout` stays.
D. Quartus: full `make build VARIANT=os30` + `tools/sweep.sh`. Success: fitter completes,
   ALM <= ~95%, WNS(`mp_ram general[0]`) >= -0.526, ideally >= 0. Per-entity sanity from
   fit.rpt (sram_controller gone; walker ~709 — the OS25 number is ALREADY serial-divider,
   do not expect "<709"; gpu_core self will EXCEED OS25's 5,720 because 0x4B stays).
E. Hardware soak: cold+warm boot quiesce; Q2 timedemo — HUD (record-0x48), world+sky+
   dynamic lights (0x49/Q29), alias (0x4B), water (CPU spanlets, unchanged), particles;
   `ofq2_stat_sync_us/lists/records/verttri/2d_us` vs current OS30 baseline (bounds C3).
   Quake1 on OS30 must fail SAFE (drained no-ops, no hang); sysreg-38 caps dump:
   bit 20 set; 21/22/23 clear; 13-19 = 0x000F_E000; bit 1 clear.

## 5. Future options (explicitly parked)

- Tier 2 client rewrite (0x49/0x48/Q29 removal, ~400-700 ALM).
- SRAM-as-z-buffer: re-target the freed private SRAM port at the z-buffer — the only
  cheap BANDWIDTH reinvestment on an SDRAM-bound variant (bigger RTL change).
- 2x command ring in freed mixer M10Ks (zero ALM, halves doorbell round-trips).
- Tex-cache split-replica (tex port A / cmap port B private 16KB) — best perf reinvestment
  if margin lands healthy; subtle module, byte-exact + thrash tests required.
