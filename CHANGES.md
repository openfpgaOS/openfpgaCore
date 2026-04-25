# openfpgaOS — change log

Per-commit fabric/firmware changes that downstream SDK sessions
(openfpgaOS-SDK, PocketDukeNukem-SDK) should know about.  Format:
one section per substantive landing, with **what shipped**, **what
it unblocks downstream**, and **resource delta**.

---

## 2026-04 — `gpu_tex_cache` true dual-port (commit `4ea5657`)

**What shipped.** `gpu_tex_cache.v` exposes two read-port interfaces
(port A + port B) sharing one M10K-backed storage and one AXI fill
machine.  Quartus infers TDP altsyncram from a two-always-block
read pattern; the existing port A signal names are unchanged so
`gpu_core.v`'s call site is byte-identical for texture fetches.
Single fill machine with fixed-priority A on tie (B's miss waits
at most one fill duration before its turn).  Port B is read-only
from the consumer's perspective.

**What it unblocks downstream.** Nothing on its own — port B is
tied off in this commit.  Prerequisite for the next two commits.

**Resource delta.** ALMs +~150 (second tag-check + arbiter +
duplicate stage-2 regs).  M10K unchanged (TDP at the same M10K
count as the prior SDP layout — Cyclone V supports both at 32 b ×
256 entries with the same block count).

---

## 2026-04 — Drop `cmap_bram`, route cmap reads through `tex_cache` port B (commit `479c760`)

**What shipped.**

- `cmap_bram` retired entirely (-16 M10K).  Palookup tables now live
  in SDRAM at `0x100000 + slot*0x4000` (16 KB per slot, 16 slots).
  The GPU pulls bytes through `gpu_tex_cache` port B.
- New ring opcode `CMD_SET_COLORMAP_ID = 0x28` (sticky 4-bit slot
  selector).  Reset default = 0, so single-palookup callers are
  bit-identical.
- Fragment pipeline's `fp_pipe_stall` extended with
  `cmap_pipe_wait` (`p2b_valid && SPAN_COLORMAP && !cmap_resp_valid_b`)
  to cover port-B miss latency.  On hit this is always 0 — the
  response lands the same cycle `pipe_addr_b` matches.
- CPU palookup uploads switch from MMIO to direct SDRAM writes via
  `caps->sdram_base + 0x100000 + slot*0x4000`, followed by
  `of_cache_clean_range` so the GPU's tex_cache fill sees committed
  bytes.  `GPU_CMAP_DATA` writes with bit 31 = 0 are now no-ops
  (the transluc[] target with bit 31 = 1 is preserved).

**What it unblocks downstream.**

- **Multi-palookup support** for BUILD/Duke3D maps.  Up to 16 distinct
  palookups simultaneously — well above the 4–8 typical Duke3D maps
  use.  No M10K ceiling; future bitstreams can grow the slot count
  without re-architecting.
- **GPU-owns-FB closure** (`project_gpu_owns_framebuffer.md`): with
  the cmap fallback path retired, Duke3D's `try_*` helpers no longer
  need a CPU spillover for non-pal0 sectors → no CPU FB writes →
  `of_cache_flush` in `d3d_gpu_flush` becomes droppable once the
  remaining BUILD inner loops are GPU-side.  Stage 5 of `transluc.md`
  was gating on this; now unblocked.

**API additions** (firmware api/of_gpu.h, mirrored to both SDK copies):

- `GPU_CMD_SET_COLORMAP_ID = 0x28`
- `OF_GPU_PALOOKUP_AXI_OFFSET = 0x100000`, `_STRIDE = 0x4000`,
  `_SLOTS = 16`
- `of_gpu_set_colormap_id(uint8_t slot)` — emits the 1-word ring
  command, sticky for subsequent SPAN_COLORMAP draws.
- `of_gpu_palookup_upload(uint8_t slot, const uint8_t *data,
  uint32_t size)` — writes to `caps->sdram_base + ...` + cache
  flush.  Up to 16 KB per slot.
- `of_gpu_colormap_upload(data, size)` retained as a slot-0 wrapper
  for legacy callers (gpudemo, d3d_gpu palookup[0] path) — bit-
  identical behaviour to before.

**Resource delta.** ALMs +~100 (cmap path rework, includes cleanup
of cmap_bram-associated logic).  M10K **−16** (cmap_bram retired).
Net: −16 M10K freed; tex_cache stays at 19 M10K.

**Verification.** tb_gpu 308 / 5 (pre-existing baseline 311 / 2 →
3 regressions tracked in tasks #59 #60 #61, all single-pixel /
fault-injection edge cases).  tb_gpu_floor exposes the same
end-of-span off-by-1 in F1–F7 (F8 reverse-stride passes).  Spike
analysis (`/tmp/cmap_cache_sim.c`) measured ~94% combined hit rate,
~5% worst-case frame-budget cost on a Duke3D-shape trace.

---

## 2026-04 — `CMD_CLEAR_RECT` partial-rect FB clear (commit `a3d70d8`)

**What shipped.**

