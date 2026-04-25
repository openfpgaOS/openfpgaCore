# Fabric `transluc[]` blend unit — implementation plan

## Status

- **Stage 1** (LUT BRAM + MMIO target-select on `GPU_CMAP_ADDR/DATA`): **landed**.
  `transluc_bram[0:8191]` (8 K × 32-bit = 32 KB) declared in `gpu_core.v`,
  CPU writes routed when `cmap_wr_target` (bit 31 of the addr write) is
  high. Tested via build + existing 301 passing GPU tests.
- **Stage 2** (read port + span-flag definitions): **landed**.
  `transluc_rd_addr` / `transluc_rd_data` registered read with the same
  fp_pipe_stall hold pattern as the colormap. `SPAN_TRANSLUC` (bit 6)
  and `SPAN_TRANSLUC_REV` (bit 7) localparams added.
- **Stage 3** (FB read-modify-write + BLEND output mux + AXI-read
  arbitration on M0): **pending**. The complete-stage implementation
  is the remaining chunk and is scoped in the sections below.
- **Stage 4** (Verilator tests + SDK API + BUILD integration): **pending**.

The `transluc_rd_addr` driver is currently a placeholder reset only —
no fragment-pipe stage feeds it yet. That arrives with stage 3.

## Pre-RTL data — Duke3D `transluc[]` analysis

Empirical row-dictionary feasibility check (see `/tmp/analyze_transluc.c`
+ `/tmp/approx_transluc.c`):

- **246/256 unique rows** in Duke3D's exact 64 KB `transluc[]` —
  row-dictionary saves only ~1 KB.
- **Asymmetric** (`T[s,d] != T[d,s]` everywhere). Cannot store half.
- **Row 0 is not identity** — no trivial src=0 shortcut.

Approximation accuracy (RGB distance in BUILD's 6-bits-per-channel space,
JND ≈ 3):

| scheme | size | M10K | exact% | RGB-dist avg |
|---|---|---|---|---|
| exact 256×256 | 64 KB | ~64 | 100% | 0 |
| **128×256** (drop src LSB) | **32 KB** | **~32** | **79.4%** | **1.3** |
| 128×128 | 16 KB | ~16 | 51% | 3.4 |
| 256×32 | 8 KB | ~8 | 18% | 10.3 |
| 64×64 | 4 KB | ~4 | 17% | 7.5 |

**Selected: 32 KB / 128×256.** Sub-JND average error, fits in 37 free
M10K with 5 to spare. Visual indistinguishability vs exact in motion
expected; spot-check pending hardware test.

## Review feedback

The goal is good, but the current resource story is too optimistic for
the Pocket build. The current checked-in `seed_10_fit.log` is already at
16,560 / 18,480 ALMs (90%) and 271 / 308 M10K blocks (88%). A full
64 KB exact `transluc[]` table should be treated as a variant feature,
not the default path, unless something else gives back a large number of
M10Ks first.

Two corrections drive the rest of this feedback:

- The 16 KB colormap currently infers as 16 M10K blocks in the fitter
  report, so a same-shaped 64 KB `transluc[]` RAM is likely ~64 M10K,
  not 32. That alone exceeds the current 37-block headroom before the
  framebuffer read path is added.
- The proposed `0x30`, `0x34`, and `0x38` MMIO offsets collide with the
  existing GPU debug registers. The register decoder is also already
  only one 32-bit slot away from full, so prefer reusing the existing
  colormap upload port with a target-select bit instead of adding more
  registers.

Recommended resource-first plan:

1. Start with word-level framebuffer read-modify-write and no BRAM line
   cache. Read one existing 32-bit framebuffer word, blend the touched
   byte lanes, then write one 32-bit word back. This costs ALMs and a
   small amount of control state, but no M10K.
2. Add exact full-table support only behind `GPU_FEAT_TRANSLUC_FULL`.
   For the default Pocket bitstream, first prove whether an exact row
   dictionary or a small approximation table is acceptable.
