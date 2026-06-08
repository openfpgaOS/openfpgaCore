<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: (c) 2026, ThinkElastic <Think@Elastic.com>
-->

# GPU utilization plan (rev 2 — 2026-06-07, post edge-walker)

Status of the landscape this plan assumes:

- **Landed**: `gpu_edge_walker.v` + `CMD_DRAW_PARAM_TRI` (0x49, 36 words)
  wired through `S_TRI_FILL` into the spanprod chunk path; suite green at
  203/203 incl. `param_tri_shared_edge_adjacency`.
- **Landed**: of_gpu.h emission core (claim/commit raw stores, lazy kick
  with fence-forced publish, desc-poll elision) — ~2–6 ms/frame CPU
  returned, apps pick it up on rebuild.
- **Measured (Quake host_speeds, hardware)**: `alias` = 23.4 ms/frame CPU
  in fights, GPU idle behind it; ~14 ms is the client-side 0x49 lowering
  (plane solve ~1 fdiv + ~40 FP ops, overflow/Q29 escalation handling,
  37-word header) ≈ 180 cy/triangle at ~8K tris/frame.

## Centerpiece: CMD_SET_TRI_STATE (0x4A) + CMD_DRAW_VERT_TRI (0x4B)

Target ≤60 cy/triangle CPU. The GPU derives the four attribute planes
from raw per-vertex data; the client stops solving 2×2 systems, stops
rebasing to bbox corners, and stops escalating slivers to Q29 — the
internal anchoring rule makes both structurally unnecessary.

### Wire contract

**0x4A CMD_SET_TRI_STATE** (sticky; cleared by soft_reset; ~14 words):
fb_base/major/minor, tex_addr/width/w_mask/h_mask, control word
(flags/colormap_id/z_mode — attr_mode implied PERSP), clamp_min/max[0..1],
z_base/major/minor, clip rect (2 packed words). Decode reuses
`load_param_span_list_payload_word` field-for-field where formats match;
state lands in the same `spanprod_*` staging regs 0x49 fills, latched
into a sticky shadow bank so successive 0x4B draws re-arm them.

**0x4B CMD_DRAW_VERT_TRI** (~14 words/triangle):
- w0–w2: packed vertices `{y int [31:16], x Q12.4 [15:0]}` — same format
  and fill convention as 0x49 (ceil both edges, left-closed right-open)
- w3–w5: s0,s1,s2 (Q16.16 texels, raw — NOT pre-multiplied by zi)
- w6–w8: t0,t1,t2 (Q16.16)
- w9–w11: zi0,zi1,zi2 (Q16.16, the scale the z window consumes)
- w12: packed per-vertex light rows `{l2[17:12], l1[11:6], l0[5:0]}` (Q6 rows)
- w13: reserved/0 (alignment + future per-tri override bits)

**Caps**: `OF_HW_GPU_VERT_TRI = (1u << 20)` in of_caps.h; HW_FEATURES bit
in axi_periph_slave (per-feature parameter, default 1 on both targets).

### Derivation datapath (the design)

Anchor = sorted-top vertex v0 after the walker's y-sort (use the SAME
sorted order — sort is 3 compare-swaps on {x,y,attr-index} triples or an
index permutation register so attrs follow their vertices).

1. **Numerator products** (6 mults): szi_k = s_k·zi_k, tzi_k = t_k·zi_k,
   k∈{0,1,2}, Q16.16×Q16.16 → take [47:16] with truncation toward −∞
   (must match the acceptance reference bit-for-bit). Runs on the PSS
   dsp/dsp2 pair (idle during walker setup).
2. **Determinant**: det = d1x·d2y − d2x·d1y with d*x Q12.4, d*y int →
   det is Q12.4-scaled, |det| < 2^29. Runs on the walker's shared 27×16
   DSP between its cross-product and prestep uses (it is idle during the
   3 serial edge divides — ~90 cycles of window).
3. **Reciprocal**: rdet = 2^N/det via a dedicated 32-beat serial divide
   (clone of the walker's restoring divider, ~40 FFs; do NOT arbitrate
   the PSS recip LUT — cross-engine arbitration costs more than the
   divider). N chosen so that worst-case |du|·bbox keeps Q16.16 attr
   precision: with bbox ≤ 1024 px and attrs ≤ Q16.16, N = 44 gives
   du/dv in Q16.28 intermediates → ≥12 guard bits over the walk range.
   Document final N in the module header after the reference is built —
   THE REFERENCE AND RTL MUST SHARE THE EXACT WIDTHS.
