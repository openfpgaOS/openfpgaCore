# Change request: GPU ring path + DRAW_TRIANGLES wedges

## Status

**Issues 1 and 3: fixed in commit `f68ba00`** (pending Quartus rebuild
+ hardware verification).  The actual bug was an AW+W same-cycle
pipeline race in the cpu_system LSU shim, NOT in
`gpu_core.v`/`gpu_tex_cache.v` as initially hypothesized.  See
[Resolution of issues 1 and 3](#resolution-of-issues-1-and-3) below.

**Issue 2 (1×1 textures): open.**  Orthogonal to issues 1/3; lives in
`gpu_tex_cache.v`.

The original gpudemo workarounds (kick after `SET_FB`/`SET_TEXTURE`,
batch-helper substitution, 8×8 face textures) are documented inline
in `src/apps/gpudemo/main.c`.  After the bitstream rebuild, the
issue 1+3 workarounds can be removed; the 1×1-texture workaround
stays until issue 2 is fixed.

## Issue 1: Posted-write FIFO stalls without periodic kicks

### Symptom

After `of_gpu_finish()` (or any state where the GPU is idle), a
sequence like `of_gpu_bind_texture()` immediately followed by
`of_gpu_draw_triangles()` (3 verts → ~23 ring words) wedges
mid-write.  CPU stalls on a posted MMIO store somewhere in the
last ~17 of those 23 writes.

The wedge is silent: `_gpu_ring_ensure`'s ring-full detection
never fires (the ring is empty in BRAM terms), and the existing
`[gpu] ring_ensure stall:` diagnostic in `firmware/api/of_gpu.h`
doesn't print.  The user sees the app freeze with no error.

### Reproducer

`gpudemo` mode 2 before workaround.  Sequence per frame after
`prepare_fb()`:

```
of_gpu_bind_texture(...);    // 3 ring words
of_gpu_draw_triangles(...);  // 20 ring words → wedges mid-stream
```

Inserting `of_gpu_kick()` between the bind and the draw makes the
sequence complete cleanly — the GPU drains its 3 SET_TEXTURE words
during the CPU's first few ring data writes for the triangle, and
the FIFO never fills.  This proves the wedge is FIFO-depth
limited, not ring-BRAM-full or DMA-busy.

### Root cause hypothesis

The AXI register slice + posted-write FIFO added by `e07700f`
("fmax+throughput: AXI register slices on VexiiRiscv masters +
posted-write FIFO + FIXED-burst coalescer") sits between the CPU
master and the GPU's MMIO slave.  Its depth is finite (≤ ~16 entries
based on observed wedge point), and writes to `GPU_RING_DATA`
when the GPU isn't actively reading the ring don't drain at the
slave's full rate.  Several CPU writes back-to-back fill the FIFO
and the next write stalls the bus.

Once the GPU has been kicked at least once, the slave's BRAM
write-port is being read every cycle by the GPU's command FSM and
the FIFO drains continuously — but a fresh idle period after a
finish/fence creates a new "FIFO fills before draining" window.

### Proposed fix

Two paths, both worth landing:

**RTL (preferred):** make the periph-slave's wready always-high for
GPU_RING_DATA writes, even when the GPU's command FSM is idle.
The BRAM write-port (`ring_bram[ring_wr_addr] <= ring_a_wdata;` at
`gpu_core.v:285`) is independent of the GPU's read FSM — there's
no architectural reason wready should pulse low.  If the AXI
register slice / posted-write FIFO is what's pulsing wready (e.g.
because the slice's downstream-ready depends on a stale signal from
the GPU FSM), fix that.

Verification: with the fix, gpudemo mode 2 should run **without**
the per-frame `of_gpu_kick()` calls inside `draw_triangle_demo`'s
inner loop and still complete frames.  Same goes for mode 0's
`prepare_fb` — the kick should be removable.

**SDK (defensive, complementary):** add an automatic kick to
`_gpu_ring_write` every N writes (e.g. every 16) inside
`firmware/api/of_gpu.h`.  Cost: 1 extra MMIO per 16 ring writes ≈
6% overhead.  Keeps apps safe even if the RTL fix doesn't land
fully or future bus changes regress this.  Apps that need maximum
throughput can call `of_gpu_kick()` themselves and skip the
auto-kick by some flag.

## Issue 2: 1×1 textures wedge texture cache

### Symptom

`of_gpu_bind_texture()` with `width=1, height=1` followed by any
draw using that texture wedges the GPU's command processor.
Substituting an 8×8 texture (same content, repeated) makes the
draw complete cleanly.

### Reproducer

`gpudemo` mode 2 originally allocated `face_tex[8]` (one byte per
cube face) and bound 1×1 textures pointing at single bytes:

```c
of_gpu_texture_t solid_tex = {
    .addr = (uint32_t)(uintptr_t)&face_tex[f / 2],
    .width = 1, .height = 1,
};
of_gpu_bind_texture(&solid_tex);  // wedge follows
```