3. Use 32-bit RAM words and full-word MMIO writes for any translucency
   table. Do not add byte-write cases; the colormap notes already show
   that byte-enable style can collapse BRAM inference into registers.
4. Keep `OF_GPU_SPAN_TRANSLUC` and `OF_GPU_SPAN_TRANSLUC_REV` as span
   flags, but gate all hardware behind a capability bit so SDK callers
   can fall back cleanly on bitstreams that omit the feature.

## What BUILD does today (semantics to reproduce)

Translucency in BUILD is an indexed-color compositing path. It uses a
64 KB lookup table called `transluc[]` (declared in `engine.c` /
`draw.c`):

```c
uint8_t transluc[65536];   // 256 src × 256 dest → blended dest
```

The idea: instead of doing real RGB alpha blending, BUILD pre-computes
"what palette index represents source-color X composited over
destination-color Y" and stores it. At blit time the inner loop is:

```c
// tvlineasm1, tvlineasm2, DrawSpriteVerticalLine, translucent rotatesprite
uint8_t fb_pixel = *dest;                  // read existing fb byte
uint8_t shaded   = palookupoffse[texel];   // shade-corrected source
uint16_t key     = (shaded << 8) | fb_pixel;
*dest = transluc[key];                     // composited result
```

There's a "reverse stereo" bit `transrev` that swaps the high/low bytes
of the key — used for some games' 50/50 vs additive variants. Handle as
a per-call flag.

Three things make this hard for the current GPU:

1. **Read-modify-write on the framebuffer.** Today the GPU's pixel
   pipeline is write-only — `gpu_core.v::FBSS` accumulates 4 bytes and
   bursts them to SDRAM via the AXI master, never reads back from SDRAM
   at the fb address.
2. **A 65 536-byte LUT** indexed per pixel by the inner loop. Has to be
   on-chip BRAM (off-chip SDRAM-per-pixel would crater throughput).
3. **The translucency is per-span**, not a global state — each
   `DRAW_SPAN` decides whether to consume its fragments through the
   transluc path or not.

## Fabric additions

### A. transluc[] LUT BRAM

Exact storage should be word-oriented, matching the current colormap RAM
style:

```verilog
reg [31:0] transluc_bram [0:16383];  // 64 KB, CPU/GPU full-word access
```

- One write port: CPU upload through a full-word MMIO data register,
  auto-incrementing by 4 bytes.
- One read port: fragment processor reads the 32-bit word containing
  `key[15:2]`, then selects `key[1:0]` with a byte-lane mux.
- Do not infer this as `reg [7:0] transluc_bram [0:65535]` with byte
  writes. The existing colormap comment documents why full-word writes
  are important for M10K inference.
- Resource estimate: current fitter data shows 16 KB colormap == 16
  M10K. By that pattern, 64 KB exact `transluc[]` is ~64 M10K. That does
  not fit the current Pocket build without freeing about 27 M10Ks first.

Lower-resource table choices:

- **Exact row dictionary, if the game table allows it.** At load time,
  scan `transluc[]` for duplicate 256-byte rows. Store
  `row_index[256]` plus `unique_rows * 256` bytes. This is exact when
  rows repeat; if there are 32 unique rows, storage is ~8 KB plus a tiny
  index table; if there are 64, ~16 KB. The decision needs measurement
  on real Duke3D / Shadow Warrior / Blood tables before RTL starts.
- **Approximate 16 KB table.** Store a reduced destination axis or a
  fixed alpha class. This is likely ~16 M10K in the current inference
  style, not 8. It may fit, but it changes the BUILD look and needs
  screenshot/reference-frame approval.
- **Tiny quantised table.** A 256 x 16 table (source byte by high
  destination nibble) is ~4 KB / ~4 M10K. This is the cheapest BRAM
  option, but it is visibly approximate and should be treated as a
  fallback, not a faithful BUILD implementation.
- **Do not do palette RGB nearest-colour blending in fabric.** The
  nearest-palette search is the expensive part; replacing the 64 KB LUT
  with per-pixel nearest-colour logic saves BRAM by spending too much
  ALM/timing budget.

### B. New MMIO registers