4. **Plane terms** (8 mults + 8 mults): for each attr a∈{szi,tzi,zi,light}:
   du_a = (da1·d2y − da2·d1y)·rdet, dv_a = (da2·d1x − da1·d2x)·rdet
   (numerators on dsp/dsp2 while rdet divides; the ·rdet products after).
   Origin_a = a0 (anchored — no large-offset products, hence no overflow
   and no Q29: |da| ≤ 2^17 in Q16.16 across any on-screen triangle).
   Light keeps 6-bit rows internally as Q6.16 plane — the 24-bit decode
   limit applies to the RECORD evaluation, which the anchored origin
   keeps in range by construction.
5. **Handoff**: results land in the existing spanprod plane regs
   (attr_origin/du/dv[0..2], light plane) exactly as if word 8–19 of an
   0x49 header had carried them; from there the path is untouched.

**Schedule**: steps 1–4 ≈ 6+2+34+16 ≈ 58 cycles, fully overlapped with
the walker's ~95-cycle setup (3 edge divides) which starts immediately
from the packed vertices. Per-triangle GPU cost target: no slower than
0x49 today. Budget guard: ~+350–500 ALMs (divider + sequencing FSM +
sticky bank); Pocket has 2,343 free — fine, but verify fit + 100 MHz on
both targets before declaring.

### Acceptance plan

- RTL-mirroring C reference for the derivation (same truncations, same
  N) added to tb_gpu_acceptance_main.cpp.
- Equivalence: vert_tri vs param_tri with CPU-solved planes over
  representative triangles (byte-exact when the reference solves with
  the same fixed-point path; tolerance-free).
- Sliver/steep-gradient set that used to require Q29 escalation — must
  render, in-range, no dropped spans.
- `param_tri_shared_edge_adjacency` re-run through 0x4B (shared edge,
  one triangle per command).
- Sticky-state semantics: 0x4A once + N×0x4B; soft_reset clears; 0x49
  unchanged as fallback (old SDKs keep working).

### SDK

`of_gpu_set_tri_state()` + `of_gpu_draw_vert_tri()` claim/commit
emitters (validators mirrored from 0x49), of_caps bit, `make sdk` sync.
Quake client: emit raw verts, drop the plane solver + Q29 escalation
behind `of_has_feature(OF_HW_GPU_VERT_TRI)`.

### Stretch (only if timing/area allow)

Three parallel slope dividers in gpu_edge_walker.v: setup ~110 → ~45 cy.
Bit-identical quotients required (same restoring algorithm, just three
instances); suite proves it. Only worth it if the walker — not the
spanprod/fragment path — is the limiter after 0x4B lands; measure first
via the walker-busy vs spanprod-stall ratio (add 2 debug counters).

## Staging-dedup pass (2026-06-07 — landed)

The 0x4A+0x4B feature was over the Pocket ALM budget (18,561 est. vs 18,480
capacity). Root cause was the feature's STAGING duplication, not its
arithmetic. Two structural dedups removed it, both bit-exact (suite 215/215):

