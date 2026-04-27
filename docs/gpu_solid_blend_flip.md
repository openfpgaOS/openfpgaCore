# GPU Specification: SOLID, BLEND, and Autonomous Frame Flip

Author: Quake port team (`/home/alberto/Repos/Quake/src/quake/`)
Target: `gpu_core.v`, SDK `of_gpu.h`, kernel video flip path.

> **2026-04-25 update.** This doc is being refreshed against the
> `transluc[]` blend unit landed in `gpu_core.v` (`transluc.md` stages
> 1–4). The original draft was written *before* that fabric work and
> double-spent the BLEND ALMs, M10K, AXI arbiter, and free flag bits.
> The doc now reflects the merged state: BLEND infrastructure already
> exists, only SOLID and `CMD_FLIP_FB` are net-new gateware. Sections
> tagged with **(landed)** describe hardware that is already in tree;
> sections tagged with **(new)** describe what still has to be built.

---

## 1. Goal

Move the **entire** per-frame rendering work to the GPU so the CPU never touches the framebuffer, and have the GPU autonomously trigger the framebuffer flip when a frame is done. Concrete benefits:

1. **No CPU↔GPU framebuffer cache concerns.** The framebuffer can live in cached SDRAM with the GPU as the sole writer. No more `of_uncached(vid.buffer)` aliasing. This is the principle captured by the project memory **"GPU owns the framebuffer — CPU never writes"** (`project_gpu_owns_framebuffer.md`); SOLID + BLEND + `CMD_FLIP_FB` are the three primitives that close the gaps preventing it from holding today.
2. **No CPU↔GPU ordering bugs in the 2D path.** Today the engine mixes CPU-direct pixel writes (status bar, console, fade) with GPU draws and only stays correct because the layouts happen not to overlap. Once all 2D is GPU, ordering is determined by ring-order alone.
3. **No frame-end CPU stall.** Today `VID_Update` calls `of_emit_finish()` then `of_video_flip()`. With autonomous flip, the CPU pushes a `CMD_FLIP_FB` at the end of the frame's command stream and immediately starts preparing the next frame.
4. **Simpler engine.** A single ring of commands per frame, terminated by `FLIP_FB`. CPU = command producer; GPU = sole executor.

This requires three gateware additions:

- **SOLID (new)** — single-color fill primitive (no texture fetch). Unblocks `Draw_Fill`, particle path, color-keyed solid sprites.
- **BLEND (already landed as `transluc[]`)** — read-modify-write framebuffer pixels through a paletted translucency LUT. The Quake `Draw_FadeScreen` use case is satisfied by the same fabric BUILD/Duke3D translucency uses; only an SDK helper that loads a unary-remap table into the existing 32 KB / 128×256 LUT is missing.
- **`CMD_FLIP_FB` (new)** — terminates a frame. GPU executes all preceding commands, then queues the displayed framebuffer to swap on the next vsync.

Estimated **net-new** cost: **~140 ALMs, 0 DSP, 0 M10K** (SOLID + `CMD_FLIP_FB`). The BLEND fabric (~250 ALMs, 32 M10K) is already paid.

---

## 2. Today's pipeline (baseline)

For context — what's already implemented in the latest bitstream (per `docs/gpu_architecture.md` §1.1 + the `transluc.md` stage-1..4 commits):

- Span path with `OF_GPU_SPAN_COLORMAP / COLUMN / SKIP_ZERO / DEPTH_TEST / DEPTH_WRITE / PERSP / TRANSLUC / TRANSLUC_REV` — **all 8 flag bits are now in use**.
- Triangle path with per-vertex `Z`/`S`/`T`/`W` (perspective via `W ≠ 0x10000`) and per-vertex `R` (Gouraud light into colormap row).
- Sticky state: framebuffer addr, z-buffer addr, texture addr/width, depth func.
- Ring: 16 KB BRAM, CPU writes via `GPU_RING_DATA`, GPU consumes after `GPU_RING_WRPTR` is published.
- Fence / wait: `CMD_FENCE` writes to `GPU_FENCE_REACHED`; CPU polls.
- **`transluc[]` LUT** (32 KB / 128×256, 32 M10K) on-chip. Upload through the shared `GPU_CMAP_ADDR/DATA` port with bit 31 of `GPU_CMAP_ADDR` selecting the target (0 = colormap, 1 = transluc). See `of_gpu_translucency_upload()`.
- **2-client M0 read arbiter** (texture cache + BLEND framebuffer-readback).
- **`fb_acc` RMW + same-word lane-merge bypass** for in-flight overdraw.
- **BLEND state machine** in `FBSS` (`BLEND_REQ → BLEND_AR_WAIT → BLEND_R_WAIT → BLEND_LUT_WAIT → BLEND_APPLY`).