Switching to `face_tex_8x8[6][64]` and binding `width=8, height=8`
fixes it.  No SDK change to the bind helper itself — same opcode,
just larger dimensions.

### Root cause hypothesis

`gpu_tex_cache.v` fetches texture data in 16-byte cache lines (per
the `axi_araddr <= {6'b0, pipe_addr_a[25:4], 4'b0}` round-down at
~line 386).  A 1×1 texture pointing at `face_tex[0]` resolves to
some byte mid-line.  The cache reads the full 16-byte line for
that address — which extends past the allocated 1-byte texture
into adjacent heap data (or, depending on alignment, into
unallocated SDRAM).  The fetched bytes are *content* irrelevant
(the GPU only samples `tex[0]`), so over-read is benign for the
data, but the fetch *transaction* might wedge if the AXI master's
length parameters interact badly with the truncated address.

Alternatively: the cache's hit detect uses the texture's
width/height for some bounds check, and width=1/height=1 produces
a degenerate compare that never reports hit.

### Proposed fix

Inspect `gpu_tex_cache.v`'s line-fetch state machine and the hit
detect.  Verify both code paths handle `width=1, height=1` without
infinite wait.  Most likely fix is a single-clamp-on-zero guard,
similar to the `width=0` case that already gets translated to "no
wrap" elsewhere in the code.

Verification: revert `gpudemo`'s 8×8 face textures to the original
`face_tex[8]` 1×1 binds and confirm mode 2 still renders.  Then
remove the workaround comment in the source.

## Issue 3: per-triangle DRAW_TRIANGLES helper wedges; batch helper works

### Symptom

`of_gpu_draw_triangles(verts, 3)` and
`of_gpu_draw_triangles_batch(verts, 3)` emit byte-identical ring
contents for `N=3` (1 header word + 1 count word + 3 × 6 vertex
words = 20 words, header payload-count = 19).  But on this
bitstream, the per-triangle helper wedges before the call returns,
while the batch helper completes cleanly.

### Reproducer

`gpudemo` mode 2 before workaround used the per-triangle helper:

```c
of_gpu_draw_triangles(tri, 3);   // wedges
```

Substituting the batch helper:

```c
of_gpu_draw_triangles_batch(tri, 3);   // works
```

makes the cube render.  Both helpers route through the same
`_gpu_cmd_header(GPU_CMD_DRAW_TRIANGLES, 19)` + `_gpu_ring_write`
sequence.  No DMA in either path.

### Root cause hypothesis

The two helpers' generated ring sequences are identical for N=3,
but they may differ in **timing relative to a previous command**:

- The per-triangle helper is called inside a loop that previously
  called `of_gpu_bind_texture` (and possibly `of_gpu_kick` between).
  Inter-helper state (e.g. SDK's `_gpu_wrptr` software shadow or
  `_gpu_fence_next`) might be in a particular range that the
  bitstream's decoder mis-handles.
- Or the bitstream's CMD_DRAW_TRIANGLES decoder reads the
  *payload-count* field from the header differently in the two
  cases, even though both emit `19`.  Worth instrumenting tb_gpu
  to dump the decoded count.

This is the most mysterious of the three; the issue 1 fix may
also dissolve issue 3 if the wedge is actually FIFO-depth driven
and the per-triangle helper just happens to land on a worse
boundary.

### Proposed fix

1. Run `tb_gpu`'s existing draw-triangle test against both helpers
   with `N=3` and confirm they produce identical decoder traces.
   If they do, the bug is timing-only (issue 1 likely subsumes
   this).
2. If the decoder traces differ, capture the first-mismatched cycle
   and trace back to the helper's emission order.

## Resolution of issues 1 and 3

The CR's hypothesis for issue 1 was that `axi_periph_slave`'s
`s_axi_wready` was being gated on GPU FSM state.  **Wrong** — code
review confirmed it isn't.  The actual bug surfaced under a new
focused testbench, `tb_gpu_chain`, that wires the production CPU
write path (LSU shim → 5× `axi_register_slice` → `cpu_target_port` →
`axi_periph_slave`) without dragging VexiiRiscv in.  The test pumps
LSU stores at the cmd/rsp interface and counts `gpu_reg_wr` pulses
at the slave's MMIO output.  Legacy per_axi tests pass clean (the
slice/port/slave path itself is fine); LSU-path tests reproduced
the wedge at write 5/23 with a debug dump that showed the shim's
state stuck:

```
shim: count=4 aw_sent=1 w_sent=1 awlen=2  (FIFO full, no progress)
slave: awready=0 wready=0 bvalid=0  port_wr_state=2 (WR_W, awaiting next W)
```