Avoid new standalone registers. `0x30`, `0x34`, and `0x38` are already
debug reads (`GPU_DBG_BADWR`, `GPU_DBG_BADCNT`, `GPU_DBG_RINGWR`), and
only `0x3C` remains in the current 4-bit register decoder.

Lower-resource upload path:

| Offset | Reg | Direction | Notes |
|--------|-----|-----------|-------|
| 0x20 | `GPU_CMAP_ADDR` | W | Reuse existing upload address. Add `bit31` as target select: `0` = colormap, `1` = transluc. Low bits are the byte address. |
| 0x24 | `GPU_CMAP_DATA` | W | Full 32-bit write to the selected target, addr += 4. |

This adds one target-select bit and a wider address register instead of
growing the MMIO map. It also keeps upload code aligned with the current
full-word BRAM inference pattern. No `GPU_TRANSLUC_FLUSH` is needed for
the first implementation if the framebuffer blend path uses explicit
word read-modify-write state instead of a persistent line cache.

### C. Fragment-pipeline changes (the hard part)

A new pipeline stage between the texel/colormap step and the FB-write
accumulator. Conceptually:

```
[texture fetch] → [colormap row apply] → [BLEND]  → [FB accumulator] → AXI write
                                            ▲
                                     (existing pixel)
```

The BLEND stage:

1. Reads the existing fb byte at the fragment's destination address.
   Resource-first implementation choices:
   - **Word-level read-modify-write, recommended first.** When a
     translucent fragment reaches the write side, read the containing
     32-bit fb word once, blend the requested byte lane(s), and write the
     word back. Horizontal spans get one read per four pixels instead of
     one read per pixel. This costs no M10K, preserves existing
     word-coalescing behavior, and gives an honest baseline before a
     cache is added.
   - **Bypass pending `fb_acc` data.** If the word being blended matches
     the current unflushed framebuffer accumulator, use the accumulator
     word as the destination source. Otherwise a second translucent span
     can read stale SDRAM while a newer opaque/translucent byte is still
     pending in the GPU.
   - **Line cache, later if profiling demands it.** A 1 KB cache is
     probably 1 M10K plus tag/control ALMs in this build, but it also
     introduces invalidation and stale-line cases. Do not spend it until
     the one-word RMW path is measured on real translucent walls/sprites.
   - **Direct SDRAM read per fragment.** Lowest control complexity, but
     worst bandwidth. Use only as a bring-up mode or behind a debug
     define.
2. Composes `key = (shaded_texel << 8) | fb_byte` (or `(fb_byte << 8) |
   shaded_texel` if the `BLEND_TRANSLUC_REV` flag is set).
3. Reads `transluc[key]` from the new LUT BRAM.
4. Output = `transluc[key]` instead of the raw colormap output.

When the new flag isn't set, the BLEND stage is bypassed and the
pipeline behaves exactly as today.

### D. Span flag

```c
#define OF_GPU_SPAN_TRANSLUC      (1 << 6)   /* output via transluc[(src<<8)|fb] */
#define OF_GPU_SPAN_TRANSLUC_REV  (1 << 7)   /* swap high/low bytes of the key */
```

For BUILD callers, `OF_GPU_SPAN_TRANSLUC` should be emitted together
with `OF_GPU_SPAN_COLORMAP` because the source byte is the post-shade
palette index. The RTL does not need to force that implication: if a
future caller sets `TRANSLUC` without `COLORMAP`, blend the raw texel
byte against the framebuffer byte.

Triangle path can support the same flag via the existing flag-passthrough
— gets you translucent triangles for SM64 or Wipeout for free.

## SDK additions

### `of_gpu.h`

```c
/* Upload BUILD's pre-computed transluc[65536] LUT, or a compressed
 * table format selected by the active GPU capability bit.  Uses the
 * shared colormap/transluc upload port; writes are 32-bit words. */
void of_gpu_translucency_upload(const uint8_t *table, uint32_t size);

/* Per-span flag bits */
#define OF_GPU_SPAN_TRANSLUC      (1u << 6)
#define OF_GPU_SPAN_TRANSLUC_REV  (1u << 7)
```