1. **Killed the `tri_state_*` sticky shadow bank** (~16×32 FFs + its decode +
   the per-draw EXECUTE copy). 0x4A's payload words now decode DIRECTLY into
   the SHARED `spanprod_*` staging regs (reusing the `load_param_span_list_payload_word`
   0x49-header arms — idx 0–4 for fb/tex, idx 7 for control, idx 20–23 for
   clamps, idx 26–28 for z; w5's packed `{h_mask,w_mask}` is unpacked inline).
   Only the clip rect (4×16b) and `tri_state_valid` persist (the walker
   consumes clip per draw; 0x49 carries its own clip in w31–32 so there is no
   staging overlap).

   **CONTRACT CHANGE**: the sticky surface/control/clamp/z state now lives in
   the shared param staging, so a subsequent **0x48 DRAW_PARAM_SPAN_LIST or
   0x49 DRAW_PARAM_TRI header OVERWRITES it**. After interleaving an 0x48/0x49
   between an 0x4A and a later 0x4B, the client MUST re-issue 0x4A before more
   0x4B draws. To make a stale-state 0x4B a *guarded no-op* (not garbage),
   `tri_state_valid` is now CLEARED whenever an 0x48/0x49 header decodes (in
   addition to soft_reset). Per-0x4B attribute-plane overwrites by the
   derivation are fine and expected — each 0x4B rederives all four planes
   (szi/tzi/zi/light) from scratch, so back-to-back 0x4B after a single 0x4A
   still work; only the surface/control/clamp/z fields are sticky, and the
   derivation never touches those. Wire formats are UNCHANGED (0x4A still 16
   words, 0x4B still 14); only internal storage moved. No SDK/test change was
   needed (the existing `vert_tri_sticky_semantics` test does not interleave
   0x48/0x49 between 0x4A and its 0x4B draws).

2. **Folded the szi/tzi products into payload arrival** (removed 6×32 FFs of
   `vt_s`/`vt_t`). 0x4B words arrive one per cycle; the DSPs are idle during
   S_PAY_DATA. Raw s_k/t_k (w3–8) park directly in the `dv_szi`/`dv_tzi`
   product slots; when zi_k arrives (w9–11) the DSPs launch s_k·zi_k / t_k·zi_k,
   captured (1-cycle DSP latency → captures trail launches by two words: launch
   k=0/1/2 at w9/w10/w11, capture at w11/w12/w13) back into the SAME slots. The
   derivation FSM then skips its old DRV_NUM_PROD/W/CAP product loop
   (DRV_SORT_C falls straight through to DRV_DELTA), making it ~9 cycles
   shorter (~127 → ~118 cy; still fully hidden under the walker's ~95-cy
   setup). The new DSP usage window (S_PAY_DATA w9–w13) is disjoint from both
   S_TRI_DERIVE and PSS/S_FRAG_PIPE, so no arbitration is needed — documented
   in the derivation DSP-ownership comment.

Net storage removed: ~22×32 FFs (16-field sticky bank + vt_s/vt_t) plus the
EXECUTE copy path and 3 derivation states.

## Still-valid items from rev 1

1. **Doom/Duke3D → param ATTR_AFFINE migration** (7 → 1.5 words/span;
   Quake2 pattern at of_emit_q2.c:526). Unchanged, still the biggest
   non-Quake CPU item.
2. **QuakeSpasm stale-header bug** — triangle call against removed
   opcode renders nothing. With 0x49 live, the right fix is re-syncing
   the header and pointing it at CMD_DRAW_PARAM_TRI (or 0x4B when it
   lands).
3. **B1 inter-record overlap** (shadow sp_* bank, ~960 FFs, med-high
   risk) — MORE valuable now: triangle records are short spans, so the
   ~11–13-cycle per-record bubble weighs heavier. Reassess after 0x4B:
   if GPU-busy becomes the limiter in fights, this is the next RTL item.

## Rejected (unchanged — see rev-1 notes/gpu_core.v comments)

Const-Z NR-skip (precision-load-bearing), fbwq coalescer/depth changes
(arbiter merges only within AW), param header delta-encoding, doorbell
auto-refill.

## Per-target gating: vert-tri ships on MiSTer, gated out on Pocket (2026-06-07)

The 0x4A/0x4B vertex-triangle path (decode + `S_TRI_DERIVE` derivation FSM)
costs ≈2.0k ALMs. On the Pocket device (Cyclone V) the design sits at >99%
density with only ~2.3k ALMs of headroom, so carrying it there is untenable;
on MiSTer it stays. It is now a per-target synthesis feature gate:

- `gpu_core` parameter `GPU_HAS_VERT_TRI` (default 1). When 0, the
  `cmd_is_set_tri_state`/`cmd_is_draw_vert_tri` decode terms are constant 0, so
  both opcodes take the unrecognised-command payload-drain no-op path and every
  writer of the vert-tri staging + derivation regs (and the `S_TRI_DERIVE`
  state, reachable only from the gated 0x4B EXECUTE arm) goes constant-inactive
  → Quartus sweeps the derivation logic.
- `axi_periph_slave` parameter `HAS_VERT_TRI` (default 1) feeds HW_FEATURES
  bit 20 (`32'h0010_0000`) so apps gate via `of_has_feature` and don't submit
  0x4A/0x4B to a core that drains them. Must track `GPU_HAS_VERT_TRI` per target.
- Pocket (`targets/pocket/core_top.v`) sets `.GPU_HAS_VERT_TRI(0)` on the GPU
  and `.HAS_VERT_TRI(0)` on the periph slave. MiSTer keeps both defaults (1).

Prune proof: `make -C src/fpga/targets/pocket check` → "Estimate of Logic"
back near the pre-feature baseline (~16,476) instead of the ~18.4k with the
feature in. **Re-enable** when the budget allows: flip `GPU_HAS_VERT_TRI` and
`HAS_VERT_TRI` back to 1 on Pocket.