- New ring opcode `CMD_CLEAR_RECT = 0x11`.  3-word payload:
  - word 0: `start_byte_addr` (CPU pre-computes
    `fb_base + y*stride + x` — matches CMD_CLEAR's "trust the CPU
    for layout" shape).
  - word 1: `{w[31:16], h[15:0]}`.
  - word 2: `{16'b0, color[15:0]}` — low 8 bits replicated 4× per
    FB word (matches CMD_CLEAR's color shape).
- Walks h rows × w bytes with byte-strobed partial-word edges.
  Word-aligned full-width strips hit the 4-byte fast path; arbitrary
  x/w byte-strobes the partial-word leading and trailing edges.
- `of_gpu_clear_rect(start_byte_addr, w, h, color)` SDK helper.

**What it unblocks downstream.**

- **Letterbox bars** (top/bottom 80×360 strips at boot/level
  transitions): single CMD_CLEAR_RECT per bar, no CPU memset.
- **Status-bar wipes** between frames: route `Sbar_Draw`'s
  background fill through CMD_CLEAR_RECT.
- **Menu pane backgrounds** + **splash bitmap underlays**:
  arbitrary-x partial-width rects work via the byte-strobe path.
- This is the **last per-frame CPU `memset(frameplace, …)` category**.
  After downstream rewires to use CMD_CLEAR_RECT, every per-frame
  FB write is GPU-side, satisfying `project_gpu_owns_framebuffer.md`
  and unblocking `of_cache_flush` retirement.

**API additions:**

- `GPU_CMD_CLEAR_RECT = 0x11`
- `of_gpu_clear_rect(uint32_t start_byte_addr, uint16_t w,
  uint16_t h, uint8_t color)` SDK helper.

**Resource delta.** ALMs +~140 (3 new states — S_CLEAR_RECT,
S_CLEAR_RECT_WORD, S_CLEAR_RECT_WAIT — plus 6 new regs for rect
state + strobe-mux logic).  M10K +0, DSP +0.

**Verification.** tb_gpu 335 / 5 (+27 new pass cases from
`test_clear_rect`: word-aligned 10×320 letterbox, byte-strobed
3×10 partial-width, 1×1 single-pixel rect, plus surrounding
sentinel-byte checks).

---

## 2026-04 — `gpu_tex_cache` Quartus multi-driver fix (commit `7a5210b`)

**What shipped.**  Verilator-tolerated multi-driver on `rd_tag_a/_b`,
`rd_valid_a/_b`, `rd_data_a/_b` (Error 10028 in Quartus): the
M10K-read latch and the S_FILL_OUT prime path both wrote those
regs.  Consolidated to a single writer per port — the latch's
else-branch fires the prime when a `prime_a` / `prime_b`
combinational signal is asserted (state == S_FILL_OUT, lat_port
matches, no consumer accept this cycle).  FSM block continues to
manage `pipe_*_x` and `fill_resp_valid_x` but no longer touches
`rd_*_x`.  Same posedge T sees both `pipe_*_x` and `rd_*_x` land
correctly, so consumer's PRE-edge T+1 still observes a coherent
`pipe_hit_x`.

**What it unblocks downstream.**  Quartus build closes again on
the cmap-via-cache plan.  No SDK-visible behaviour change.

**Resource delta.** ALMs ±~0 (combinational `prime_a/_b` signals;
the FSM block lost a few `rd_*_x <= …` lines).  M10K +0, DSP +0.

**Verification.** tb_gpu 335 / 5, byte-identical to the pre-fix
baseline.

---

## How to read M10K / ALM numbers

The "current fitted utilisation" callout in
`openfpgaOS-SDK/docs/gpu_architecture.md` §2 reflects the Pocket
bitstream after all the above commits.  Per-commit deltas above
are the *incremental* cost relative to the prior commit, not
absolute fitter numbers.  For absolute numbers, see
`src/fpga/targets/pocket/output_files/seed_10_fit.log` after a
fresh build.

---

## Open follow-ups

- **#59** end-of-span off-by-1 in cmap path (visible in
  `persp_2seg_px15`, `w300_r1_px6`, `tb_gpu_floor` F1–F7).  Same
  class — cmap byte for the LAST pixel of a span is the previous
  pixel's byte.  Likely a cmap_req_addr_reg latch / pipe_addr_b
  timing interaction at end-of-pipeline; needs VCD.
- **#60** `tb_gpu_gpudemo` MMIO-drops timeout: 1-in-500 ring writes
  drop, GPU should recover.  Was passing pre-cmap-via-cache; now
  hangs.  New `req_ready_b` semantics interact with the recovery
  path.
- **#61** triangle SKIP_ZERO `triCK_y1_x2`: pixel reads `0xFF`
  (skip key) instead of `0x20`.  Triangle internal-span emit
  interaction with cmap-via-port-B response timing.

The cmap-via-cache and CMD_CLEAR_RECT mainline paths work in 335 of
340 tests across spans, triangles, depth, skip-zero, perspective
single-segment, batched triangles, transluc[] BLEND, multi-line
cache stress, gpudemo replay (no drops), and the full new
`test_clear_rect` matrix.  The 5 open regressions cluster at
boundary conditions and don't block the SDK from starting to use
the new APIs.