Root cause: when the AW handshake (`per_awvalid && per_awready`)
and the first W handshake (`per_wvalid && per_wready`) of a new
multi-beat coalesced burst fire on the **same posedge** — which is
exactly what happens at the bind_texture (1-beat) → next-burst
(3-beat coalesced) transition — `burst_awlen` is still the OLD
value (0 from the prior single-beat) during the cycle.  It only
updates to `burst_awlen_calc` (=2) at the same posedge the W
handshake commits.  The shim's `w_is_last = (burst_w_idx ==
burst_awlen)` therefore evaluates to true on beat 0 of the new
burst, sets `lsu_w_sent`, and never drives beats 1 and 2.  The
slave waits forever for the missing W beats; B never fires; the
4-deep posted FIFO stays full; `cmd_ready` stays low; CPU stalls
on the next posted MMIO store.

Fix: introduce a wire that uses `burst_awlen_calc` when the AW
handshake is firing this cycle:

```verilog
wire eff_burst_awlen = (per_awvalid_cpu && per_awready_cpu)
                     ? burst_awlen_calc : burst_awlen;
wire w_is_last       = (burst_w_idx == eff_burst_awlen);
```

Applied in two places:

1. `src/fpga/common/lsu_axi_shim.v` — new file, extracted from the
   inline shim in `cpu_system.v` so the testbench can wire it
   without instantiating VexiiRiscv.
2. `src/fpga/common/cpu_system.v` — same fix in the inline copy so
   the production CPU port picks it up without refactoring
   cpu_system to use the new module.

Why issue 3 is subsumed: per-tri and batch helpers emit byte-
identical ring sequences for N=3, but they land different
`wr_count` snapshots in the shim FIFO.  The per-tri helper's
sequence happened to land in the AW+W coincidence window of the
race; the batch helper happened to dodge it.  This was the timing-
only divergence the CR predicted.

Diagnostic counters added to `axi_periph_slave.v` (commit `f68ba00`)
let post-fix observation continue from hardware:

| MMIO  | Counter                | Drift = symptom                       |
|-------|------------------------|---------------------------------------|
| 0xE4  | `dbg_awready_count`    | AW handshakes accepted by slave       |
| 0xE8  | `dbg_wready_count`     | W beats accepted by slave             |
| 0xEC  | `dbg_bvalid_count`     | B responses sent by slave             |
| 0xF0  | `dbg_wstall_cycles`    | cycles in S_WR_NEXT with no W beat    |

In steady state, `awready_count ≈ bvalid_count + 1` and
`wready_count ≈ awready_count + Σ(awlen+1)` per coalesced burst.
Drift between these flags a dropped beat; `wstall_cycles` climbing
without throughput flags an upstream stall.

## Acceptance criteria

After the RTL/SDK fixes land:

- `gpudemo` works without ANY of the three workarounds (revert the
  three `~/Repos/prompt-review-transluc.md` comments and confirm
  mode 2 runs).
- `tb_gpu` regression: a new test `test_idle_to_bind_to_draw` that
  emits SET_TEXTURE → DRAW_TRIANGLES from cold-idle without an
  intermediate kick, asserting the FB receives the triangle pixels
  within a bounded number of cycles.  ✅ covered by
  `tb_gpu_chain`'s `test_lsu_bind_then_draw` (LSU side) — sim repro
  before fix, passes after.
- `tb_gpu` regression: `test_1x1_texture` that binds a 1×1
  texture and draws a span/triangle using it, asserting the
  fetched texel matches the byte at the texture's base address.
  Open — issue 2.

## Estimated effort

| Issue | RTL diagnose | RTL fix | tb_gpu coverage | Bitstream rebuild + verify |
|-------|---|---|---|---|
| 1 (FIFO/wready) | 1 day | 0.5 day | 0.5 day | 0.5 day |
| 2 (1×1 texture) | 0.5 day | 0.25 day | 0.25 day | (shared) |
| 3 (per-tri DRAW_TRIANGLES) | 0.5 day (likely subsumed by 1) | — | 0.25 day | (shared) |
| **Total** | **2 days** | **0.75 day** | **1 day** | **0.5 day** |

Total: **~4-5 days** if all three turn out to need separate RTL
work; closer to **2 days** if issue 1's fix dissolves issue 3.

## Workaround source pointers

In `~/Repos/openfpgaOS-SDK/src/apps/gpudemo/main.c`:

- `prepare_fb()`: `of_gpu_kick()` after `of_gpu_set_framebuffer()` —
  remove once issue 1 is fixed.
- `draw_maze_demo()`: same kick after the per-frame
  `of_gpu_set_framebuffer()` call inside the function — same removal.
- `draw_triangle_demo()`: `face_tex_8x8[6][64]` instead of
  `face_tex[8]`, `width=8 height=8` in the bind, kicks between bind
  and draw — remove all three once issues 1+2 are fixed.
- `draw_triangle_demo()`: `of_gpu_draw_triangles_batch(tri, 3)`
  instead of `of_gpu_draw_triangles(tri, 3)` — remove once
  issue 3 is fixed (or auto-fixed by issue 1).

Each workaround has an inline comment pointing at this CR.