Free opcode space (verified against `of_gpu.h` HEAD):

- `0x03 .. 0x0F` — utility / control commands (`FENCE` is `0x02`).
- `0x22, 0x25, 0x26, 0x27, 0x28 .. 0x2F` — `SET_*` state commands. `0x22 SET_BLEND`, `0x25 SET_SHADE`, `0x26 SET_ALPHA_REF` are reserved-for-historical-reasons (see header) and **must not be reused**; `0x27` and `0x28..0x2F` are free.
- `0x32 .. 0x3F` — triangle-related (`DRAW_TRIANGLES` is `0x30`; `0x31 DRAW_INDEXED` reserved).
- `0x43 .. 0x4F` — span-related (`DRAW_SPAN` is `0x40`; `0x41 DRAW_SPANS`, `0x42 DRAW_SPRITE` reserved).

**Free `flags` bits in `of_gpu_span_t.flags`: zero — all 8 bits are taken.** SOLID therefore cannot land as a new flag bit; it has to use a dedicated opcode (or sticky state). See §3.2.

MMIO map: register decoder is one slot (`0x3C`) away from full. Any new MMIO must reuse an existing register.

---

## 3. SOLID — constant-color fill (new)

### 3.1 Behaviour

When a span (or triangle) has SOLID semantics, the fragment processor does **not** issue a texture fetch. Instead it writes the per-span `solid_color` byte for every pixel that passes depth test and the SKIP_ZERO check. Other fragment-pipeline stages (depth test, framebuffer accumulator, AXI burst writer) are unchanged.

Interaction with other flags:

- `SOLID + DEPTH_TEST` — depth-test the span, write `solid_color` only on pass. Required for particles.
- `SOLID + DEPTH_WRITE` — also write the depth value (reuse existing per-span `zi`/`zistep`).
- `SOLID + SKIP_ZERO` — discard pixels where `solid_color == 0xFF`. Edge case: lets a single solid command produce nothing if `solid_color` is the skip key. Engine should never emit this combination; gateware should not special-case it.
- `SOLID + COLORMAP` — apply colormap row to `solid_color` once (in setup, not per pixel). Useful for "solid fill modulated by sticky light". Engine doesn't need this initially; gateware can ignore the COLORMAP flag when SOLID is set.
- `SOLID + PERSP` — **explicitly illegal**. The fragment processor's PSS sub-FSM expects a textured source; SOLID short-circuits texture fetch. If both are asserted, gateware should drop PERSP silently and emit an affine-fill span. Engine must not set both.
- `SOLID + TRANSLUC` — `solid_color` is the source byte; blended into FB through the existing 32 KB `transluc[]` LUT (see §4). This is exactly what `Draw_FadeScreen` needs.
- `SOLID + TRANSLUC_REV` — same, with the LUT key bytes swapped (high/low). Useful for variants where the destination is the "source" axis of the fade table.

### 3.2 Encoding

Because all 8 span-flag bits are taken (TRANSLUC at bit 6, TRANSLUC_REV at bit 7), SOLID **cannot** be a new flag bit. The cleanest encoding is a dedicated opcode:

```
CMD_DRAW_SOLID_SPAN (opcode 0x44)
  Word 0: [31:16] count, [15:8] light, [7:0] flags_subset
          flags_subset bits: SKIP_ZERO(2), DEPTH_TEST(3), DEPTH_WRITE(4),
                             TRANSLUC(6), TRANSLUC_REV(7)
                             — COLORMAP(0), COLUMN(1), PERSP(5) ignored
  Word 1: [31:0] fb_addr
  Word 2: [31:16] fb_stride, [15:8] reserved, [7:0] solid_color
  Word 3: [31:0] z_addr      (only consumed if DEPTH_TEST | DEPTH_WRITE)
  Word 4: [31:0] zi
  Word 5: [31:0] zistep

  Total payload = 5 words.
```

5 words vs the 18-word `DRAW_SPAN` payload — a SOLID span is ~3.6× cheaper to enqueue, which matters for `Sbar_Draw` where dozens of one-color rects fly per frame.

**Triangle SOLID** uses a sticky-state pattern (cheaper than per-vertex color in the count word, since SOLID triangles are rare):

```
CMD_SET_SOLID_COLOR (opcode 0x27)
  Word 0: [31:8] reserved, [7:0] solid_color
```

Sets `st_solid_color` in the gateware. A new bit in the existing `DRAW_TRIANGLES` count word selects:

```
DRAW_TRIANGLES word 0 (count word):
  [31:1] num_vertices  (× 3 vertices per triangle, batched)
  [0]    tri_solid     (1 = use st_solid_color, ignore textures)
```