Add a capability bit such as `OF_HW_GPU_TRANSLUC` (and possibly
`OF_HW_GPU_TRANSLUC_FULL`) so BUILD integration can select GPU or CPU
paths without guessing which bitstream is loaded.

### `d3d_gpu.h` / `d3d_gpu.c` — five new helpers mirroring the SW inner loops

```c
/* Translucent vertical column — replaces tvlineasm1.  Same args as
 * d3d_gpu_mvline (we add SPAN_TRANSLUC instead of SPAN_SKIP_ZERO). */
void d3d_gpu_tvline(uint8_t *dest, int num_pixels, int shade,
                    uint32_t vplce, uint32_t vince, uint8_t v_shift,
                    const uint8_t *texture, int reverse);

/* Translucent 2-column form — replaces tvlineasm2.  Less common but
 * BUILD uses it for some sloped wall variants. */
void d3d_gpu_tvline2(...);

/* Sprite vertical line with translucent compositing — replaces
 * DrawSpriteVerticalLine.  Uses tspal as the colormap row. */
void d3d_gpu_sprite_vline(int8_t *params, int count,
                          const uint8_t *texture, uint8_t *dest);

/* Opaque rotatesprite blit (no transluc, just SKIP_ZERO).  Emits one
 * SPAN_COLORMAP per destination row. */
void d3d_gpu_rotatesprite_opaque(int dx, int dy, int dw, int dh,
                                  int src_xfrac, int src_yfrac,
                                  int xstep, int ystep,
                                  const uint8_t *src_tex, int src_w);

/* Translucent rotatesprite blit — adds SPAN_TRANSLUC. */
void d3d_gpu_rotatesprite_translucent(...);
```

### Hook sites in BUILD

```
draw.c::tvlineasm1                  → d3d_gpu_try_tvline()
draw.c::tvlineasm2                  → d3d_gpu_try_tvline2()
draw.c::DrawSpriteVerticalLine      → d3d_gpu_try_sprite_vline()
engine.c::dorotatesprite (inner pixel loop, not the wrapper)
                                    → branch on dastat & 32:
     • opaque       → d3d_gpu_rotatesprite_opaque
     • translucent  → d3d_gpu_rotatesprite_translucent
display_of.c::clear-bars memset     → of_gpu_clear() on the bar regions
splash bitmap blits                  → d3d_gpu_rotatesprite_opaque
                                       (or a custom unscaled blit helper)
```

Same gated `try/return` pattern as the existing vline/hline work, so
falling back to SW per call is functionally safe. It does **not** deliver
the framebuffer-coherency win unless every fallback that writes
`frameplace` also performs the existing cache sync, or unless all
framebuffer-writing translucent paths have GPU coverage.

## What this buys

- **Race gone, if GPU coverage is complete.** Once `frameplace` is no
  longer touched by CPU code, no L1 dirty lines exist for fb addresses,
  no eviction can clobber GPU writes. If the resource-reduced bitstream
  keeps any CPU translucent fallback, keep the existing cache coherency
  path around that fallback.
- **GPU acceleration for translucent walls and sprites.** Today these
  are some of the most expensive CPU per-pixel loops (`transluc` lookup
  is a 64 KB indirect — CPU cache murder). Exact GPU LUT BRAM is much
  faster; compressed/approximate modes need visual approval first.
- **Triangle path gets blending too.** SM64's translucent water,
  Wipeout's afterburner glow — same primitive, no extra work.
- **`of_cache_flush` in `d3d_gpu_flush` can become unnecessary** only
  after CPU framebuffer writes are fully removed from the active paths.
  Keep it until that is validated on the exact bitstream/configuration
  being shipped.

## What it costs

- **Full exact table**: likely ~64 M10K, not 32, plus blend control
  logic. This is over the current Pocket fit headroom by roughly 27
  M10Ks before any framebuffer read/cache storage. Treat it as a
  separate `GPU_FEAT_TRANSLUC_FULL` bitstream unless other memories are
  removed.
- **16 KB compressed/approximate table**: likely ~16 M10K in the current
  inference style. This can fit in the `seed_10_fit.log` headroom, but
  leaves much less placement slack and still needs visual validation.
- **4 KB quantised table**: likely ~4 M10K. Best resource footprint, but
  approximate.
- **Framebuffer read path**: word-level RMW should be 0 M10K and roughly
  low hundreds of ALMs. A later line cache adds M10K and coherency
  complexity.
- **Throughput**: word-level RMW should be acceptable for horizontal
  spans because one SDRAM read covers four byte lanes. Vertical columns
  still pay closer to one read per pixel; measure before adding a line
  cache.
- **One-time CPU upload of full transluc[]**: 64 KB is 16,384 32-bit
  MMIO writes at level load, never per-frame. Compressed tables reduce
  this proportionally.
- **Refactoring the inner-loop callers**: about 5 functions in
  `draw.c`, plus `dorotatesprite` in `engine.c`. Same pattern as the
  wall/floor work already done — each call site is one
  `if (d3d_gpu_try_X(...)) return;` gate.

## Validation strategy

The fabric work needs at least three new `tb_gpu_*` cases:

1. **`tb_gpu_transluc_lut`** — upload a deterministic LUT, draw a single
   span with `SPAN_TRANSLUC`, verify each output byte equals
   `LUT[(shaded_src << 8) | preexisting_fb]` byte-exact against a CPU
   oracle that reproduces the BUILD inner loop in C.
2. **`tb_gpu_transluc_overdraw`** — same span drawn twice in a row (so
   the second pass blends its own first-pass output). Catches the
   fb-read-cache stale-line case if the LUT changes don't propagate.
3. **`tb_gpu_transluc_no_blend_interleave`** — alternating opaque +
   translucent spans on the same row. Catches the case where the
   bypass/non-bypass mux glitches the accumulator.
4. **`tb_gpu_transluc_pending_acc_bypass`** — draw an opaque byte into a
   word, keep it pending in `fb_acc`, then blend a translucent byte that
   reads the same word before the write has reached SDRAM. Verifies the
   RMW path uses the newest GPU-owned word.
5. **`tb_gpu_transluc_resource_mode`** — for any compressed or quantised
   table mode, run the same source/fb cases against that mode's CPU
   oracle. Exact modes must be byte-exact; approximate modes need an
   explicit image-diff tolerance, not silent drift.

**Real-world**: Duke3D shooting at a glass window or through smoke —
both use translucent rendering heavily. If the visible output matches a
CPU-only reference frame to within zero pixels, the fabric is correct.

**Fit gate**: after simulation passes, run Quartus and record ALMs,
M10K, DSPs, and worst slack next to the current baseline. Do not accept
the default Pocket variant if it pushes M10K usage over the device limit
or leaves the design dependent on an unusually lucky seed.

## Open questions / decisions to resolve before starting

- **Table format**: exact full 64 KB (~64 M10K), exact row dictionary
  if real tables have duplicate rows, approximate 16 KB, or tiny
  quantised table. Measure actual BUILD tables before choosing.
- **fb readback path**: one-word RMW first, then line cache only if
  profiling shows it is necessary.
- **Register map**: reuse `GPU_CMAP_ADDR/DATA` with a target-select bit,
  or widen the register decoder. Reuse is strongly preferred.
- **Headroom source**: if exact full-table support is required, which
  existing memory-heavy feature gets removed or moved to a separate
  bitstream variant. The current default build does not have 64 spare
  M10Ks.
- **Capability model**: which `OF_HW_GPU_*` bits advertise exact vs
  approximate translucency so BUILD can decide whether to use GPU
  translucent paths.
- **Compatibility with the per-pixel SPAN_PERSP work** (see `span.md`):
  the new BLEND stage sits between colormap apply and FB accumulator —
  *after* the persp pipeline output. The two are orthogonal and can be
  built in either order, but if persp lands first, the metadata
  carry-through (fb_addr, etc.) for BLEND will need the same shape.