Bit 0 is currently unused (`num_vertices` is always a multiple of 3, so the LSB of the count word is unused as a count bit).

### 3.3 Fragment-processor implementation note

`gpu.md` Tier 2 SOLID design (§3.2) calls out the issue-handshake gotcha:

> Today `issue_committed = tex_req_valid && tex_req_ready`; suppressing the request would also suppress forward progress. Instead, split the issue handshake into `tex_issue_committed` and `solid_issue_committed`, advance p0/p1 on either, and inject the solid color into the p2 stage without waiting for `tex_resp_valid`.

Spec adopts that approach. Note that the BLEND landing already touched the same area: the M0 arbiter has a `tex_m0_in_flight` tracker that gates BLEND's AR. SOLID must declare itself outside both lanes — the bypass injects directly into `p2_color`, never seeing the arbiter.

### 3.4 Cost estimate

- 1 register (`sp_solid_color`, 8 bits).
- 1 register (`st_solid_color`, 8 bits) for the triangle path.
- New opcode 0x44 decode in the command FSM: ~25 ALMs.
- 2:1 mux at `p2_color` selecting between `tex_resp_data` and the solid color byte: ~12 ALMs.
- Issue-handshake split (`solid_issue_committed`): ~30 ALMs.
- `tri_solid` decode in `DRAW_TRIANGLES`: ~5 ALMs.

**Total: ~80 ALMs, 0 DSP, 0 M10K.** Up from the original 50 ALM estimate because the dedicated opcode adds command-FSM real estate that the flag-bit version avoided.

### 3.5 Test cases (extend `tb_gpu`)

- `tb_gpu_solid_span` — SOLID span of count=320 with color=0x37, verify all 320 FB bytes equal 0x37.
- `tb_gpu_solid_depth_test` — SOLID + DEPTH_TEST, half pixels pass, half fail. Verify only the passing half writes.
- `tb_gpu_solid_no_tex_traffic` — SOLID span, monitor `m_rd_arvalid`. Texture cache must not issue any AR. Catches the issue-handshake bug.
- `tb_gpu_solid_triangle` — `SET_SOLID_COLOR` + `DRAW_TRIANGLES` with `tri_solid=1`, verify constant fill.
- `tb_gpu_solid_transluc` — SOLID + TRANSLUC, full-screen span over a known FB gradient with a unary fade table loaded into all 128 LUT rows. Verify each output byte equals `transluc[(solid_color << 7) | fb]` (LSB of source dropped per the 128×256 quant).
- `tb_gpu_solid_skip_zero_color_ff` — pathological SOLID + SKIP_ZERO with `solid_color = 0xFF`. Verify zero pixels write (defines the edge-case behaviour rather than fixing it).

---

## 4. BLEND — paletted translucency LUT *(landed; this section reconciles the Quake-team design with what already shipped)*

### 4.1 Quake's actual need

The dominant blend use case in Quake is **`Draw_FadeScreen`** — darken every framebuffer pixel by remapping through a 256-byte fade table at intermission, pause, and damage flashes. There's no per-pixel alpha, no source colour to blend into the destination — just `fb[x] = fade_lut[fb[x]]`.

A second use case is **alpha-keyed sprites with translucency**.

The landed `transluc[]` unit (`transluc.md`) implements the BUILD-engine 2D blend `fb[x] = transluc[(src << 8) | fb[x]]`. Quake's 1D unary remap is a strict subset: load the same 256-byte table into all 128 source rows of the LUT, then emit a `SOLID + TRANSLUC` span where `solid_color` is any value (the LUT becomes source-independent because every row holds the same data). Net cost: SDK helper, zero RTL.

### 4.2 Behaviour *(landed)*

When a span has the TRANSLUC flag set, the fragment processor:

1. Issues an AXI read on M0 of the destination framebuffer word(s) covering the span. Arbitrated against the texture cache via `blend_owns_m0` in `gpu_core.v`.
2. For each pixel, looks up the new value in the 32 KB `transluc[]` BRAM indexed by `key = { shaded_src[7:1], fb_byte[7:0] }` (the low bit of source is dropped — 128×256 quant; 79.4% byte-exact vs full 64 KB; sub-JND average error 1.3 in BUILD's 6-bit-per-channel space).
3. Writes the result back through the existing `fb_acc` accumulator + AXI burst write path. Same-word in-flight overdraw uses the lane-merge bypass at `BLEND_R_WAIT` so a second blend of the same word reads the freshest GPU-owned value, not stale SDRAM.

If the span also has SKIP_ZERO set, source bytes equal to `0xFF` discard the write **before** the FB read is issued (we never read the FB word for a skipped pixel — important when the span is sparse). Already implemented.

If `TRANSLUC_REV` is set, the key bytes are swapped: `key = { fb_byte[7:0], shaded_src[7:1] }` (note: in REV mode, the FB byte's low bit is the one preserved on the source axis — design choice in `transluc.md` §C).

### 4.3 LUT upload *(landed — supersedes the original `CMD_SET_BLEND_LUT 0x29`)*

The original draft proposed a new ring-buffer command `CMD_SET_BLEND_LUT (0x29)` with mode word 0=1D / 1=2D. **This was superseded.** The shipped path reuses the existing colormap-upload MMIO with a target-select bit:

```
GPU_CMAP_ADDR (MMIO 0x20):
  [31]    target_select (0 = colormap, 1 = transluc[])
  [14:0]  byte address within target  (auto-increments by 4 on each
                                       GPU_CMAP_DATA write)
GPU_CMAP_DATA (MMIO 0x24):
  [31:0]  full 32-bit word write to the selected target
```

SDK helper:

```c
/* Decimates BUILD's 64 KB transluc[256][256] to the 32 KB / 128×256
 * fabric LUT (low bit of source axis dropped). */
void of_gpu_translucency_upload(const uint8_t *table, uint32_t size);
```

For Quake's `Draw_FadeScreen` (1D unary remap), the SDK needs one new helper that writes a 256-byte table into all 128 source rows:

```c
/* Replicate a 256-byte unary remap into every source row of the
 * fabric LUT.  After this, any SOLID + TRANSLUC span behaves as
 * fb[x] = remap[fb[x]], independent of solid_color. */
void of_gpu_blend_lut_unary(const uint8_t remap[256]);
```

Storage cost was originally listed as "1 M10K (256 B) for 1D" / "4 M10K for 2D". **Both numbers were wrong.** The fitter infers the colormap RAM at 1 M10K per KB; the landed `transluc[]` is 32 KB → **32 M10K**. There is no separate 1D-mode storage — Quake's `Draw_FadeScreen` rides on the existing 2D LUT.

### 4.4 Span flag *(landed)*

```c
#define OF_GPU_SPAN_TRANSLUC      (1 << 6)   /* fb[x] = transluc[(src<<8)|fb] */
#define OF_GPU_SPAN_TRANSLUC_REV  (1 << 7)   /* swap key bytes */
```

Use `OF_GPU_SPAN_TRANSLUC` together with `OF_GPU_SPAN_COLORMAP` for shaded translucent draws (the source byte is the post-shade palette index). For Quake's `Draw_FadeScreen`, the SOLID + TRANSLUC variant is preferred and ignores COLORMAP.

For triangle-level translucency, the same flag plumbing as SOLID is used (sticky `tri_translucent` bit; spec is open — see §8).

### 4.5 Fragment-processor and FB-acc changes *(already paid)*

The original §4.5 listed three deltas:

- `fb_acc` RMW + freshness rule
- 2-client M_RD arbiter + response routing
- LUT BRAM + indexer

**All three are in HEAD.** Re-implementing them as part of this spec is a no-op; the only cost the BLEND→`Draw_FadeScreen` integration adds is the `of_gpu_blend_lut_unary()` SDK helper (zero gates) and the `SOLID + TRANSLUC` interaction tested in §3.5.

### 4.6 Cost estimate *(retroactive — already on the chip)*

| Sub-component | ALMs | DSP | M10K | Status |
|---|---|---|---|---|
| `fb_acc` RMW state machine + freshness rule | 90 | 0 | 0 | landed |
| 2-client M_RD arbiter + response routing | 80 | 0 | 0 | landed |
| LUT BRAM (32 KB / 128×256) + read port + indexer | 60 | 0 | 32 | landed |
| Span flag + payload routing | 10 | 0 | 0 | landed |
| **Total (BLEND fabric)** | **240** | **0** | **32** | landed |
| `of_gpu_blend_lut_unary()` SDK helper | 0 | 0 | 0 | new |

**Total net-new for `Draw_FadeScreen` integration: 0 ALMs, 0 DSP, 0 M10K.**

### 4.7 Test cases

- `tb_gpu_blend_fade_screen` — upload a unary-remap LUT (each entry darker than its index), pre-load FB with a gradient, render a full-FB SOLID + TRANSLUC span, verify each output byte = remap[input]. Compares against a CPU reference.
- `tb_gpu_blend_freshness` — render two overlapping TRANSLUC spans, verify the second sees the first's blend output (not the original FB). **Already covered by `test_transluc_overdraw`.**
- `tb_gpu_blend_skip_zero` — TRANSLUC + SKIP_ZERO with a sparse source, verify skipped pixels do not trigger an AXI R for their FB word.
- `tb_gpu_blend_under_drops` — extend the existing tb_gpu drop-sim coverage to the new M_RD client. **Already covered by `test_transluc_no_blend_interleave`.**
- `tb_gpu_blend_combined_flags` — combinations: `TRANSLUC + DEPTH_TEST`, `TRANSLUC + COLORMAP + SKIP_ZERO`, `SOLID + TRANSLUC + DEPTH_TEST`. Catches mux glitches that single-flag tests miss.

### 4.8 Future extension — per-channel alpha (Tier 2 BLEND)

Listed as `gpu.md` Tier 3 and not specified here in detail, but the prerequisites are unchanged:

- RGB565 framebuffer support (currently I8 only).
- 2 DSPs for per-channel multiplies.
- ~120 ALMs for the blend math.

Quake doesn't need this. Defer until an SDL2 app requires it. Note the existing 32 M10K of `transluc[]` is I8-only; an RGB blend path would not reuse that storage.

---

## 5. CMD_FLIP_FB — autonomous frame flip (new)

### 5.1 Goal

End-of-frame flip without CPU involvement after the kick. CPU pushes the frame's commands ending in `CMD_FLIP_FB`, kicks once, and immediately starts preparing the next frame. The GPU executes the queue in order; when it reaches `CMD_FLIP_FB`, all prior draws have completed, and the GPU triggers the display flip.

### 5.2 Command encoding

```
CMD_FLIP_FB (opcode 0x05)
  Word 0: [31:0] flip_target_addr  (32-bit physical address of the
                                    framebuffer to swap onto the display)
```

The flip target is **explicit** in the command, not implicit from the most recent `CMD_SET_FB`, because:

- `CMD_SET_FB` was bound earlier in the frame for *drawing* purposes; the flip semantically targets the same buffer, but having the CPU specify it explicitly avoids ambiguity in flows that bind multiple FBs in one frame (e.g., render-to-texture before final compositing).
- It makes the command self-documenting in ring traces.

### 5.3 Behaviour

When `gpu_core.v`'s command FSM consumes a `CMD_FLIP_FB`:

1. **Drain stage.** All prior draws have already executed in command-order, so by the time the FSM is processing this command, the framebuffer is being painted by the trailing fragment-pipeline stages. Wait for the existing fragment pipeline + `fbss` accumulator to drain to SDRAM. The drain condition is the same one `CMD_FENCE` uses today, with the addition of `m_wr_b_resp` having returned for every outstanding burst — i.e. the flip must wait until `m_wr_outstanding == 0`. Worst case is a single full-row burst of latency (~250 cycles), which is invisible at frame cadence.

2. **Flip-pending register.** Write the new register `GPU_DISPLAY_NEXT_ADDR` with `flip_target_addr` and assert `gpu_flip_request` for one cycle. The display controller's vsync ISR (or hardware logic) latches `GPU_DISPLAY_NEXT_ADDR` into the active scanout pointer at the next vblank edge — standard "next-vsync flip" semantics, no tearing.

3. **Coalescing rule.** If `gpu_flip_request` is asserted while a previous flip is still pending (display hasn't reached vblank yet), the GPU command FSM **stalls** on this command until the pending flip completes. This back-pressures the CPU when it tries to push frames faster than the display refreshes (60 Hz). Without this, the second flip would race the first.

4. **Implicit-fence semantics.** `CMD_FLIP_FB` advances `GPU_FENCE_REACHED` by one **after** the flip register has been latched, using a dedicated token namespace separate from `CMD_FENCE`. Concretely: the command consumer increments `gpu_flip_fence_reached` (a separate 32-bit register exposed at `GPU_FLIP_FENCE_REACHED`, MMIO offset TBD by gateware), not `gpu_fence_reached`. CPU code that wants "previous flip queued" polls the flip-specific counter; CPU code that wants "all queued GPU work has been retired" still polls `GPU_FENCE_REACHED` after an explicit `CMD_FENCE`. Mixing the two namespaces was a footgun the prior draft had implicit; this draft makes it explicit.

5. **GPU-idle guarantee.** After step 1 drains, the GPU is idle (no pending fragment, no outstanding AXI write). Submitting a `CMD_FLIP_FB` therefore offers a free synchronisation point that the engine can use as `gpu_finish()` without an extra `CMD_FENCE` — and on the rare path where the engine needs to read FB after flip (screenshot capture, save-on-quit), the flip's drain stage is sufficient.

### 5.4 Display-controller integration

The cleanest integration the gateware team proposes (per the existing architecture):

- A new GPU-output port `gpu_flip_request` (1 bit) and `gpu_flip_addr` (32 bits) feed the existing display controller.
- Display controller already has a "scanout base address" register that it samples at vblank. Re-route that register's load mux: at vblank, if `gpu_flip_request` is high, latch `gpu_flip_addr` into the scanout base; clear `gpu_flip_request` (handshake back to the GPU so it can accept the next flip).
- If the display controller currently lives behind the kernel's `of_video_flip()` SVC, that SVC stays as a fallback for non-GPU code paths (system messages, BIOS-ish stuff). The GPU-driven path doesn't go through the SVC.

> **P1 unblocker — display-controller scope.** The display path on the Pocket target is implemented in the per-target `core_top.v` and the on-chip `video_CRT_scanout_indexed_BRAM` block; on `sim` it is a stub. The flip register and vblank handshake have to be **cross-target** because SDK apps are portable (`docs/app-virtual-map.md`, `make check-api`). Before this section can become RTL, the gateware team needs to confirm: (a) where `gpu_flip_addr` enters the display controller on each target, (b) whether the existing scanout-base register is single-buffered or already double-buffered, (c) whether vblank is exposed to `gpu_core.v`'s clock domain or needs a CDC. Until these are answered, treat §5.4 as a stub and §5.7's cost estimate as lower-bounded.

### 5.5 Engine-side use

```c
// Frame N
of_emit_clear(...);              // queued
... draws ...                    // queued
of_gpu_flip_fb(buf_N);           // queued — terminates frame
of_emit_kick();                  // publishes wrptr ONCE for whole frame
// CPU returns immediately. Starts frame N+1 prep.

// Frame N+1
of_emit_set_fb(buf_(N+1));
of_emit_clear(...);
... draws ...
of_gpu_flip_fb(buf_(N+1));
of_emit_kick();
```

**Triple-buffer back-pressure semantics.** With 3 buffers (`buf_0`, `buf_1`, `buf_2`) and per-buffer fence tracking on the engine side (`buffer_flip_fence[3]` array, one entry per buffer holding the flip-fence token observed when the buffer was last submitted for display), the engine binds `buf_((frame_index) % 3)` for drawing. Before binding, it polls `GPU_FLIP_FENCE_REACHED >= buffer_flip_fence[buf_id] + 1` — i.e. "the flip after my last submission has retired, which means the display is no longer scanning out this buffer." Three buffers + 60 Hz display + a CPU that can produce a frame in ≤ 16.6 ms means this poll is almost always non-blocking; if the CPU is faster than the display, it blocks here (correct, intentional, equivalent to vsync).

The only blocking point is when the CPU comes back to a buffer 3 frames later — by then its draws are done and the display has long since released it.

### 5.6 SDK additions

```c
static inline void of_gpu_flip_fb(uint32_t flip_target_addr) {
    _gpu_cmd_header(GPU_CMD_FLIP_FB, 1);
    _gpu_ring_write(flip_target_addr);
}

static inline uint32_t of_gpu_flip_fence(void) {
    /* Read the flip-specific fence counter, distinct from
     * GPU_FENCE_REACHED.  CMD_FLIP_FB advances this counter; CMD_FENCE
     * does not. */
    return GPU_FLIP_FENCE_REACHED;
}
```

Plus engine wrapper `of_emit_flip_fb(uint32_t)`.

The kernel's `of_video_flip()` SVC stays for fallback / system overlay use (boot splash, kernel panic, BIOS-ish paths). The two paths must not be used simultaneously: doing so creates a race between the SVC's direct register write and the GPU's queued flip. Document this in `of_video_flip()`'s header — and add a runtime debug-mode assertion (`gpu_flip_request != 0` ⇒ trap) to catch accidental mixing during development.

### 5.7 Capability advertisement

Apps need to detect the new primitives without parsing a bitstream version string. Add the following to `of_caps.h`:

```c
#define OF_HW_GPU_SOLID         (1u << N0)   /* CMD_DRAW_SOLID_SPAN, CMD_SET_SOLID_COLOR */
#define OF_HW_GPU_FLIP          (1u << N1)   /* CMD_FLIP_FB + GPU_FLIP_FENCE_REACHED */
#define OF_HW_GPU_TRANSLUC      (1u << N2)   /* OF_GPU_SPAN_TRANSLUC + transluc[] */
```

(Bit positions to be assigned by the kernel team against the existing `of_caps.h` map.) The Quake engine reads these once at startup; if any required bit is missing, it falls back to the corresponding CPU path (and, for SOLID/TRANSLUC, keeps the `of_cache_flush()` in `d3d_gpu_flush` that the GPU-owns-FB principle would otherwise retire).

### 5.8 Cost estimate

- New register: `gpu_flip_addr` (32 bits) + `gpu_flip_request` (1 bit) — ~36 ALMs.
- Flip-pending coalescing: 1 small FSM extension to the command consumer — ~30 ALMs.
- Flip-fence counter (`GPU_FLIP_FENCE_REACHED`): 32-bit counter + MMIO read decode — ~20 ALMs.
- Display-controller mux + vblank handshake: ~10 ALMs in `gpu_core.v` plus an additional vblank-edge handshake in the display block — gateware team to size the display side.

**Total in `gpu_core.v`: ~96 ALMs, 0 DSP, 0 M10K** (excluding the display controller's own delta, which is unbounded until §5.4's open questions are answered).

### 5.9 Test cases

- `tb_gpu_flip_basic` — push CLEAR + DRAW_SPAN + FLIP_FB(addr), verify after fence: framebuffer at addr is painted, `gpu_flip_addr` register holds addr, `gpu_flip_request` is high.
- `tb_gpu_flip_coalesce` — push two FLIPs back-to-back without simulating a vblank; verify the second FLIP stalls.
- `tb_gpu_flip_fence_separate` — push FLIP_FB(buf_A), verify `GPU_FLIP_FENCE_REACHED` advances by 1 and `GPU_FENCE_REACHED` does not. Then push CMD_FENCE, verify only `GPU_FENCE_REACHED` advances.
- `tb_gpu_flip_drain` — push DRAW_SPAN that lands on the last word of the FB, then immediately FLIP_FB. Verify the AXI write completes (via `m_wr_outstanding == 0`) before `gpu_flip_request` rises.
- Full-system: connect a vblank stimulus, push two frames with FLIP, verify the displayed buffer alternates and there's no flip during scanout.

---

## 6. Engine-side roadmap (post-gateware-land)

This roadmap aligns with the **GPU owns the framebuffer — CPU never writes** principle (`project_gpu_owns_framebuffer.md`). Once SOLID + BLEND + `CMD_FLIP_FB` are in:

1. **Move all 2D to GPU.**
   - `Draw_Fill` → SOLID span(s) (or a SOLID rect = 2 SOLID triangles for large fills)
   - `Draw_FadeScreen` → SOLID + TRANSLUC span (full-screen) with a unary fade table loaded via `of_gpu_blend_lut_unary()`
   - `Draw_Pic` / `Draw_TransPic` → SPAN path with optional SKIP_ZERO (already implementable today; we reverted only because of mixing with CPU 2D — once everything is GPU there's no mixing)
   - `Draw_Character` → SPAN + SKIP_ZERO (with the conchars `0↔0xFF` swap at Draw_Init)
   - `Draw_TileClear` → SPAN with POT mask tiling
   - `Draw_ConsoleBackground` → SPAN with the in-place version-string overlay flushed once before the GPU reads it
   - `Sbar_Draw` (the status-bar layout) — composed entirely of the converted primitives above
   - **HUD / clear-bars / splash audit** — every site that currently calls `memset(frameplace, ...)` or writes through `vid.buffer` directly is in scope. Per the project memory: "for HUD / overlay / splash / bar-clear paths that historically used `memset(frameplace, ...)`, route them through `of_gpu_clear()` (or equivalent rectangular-clear command) so they're GPU-side."

2. **Drop the uncached framebuffer alias.** `vid.buffer` becomes a regular cached pointer; the GPU reads/writes through its own AXI master; the CPU never accesses it directly. `of_uncached(of_video_surface())` calls go away.

3. **Rework `VID_Update` around `CMD_FLIP_FB`.** Replace `of_emit_finish()` + `of_video_flip()` with `of_emit_flip_fb(buf_N)` + `of_emit_kick()`. The `VID_Update` body shrinks to ~3 lines.

4. **Per-buffer flip-fence tracking.** With 3 framebuffers and one flip-fence per `CMD_FLIP_FB`, the next-frame prep blocks only if the GPU somehow hasn't finished a 3-frame-old draw. On a well-pipelined frame, zero block.

5. **Drop `of_cache_flush()` in `d3d_gpu_flush`.** Conditional on steps 1+2 being complete and verified. Per the project memory: "as long as any CPU code writes to FB addresses, those addresses can have dirty L1 lines that race with GPU writes." The 32 M10K spent on `transluc[]` is justified only when this drop happens — otherwise the BLEND fabric has paid the cache-coherency tax twice.

After step 5, the renderer is fully autonomous: CPU produces commands, GPU consumes and flips. There's no per-frame stall on the CPU side, and no FB cache-flush µs/frame.

---

## 7. Resource budget summary

| Feature | ALMs | DSP | M10K | Status | Crit-path? |
|---|---|---|---|---|---|
| BLEND fabric (`transluc[]`) | 240 | 0 | 32 | **landed** | safe — RMW off-loop, mirrors existing `fbss` Z path |
| SOLID (§3) | 80 | 0 | 0 | new | safe — 2:1 mux at p2, single LUT level |
| `CMD_FLIP_FB` (§5) | 96 | 0 | 0 | new (modulo display-controller delta) | safe — off-pipeline, handshake to display ctl |
| **Σ net-new (this spec)** | **176** | **0** | **0** | | |
| Capability bits + SDK helpers | 0 | 0 | 0 | new | n/a (software) |

Reconciliation against the architecture-doc headroom:

- `gpu_architecture.md` §2.1 listed ~100 ALMs / 4 M10K free at the time of writing. That snapshot is **stale**: the BLEND landing consumed 240 ALMs and 32 M10K, and the current `seed_10_fit.log` reports 16,560 / 18,480 ALMs (90%), 271 / 308 M10K blocks (88%) — i.e. ~1,920 ALM / ~37 M10K headroom remains.
- 176 net-new ALMs fits comfortably in that envelope. 0 net-new M10K is uncomplicated.
- The display-controller delta (§5.4) is the residual uncertainty.

Future extensions (RGB blend Tier 2): +120 ALMs, +2 DSP, 0 M10K — defer.

---

## 8. Open questions

- **Display-controller register location and vblank source (§5.4).** Whose AXI does `gpu_flip_addr` need to drive? Is vblank already in `gpu_core.v`'s clock domain or does it need a CDC? P1 unblocker — gateware team to scope.
- **`GPU_FLIP_FENCE_REACHED` MMIO offset.** The register decoder is one slot from full (only `0x3C` remains). If `0x3C` is taken by this register, future MMIO additions need a decoder rev. Alternatively, multiplex the existing `GPU_FENCE_REACHED` with a sub-select bit written by the CPU.
- **Triangle TRANSLUC plumbing.** SOLID gets a `tri_solid` bit in the count word. Should `tri_translucent` follow the same pattern, or stick with sticky state (`CMD_SET_TRANSLUC_ACTIVE`)? Sticky is simpler, but per-triangle variation forces a SET between every triangle.
- **`of_video_flip()` SVC fate.** Stays as fallback for boot/panic/system overlays, but how do we prevent accidental concurrent use with `CMD_FLIP_FB`? Proposed: debug-mode assertion (§5.6); production behaviour TBD.
- **Engine-side bottleneck after this lands.** With FB writes fully offloaded, the next CPU bottlenecks become BSP traversal, edge-walk, and surface-cache lightmap rebuild. Out of scope here, but worth noting.
- **2D LUT precision sufficiency for Quake.** The 128×256 quant drops the LSB of the source axis. For SOLID + TRANSLUC where the source is a constant `solid_color`, the quant is only visible at the boundary between adjacent palette entries. Visually validated for Duke3D (transluc.md); needs a Quake-specific spot-check with `Draw_FadeScreen` at multiple fade depths before declaring acceptance.

---

## 9. Acceptance criteria

A successful land of this spec means, on the Quake target:

1. The `D_GPU_WORLD_LIGHT=1` build runs Quake with all 2D paths converted (`Draw_Fill` driven by SOLID, `Draw_FadeScreen` driven by SOLID + TRANSLUC).
2. `vid.buffer` is removed; the engine no longer references `of_uncached(...)` for the framebuffer.
3. `VID_Update` issues `CMD_FLIP_FB` instead of `of_video_flip` + `of_emit_finish`.
4. `of_cache_flush()` is dropped from `d3d_gpu_flush`. Per-frame "FB sync µs" goes to zero.
5. Per-frame CPU-side block on GPU completion is gone (measurable with a `pq_cycleprof` row counting "frame-end wait µs" — should drop to near zero).
6. `OF_HW_GPU_SOLID`, `OF_HW_GPU_FLIP`, `OF_HW_GPU_TRANSLUC` capability bits are advertised by the kernel and consulted by the engine.
7. No tearing or flicker on regression-tested Quake levels through standard gameplay (E1M1, E1M2, intermission, console toggle, menu in/out, fade screens).
8. `tb_gpu` passes all new test cases listed in §3.5, §4.7, §5.9, including the combined-flag matrix (`SOLID + TRANSLUC`, `TRANSLUC + DEPTH_TEST`, `SOLID + DEPTH_TEST + SKIP_ZERO`).
9. Hardware regression: HUD audit complete — no remaining `memset(frameplace, ...)` or direct `vid.buffer` writes outside of GPU command emitters.
